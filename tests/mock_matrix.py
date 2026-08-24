"""Reusable asyncio mock of an MT-VIKI HDMI matrix (MT-HD0808 family).

Implements the command table from ``PROTOCOL.md`` / the build contract faithfully
enough to drive the vendored ``custom_components.mtviki_matrix.api`` client under
test, plus deliberate fault-injection knobs.

Nothing in this module imports Home Assistant or the integration under test, so it
can be driven from a raw ``asyncio`` client (see ``test_mock_matrix.py``).

Wire format (see PROTOCOL.md):
    * ASCII lines, CRLF terminated in both directions.
    * Whitespace-tokenised, ``tokens[0]`` dispatch.
    * No auth, no banner, no NAK -- an unknown/rejected command simply gets no reply.
    * ``BeepONOnce`` is silent by design (but is still recorded here for assertions).

Fault injection
---------------
``frame_mode``
    ``"normal"``  one TCP write per reply line (the common case)
    ``"batch"``   every reply line produced by a single inbound TCP segment is
                  concatenated into ONE write (exercises multi-line reassembly)
    ``"split"``   the whole reply blob is written in two chunks, split mid-line
                  (exercises partial-frame reassembly)
``push_sws()``        emit an unsolicited ``SWS`` line to every connected client
``push_line()``       emit an arbitrary unsolicited line
``write_raw()``       emit arbitrary bytes (no framing applied)
``drop_connections()``  hard-close every client socket (exercises reconnect)
``unsupported``       set of command keywords the device silently ignores, to
                      model the spec-only commands a real unit may not implement
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from typing import Self

DEFAULT_FIRMWARE = "01.00.00"
DEFAULT_IP = "192.168.1.186"
DEFAULT_IP_MASK = "255.255.255.0"
DEFAULT_TITLE = "MTVIKI"
DEFAULT_SERVICE_TYPE = "MEET"
DEFAULT_SERVICE_NUM = "0042"

# 512 hex chars == 256 bytes, the (unconfirmed) EDID payload encoding.
_EDID_BLOCK = "00FFFFFFFFFFFF001E6D010101010101" * 32
DEFAULT_EDID_DATA = _EDID_BLOCK[:512]


def model_for(inputs: int, outputs: int) -> str:
    """Best-guess PING reply for a given matrix size.

    The reference module only ever observed ``FHDM88LAMG`` on an 8x8 unit; the
    string is clearly model specific (``88`` == 8x8), so the mock synthesises a
    matching literal rather than pretending every unit answers the same thing.
    """
    return f"FHDM{inputs}{outputs}LAMG"


class MockMatrix:
    """Stateful asyncio TCP mock of an MT-VIKI matrix."""

    def __init__(
        self,
        inputs: int = 8,
        outputs: int = 8,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        model: str | None = None,
        firmware: str = DEFAULT_FIRMWARE,
        ip: str = DEFAULT_IP,
        ip_mask: str = DEFAULT_IP_MASK,
        keylock: bool = False,
        beep_en: bool = False,
        frame_mode: str = "normal",
        split_at: int | None = None,
        split_delay: float = 0.02,
        unsupported: Iterable[str] | None = None,
    ) -> None:
        self.inputs = inputs
        self.outputs = outputs
        self.host = host
        self._requested_port = port
        self.port = port

        # --- device state -------------------------------------------------
        self.routes: dict[int, int] = {
            out: min(out, inputs) for out in range(1, outputs + 1)
        }
        self.scenes: dict[int, dict[int, int]] = {}
        self.keylock = keylock
        self.beep_en = beep_en
        self.firmware = firmware
        self.model = model or model_for(inputs, outputs)
        self.ip = ip
        self.ip_mask = ip_mask
        self.input_hdcp: dict[int, int] = {i: 1 for i in range(1, inputs + 1)}
        self.output_hdcp: dict[int, int] = {o: 0 for o in range(1, outputs + 1)}
        self.input_edid: dict[int, int] = {i: 1 for i in range(1, inputs + 1)}
        self.edid_data: dict[int, str] = {}
        self.title = DEFAULT_TITLE
        self.service_type = DEFAULT_SERVICE_TYPE
        self.service_num = DEFAULT_SERVICE_NUM

        # --- observability ------------------------------------------------
        #: every command line received, in order, across all connections
        self.received: list[str] = []
        #: number of times a client connected
        self.connection_count = 0
        #: monotonic timestamps of each received line, index-aligned with .received
        self.received_at: list[float] = []

        # --- fault injection ----------------------------------------------
        self.frame_mode = frame_mode
        self.split_at = split_at
        self.split_delay = split_delay
        self.unsupported: set[str] = set(unsupported or ())
        #: when True, accept the TCP connection then immediately close it
        self.refuse_after_accept = False

        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> int:
        """Start listening; returns the bound port."""
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self._requested_port
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self.drop_connections()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        self._tasks.clear()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # connection handling
    # ------------------------------------------------------------------
    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connection_count += 1
        if self.refuse_after_accept:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return

        self._writers.add(writer)
        buffer = b""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buffer += chunk
                replies: list[str] = []
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    self.received.append(line)
                    self.received_at.append(asyncio.get_running_loop().time())
                    replies.extend(self.handle_command(line))
                if replies:
                    await self._write_framed(writer, replies)
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        finally:
            self._writers.discard(writer)
            with contextlib.suppress(Exception):
                writer.close()

    async def _write_framed(
        self, writer: asyncio.StreamWriter, lines: list[str]
    ) -> None:
        """Write ``lines`` honouring the configured ``frame_mode``."""
        blob = "".join(f"{line}\r\n" for line in lines).encode("ascii")
        try:
            if self.frame_mode == "batch":
                writer.write(blob)
                await writer.drain()
            elif self.frame_mode == "split":
                cut = (
                    self.split_at
                    if self.split_at is not None
                    else max(1, len(blob) // 2)
                )
                cut = max(1, min(cut, len(blob) - 1)) if len(blob) > 1 else len(blob)
                writer.write(blob[:cut])
                await writer.drain()
                await asyncio.sleep(self.split_delay)
                writer.write(blob[cut:])
                await writer.drain()
            else:  # "normal"
                for line in lines:
                    writer.write(f"{line}\r\n".encode("ascii"))
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass

    # ------------------------------------------------------------------
    # fault injection helpers
    # ------------------------------------------------------------------
    async def push_line(self, line: str) -> None:
        """Emit an unsolicited line to every connected client."""
        for writer in list(self._writers):
            await self._write_framed(writer, [line])

    async def push_sws(self) -> None:
        """Emit an unsolicited ``SWS`` push (front-panel / IR change)."""
        await self.push_line(self._sws())

    async def push_route(self, output: int, input_: int) -> None:
        """Mutate a route locally (as the front panel would) and push ``SWS``."""
        self.routes[output] = input_
        await self.push_sws()

    async def write_raw(self, data: bytes) -> None:
        """Write raw bytes to every client with no framing applied."""
        for writer in list(self._writers):
            with contextlib.suppress(Exception):
                writer.write(data)
                await writer.drain()

    async def drop_connections(self) -> None:
        """Hard-close every client connection (simulates a device/network drop)."""
        for writer in list(self._writers):
            with contextlib.suppress(Exception):
                writer.transport.abort()
            with contextlib.suppress(Exception):
                writer.close()
        self._writers.clear()
        await asyncio.sleep(0)

    @property
    def client_count(self) -> int:
        return len(self._writers)

    # ------------------------------------------------------------------
    # command table
    # ------------------------------------------------------------------
    def _sws(self) -> str:
        vals = " ".join(str(self.routes.get(o, 1)) for o in range(1, self.outputs + 1))
        return f"SWS {vals}"

    def _in_hdcp(self) -> str:
        vals = " ".join(
            str(self.input_hdcp.get(i, 0)) for i in range(1, self.inputs + 1)
        )
        return f"InPortHDCPS {vals}"

    def _out_hdcp(self) -> str:
        vals = " ".join(
            str(self.output_hdcp.get(o, 0)) for o in range(1, self.outputs + 1)
        )
        return f"OutPortHDCPS {vals}"

    def handle_command(self, line: str) -> list[str]:
        """Apply one command line, returning the reply lines (possibly empty)."""
        tokens = line.split()
        if not tokens:
            return []
        cmd, args = tokens[0], tokens[1:]

        if cmd in self.unsupported:
            return []

        # --- routing ------------------------------------------------------
        if cmd == "SW":
            if len(args) < 2:
                return []
            try:
                src = int(args[0])
                outs = [int(a) for a in args[1:]]
            except ValueError:
                return []
            if not 1 <= src <= self.inputs:
                return []
            outs = [o for o in outs if 1 <= o <= self.outputs]
            if not outs:
                return []
            for out in outs:
                self.routes[out] = src
            return [self._sws()]

        if cmd == "GetSW":
            return [self._sws()]

        # --- scenes -------------------------------------------------------
        if cmd == "SceneSave":
            scene = _as_int(args, 0)
            if scene is None:
                return []
            self.scenes[scene] = dict(self.routes)
            return ["SceneSaveOK"]

        if cmd == "SceneCall":
            scene = _as_int(args, 0)
            if scene is None:
                return []
            if scene in self.scenes:
                self.routes = dict(self.scenes[scene])
            return [self._sws()]

        # --- key lock -----------------------------------------------------
        if cmd == "SetKeyLock":
            val = _as_int(args, 0)
            if val not in (0, 1):
                return []
            self.keylock = bool(val)
            return [f"KeyLockStatus {int(self.keylock)}"]

        if cmd == "GetKeyLock":
            return [f"KeyLockStatus {int(self.keylock)}"]

        # --- beep ---------------------------------------------------------
        if cmd == "SetBeepEn":
            val = _as_int(args, 0)
            if val not in (0, 1):
                return []
            self.beep_en = bool(val)
            return [f"BeepEn {int(self.beep_en)}"]

        if cmd == "GetBeepEn":
            return [f"BeepEn {int(self.beep_en)}"]

        if cmd == "BeepONOnce":
            # Documented with an EMPTY response cell -- deliberately silent.
            # Still recorded in self.received for assertions.
            return []

        # --- identity / network -------------------------------------------
        if cmd == "GetMCUFWVer":
            return [f"MCUVer {self.firmware}"]

        if cmd == "PING":
            return [self.model]

        if cmd == "GetIP":
            return [f"IP {self.ip}"]

        if cmd == "GetIPMask":
            return [f"IPMask {self.ip_mask}"]

        # --- HDCP ---------------------------------------------------------
        if cmd == "GetInPortHDCP":
            return [self._in_hdcp()]

        if cmd == "GetOutPortHDCP":
            return [self._out_hdcp()]

        if cmd == "SetOutPortHDCP":
            out = _as_int(args, 0)
            mode = _as_int(args, 1)
            if out is None or mode is None or not 1 <= out <= self.outputs:
                return []
            self.output_hdcp[out] = mode
            # Device answers with the FULL positional list, not just the change.
            return [self._out_hdcp()]

        # --- EDID ---------------------------------------------------------
        if cmd == "SetEDID":
            port = _as_int(args, 0)
            sel = _as_int(args, 1)
            if port is None or sel is None or not 1 <= port <= self.inputs:
                return []
            self.input_edid[port] = sel
            return [f"InPortEdid {port} {sel}"]

        if cmd == "SetEDIDData":
            slot = _as_int(args, 0)
            if slot is None or len(args) < 2:
                return []
            self.edid_data[slot] = args[1]
            return ["SetEDIDData OK"]

        if cmd == "GetEDIDData":
            slot = _as_int(args, 0)
            if slot is None:
                return []
            return [f"EDIDData {slot} {self.edid_data.get(slot, DEFAULT_EDID_DATA)}"]

        # --- labels (note the load-bearing "Lable" misspelling) ------------
        if cmd == "SetTitleLable":
            self.title = " ".join(args)
            return [f"TitleLable {self.title}"]

        if cmd == "GetTitleLable":
            return [f"TitleLable {self.title}"]

        if cmd == "SetServiceType":
            self.service_type = " ".join(args)
            return [f"ServiceType {self.service_type}"]

        if cmd == "GetServiceType":
            return [f"ServiceType {self.service_type}"]

        if cmd == "SetServiceNum":
            self.service_num = " ".join(args)
            return [f"ServiceNum {self.service_num}"]

        if cmd == "GetServiceNum":
            return [f"ServiceNum {self.service_num}"]

        # Unknown command: no NAK exists in this protocol -- stay silent.
        return []

    # ------------------------------------------------------------------
    # assertion helpers
    # ------------------------------------------------------------------
    def commands(self, keyword: str | None = None) -> list[str]:
        """Received command lines, optionally filtered by first token."""
        if keyword is None:
            return list(self.received)
        return [c for c in self.received if c.split()[:1] == [keyword]]

    def count(self, keyword: str) -> int:
        return len(self.commands(keyword))

    def clear(self) -> None:
        self.received.clear()
        self.received_at.clear()

    async def wait_for(
        self, keyword: str, count: int = 1, timeout: float = 2.0
    ) -> None:
        """Wait until ``keyword`` has been received at least ``count`` times."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self.count(keyword) >= count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(
            f"timed out waiting for {count}x {keyword!r}; got {self.received!r}"
        )

    async def wait_for_connection(self, count: int = 1, timeout: float = 5.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self.connection_count >= count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(
            f"timed out waiting for {count} connection(s); got {self.connection_count}"
        )


def _as_int(args: list[str], index: int) -> int | None:
    try:
        return int(args[index])
    except (IndexError, ValueError):
        return None


async def _main() -> None:  # pragma: no cover - manual smoke helper
    """Run the mock standalone: ``python tests/mock_matrix.py [port] [size]``."""
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
    size = sys.argv[2] if len(sys.argv) > 2 else "8x8"
    ins, outs = (int(x) for x in size.split("x"))
    mock = MockMatrix(inputs=ins, outputs=outs, port=port)
    await mock.start()
    print(f"MockMatrix {size} listening on {mock.host}:{mock.port} (ctrl-c to stop)")
    try:
        await asyncio.Event().wait()  # serve until interrupted
    except (KeyboardInterrupt, asyncio.CancelledError):
        await mock.stop()


if __name__ == "__main__":  # pragma: no cover
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
