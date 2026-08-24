"""Vendored asyncio client for the MT-VIKI HDMI matrix ASCII-over-TCP protocol.

This module is a dependency-free (stdlib only) protocol library.  It contains
**no Home Assistant imports** so it can be unit tested standalone and reused
outside of Home Assistant.

Protocol source / credit
------------------------
The wire protocol implemented here was reverse engineered from
`bitfocus/companion-module-mt-viki-matrix <https://github.com/bitfocus/companion-module-mt-viki-matrix>`_
(module v1.1.0 by Jens Frank) and its vendored ``docs/spec.md``
("MT-HD0808 TCP Port 8080 commands").  All credit for the protocol
documentation goes to that project.

Wire format
-----------
* Plain ASCII over raw TCP.  Default port 8080.  No banner, no handshake,
  no authentication, no NAK -- *a rejected command simply produces no echo*.
* Transmit: ``f"{command}\\r\\n"``, arguments separated by single spaces, every
  port / input / output / scene index is **1-based**.
* Receive: bytes are buffered and split on ``\\n``; a trailing ``\\r`` is
  stripped and empty lines are skipped.  Replies may be batched into one TCP
  segment or split across several, so framing is always done on the buffer.
* Tokenisation uses :meth:`str.split` (whitespace-run tolerant), never a naive
  single-space split.
* The device pushes **unsolicited** lines (notably ``SWS ...``) whenever the
  routing is changed from the front panel or the IR remote, so this client is
  built as a permanent reader loop rather than a request/response pump.

Command table (``*`` = spec-only, never exercised by the reference module)::

    SW <in> <out1> [out2 ...]      -> SWS <in_for_out1> ... <in_for_outN>
    GetSW                          -> SWS ...
    SceneSave <1-16>               -> SceneSaveOK
    SceneCall <1-16>               -> SWS ...            (self-syncs)
    SetKeyLock 1|0 / GetKeyLock    -> KeyLockStatus 0|1
    SetBeepEn 1|0  / GetBeepEn     -> BeepEn 0|1         (front-panel key click)
    BeepONOnce                     -> (no reply at all)
    GetMCUFWVer*                   -> MCUVer 01.00.00
    PING*                          -> model literal, e.g. FHDM88LAMG
    GetIP* / GetIPMask*            -> IP a.b.c.d / IPMask a.b.c.d
    GetInPortHDCP*                 -> InPortHDCPS v1..vN
    GetOutPortHDCP* /
      SetOutPortHDCP <out> <mode>* -> OutPortHDCPS v1..vN (full positional list)
    SetEDID <in> <sel>*            -> InPortEdid <in> <sel>
    SetEDIDData <slot> <payload>*  -> SetEDIDData OK
    GetEDIDData <slot>*            -> EDIDData <slot> <hex...>
    SetTitleLable <s>* /
      GetTitleLable*               -> TitleLable <s>     (misspelling is load-bearing)
    SetServiceType* / GetServiceType* -> ServiceType <s> (front LCD line 1)
    SetServiceNum*  / GetServiceNum*  -> ServiceNum <s>  (front LCD line 2)

In ``SWS a b c d`` the value at position *N* is the **input** currently routed
to **output** *N*.  The number of value tokens equals the number of outputs on
the unit, so nothing in this module ever hardcodes a field count (a 16x16 unit
sends 16 values).

Output HDCP mode values: the vendor doc contradicts itself.  We adopt
``0=off, 1=HDCP1.4, 2=HDCP2.0, 3=HDCP2.2``; the alternative reading
(``0=disable, 1=enable, 2=follow-input``) is equally plausible and unverified.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Final

__all__ = [
    "MATRIX_SIZES",
    "DiscoveredMatrix",
    "MTVikiClient",
    "MTVikiConnectionError",
    "MTVikiError",
    "MatrixState",
    "async_discover",
]

_LOGGER = logging.getLogger(__name__)

#: Matrix geometries offered by the reference Companion module: ``(inputs, outputs)``.
MATRIX_SIZES: Final[dict[str, tuple[int, int]]] = {
    "2x2": (2, 2),
    "4x2": (4, 2),
    "4x4": (4, 4),
    "8x8": (8, 8),
    "16x16": (16, 16),
}

DEFAULT_PORT: Final = 8080
DEFAULT_TIMEOUT: Final = 5.0
#: Silence window that terminates a free-form (``async_send_raw``) reply.
DEFAULT_WINDOW: Final = 1.0
#: Shorter wait used for spec-only (UNVERIFIED) commands that may never answer,
#: so that a device which ignores them cannot stall the on-connect sequence.
PROBE_TIMEOUT: Final = 1.5
#: Reconnect backoff: 1, 2, 4, 8, 16, 30, 30, ... seconds.
RECONNECT_BACKOFF_START: Final = 1.0
RECONNECT_BACKOFF_MAX: Final = 30.0
#: Ring buffer depth for :meth:`MTVikiClient.recent_traffic`.
TRAFFIC_LOG_SIZE: Final = 200
#: Scene range is the reference module's convention, not a device-doc fact.
SCENE_MIN: Final = 1
SCENE_MAX: Final = 16

#: Reply keywords understood by the single inbound parser.  Used both for
#: dispatch and to recognise the PING reply (a bare model literal that is *not*
#: one of these keywords).
_KNOWN_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "SWS",
        "KeyLockStatus",
        "BeepEn",
        "MCUVer",
        "IP",
        "IPMask",
        "InPortHDCPS",
        "OutPortHDCPS",
        "InPortEdid",
        "EDIDData",
        "SetEDIDData",
        "SceneSaveOK",
        "TitleLable",
        "ServiceType",
        "ServiceNum",
    }
)

_CONN_LOST: Final = object()  # response-queue sentinel


class MTVikiError(Exception):
    """Base error raised by this module."""


class MTVikiConnectionError(MTVikiError):
    """The TCP connection could not be established, or was lost."""


@dataclass
class MatrixState:
    """Snapshot of everything the device has told us so far.

    Every field is populated *only* from lines the device actually sent -- this
    client performs **no optimistic updates**.
    """

    #: output -> input, both 1-based.  Empty until the first ``SWS`` arrives.
    routes: dict[int, int] = field(default_factory=dict)
    keylock: bool | None = None
    beep_en: bool | None = None
    firmware: str | None = None
    #: ``PING`` reply, a model-specific literal such as ``FHDM88LAMG``.
    model: str | None = None
    ip: str | None = None
    ip_mask: str | None = None
    #: input -> raw HDCP value.  Empty if the device never answered.
    input_hdcp: dict[int, int] = field(default_factory=dict)
    #: output -> raw HDCP value.  Empty if the device never answered.
    output_hdcp: dict[int, int] = field(default_factory=dict)
    title: str | None = None
    service_type: str | None = None
    service_num: str | None = None
    connected: bool = False


class MTVikiClient:
    """Asyncio client for the MT-VIKI ASCII TCP control protocol.

    A permanently running reader task buffers raw bytes, splits complete lines
    and feeds **every** line -- solicited echo or spontaneous push alike --
    through one parser that updates :class:`MatrixState`.  Lines that arrive
    while a request window is open are additionally handed to the waiting
    caller.

    ``start()`` opens the connection and keeps it alive with exponential
    backoff; ``async_connect()`` is a single attempt used for config-flow
    validation.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        inputs: int = 8,
        outputs: int = 8,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialise the client.  No I/O happens here."""
        self.host = host
        self.port = port
        self.inputs = inputs
        self.outputs = outputs
        self.timeout = timeout

        self._state = MatrixState()
        self._callback: Callable[[MatrixState], None] | None = None

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._supervisor: asyncio.Task[None] | None = None

        self._buffer = bytearray()
        self._resp_queue: asyncio.Queue[object] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._in_request = False
        self._awaiting_ping = False
        self._stopping = False
        self._static_info_done = False
        self._disconnected = asyncio.Event()
        self._disconnected.set()
        self._first_attempt: asyncio.Event = asyncio.Event()
        self._traffic: deque[str] = deque(maxlen=TRAFFIC_LOG_SIZE)

    # ------------------------------------------------------------------ misc

    @property
    def target(self) -> str:
        """``host:port``, for logging."""
        return f"{self.host}:{self.port}"

    @property
    def connected(self) -> bool:
        """True while the socket is usable."""
        return self._state.connected

    @property
    def state(self) -> MatrixState:
        """Current state snapshot (a copy -- callers may keep it around)."""
        return self._snapshot()

    def set_state_callback(self, cb: Callable[[MatrixState], None] | None) -> None:
        """Register a callback fired on *any* state change, including
        ``connected`` flips.  Called with a fresh snapshot."""
        self._callback = cb

    def recent_traffic(self) -> list[str]:
        """Last <=200 traffic lines, ``"HH:MM:SS.mmm TX >>> GetSW"`` style."""
        return list(self._traffic)

    # ------------------------------------------------------- state plumbing

    def _snapshot(self) -> MatrixState:
        return replace(
            self._state,
            routes=dict(self._state.routes),
            input_hdcp=dict(self._state.input_hdcp),
            output_hdcp=dict(self._state.output_hdcp),
        )

    def _notify(self) -> None:
        if self._callback is None:
            return
        try:
            self._callback(self._snapshot())
        except Exception:  # pragma: no cover - a bad callback must not kill us
            _LOGGER.exception("state callback raised")

    def _set_connected(self, value: bool) -> None:
        if self._state.connected == value:
            return
        self._state.connected = value
        self._notify()

    def _log_traffic(self, direction: str, text: str) -> None:
        stamp = datetime.now().astimezone().strftime("%H:%M:%S.%f")[:-3]
        arrow = {"TX": ">>>", "RX": "<<<"}.get(direction, "---")
        self._traffic.append(f"{stamp} {direction} {arrow} {text}")

    # ------------------------------------------------------------ lifecycle

    async def async_connect(self) -> None:
        """Single connection attempt.

        Raises :class:`MTVikiConnectionError` on failure.  Used by the config
        flow; it does *not* start the auto-reconnect supervisor and does not
        run the on-connect query sequence.
        """
        await self._open()

    async def start(self) -> None:
        """Connect and keep the connection alive.

        Spawns the supervisor task (exponential backoff 1, 2, 4 ... 30 s) which
        re-runs the on-connect sequence after every successful (re)connect.
        Returns once the first connection attempt has settled; it never raises
        for an unreachable device -- inspect :attr:`connected` for that.
        """
        if self._supervisor is not None and not self._supervisor.done():
            return
        self._stopping = False
        self._first_attempt = asyncio.Event()
        self._supervisor = asyncio.create_task(
            self._supervise(), name=f"mtviki-supervisor-{self.target}"
        )
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                self._first_attempt.wait(), self.timeout + PROBE_TIMEOUT
            )

    async def stop(self) -> None:
        """Cancel the supervisor and reader tasks and close the socket."""
        self._stopping = True
        self._callback_safe_stop()
        supervisor, self._supervisor = self._supervisor, None
        if supervisor is not None:
            supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await supervisor
        await self._close()
        _LOGGER.info("MT-VIKI %s: stopped", self.target)

    def _callback_safe_stop(self) -> None:
        # Unblock anything waiting on a reply so tasks can unwind promptly.
        self._resp_queue.put_nowait(_CONN_LOST)

    async def _supervise(self) -> None:
        """Connect / on-connect sequence / wait for loss / backoff, forever."""
        delay = RECONNECT_BACKOFF_START
        attempt = 0
        while not self._stopping:
            attempt += 1
            if not self.connected:
                try:
                    await self._open()
                except MTVikiConnectionError as err:
                    self._first_attempt.set()
                    if self._stopping:
                        return
                    _LOGGER.info(
                        "MT-VIKI %s: connect attempt %d failed (%s); retrying in %.0fs",
                        self.target,
                        attempt,
                        err,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, RECONNECT_BACKOFF_MAX)
                    continue
            delay = RECONNECT_BACKOFF_START
            self._first_attempt.set()
            try:
                await self._async_on_connect()
            except MTVikiError as err:
                _LOGGER.debug(
                    "MT-VIKI %s: on-connect sequence incomplete: %s", self.target, err
                )
            await self._disconnected.wait()
            if self._stopping:
                return
            _LOGGER.info(
                "MT-VIKI %s: connection lost; reconnecting in %.0fs", self.target, delay
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_BACKOFF_MAX)

    async def _open(self) -> None:
        """Open the TCP socket and start the reader task."""
        await self._close()
        _LOGGER.debug(
            "MT-VIKI %s: connecting (timeout %.1fs)", self.target, self.timeout
        )
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
        except TimeoutError as exc:
            raise MTVikiConnectionError(
                f"timed out after {self.timeout}s connecting to {self.target}"
            ) from exc
        except ConnectionRefusedError as exc:
            raise MTVikiConnectionError(f"connection refused by {self.target}") from exc
        except OSError as exc:
            raise MTVikiConnectionError(
                f"cannot connect to {self.target}: {exc}"
            ) from exc

        self._reader = reader
        self._writer = writer
        self._buffer.clear()
        self._drain_resp_queue()
        self._disconnected.clear()
        self._reader_task = asyncio.create_task(
            self._read_loop(reader), name=f"mtviki-reader-{self.target}"
        )
        self._log_traffic("--", f"connected to {self.target}")
        _LOGGER.info("MT-VIKI %s: connected", self.target)
        self._set_connected(True)

    async def _close(self) -> None:
        """Tear the socket and reader task down; safe to call repeatedly."""
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        writer, self._writer = self._writer, None
        if writer is not None:
            with contextlib.suppress(OSError, RuntimeError):
                writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            self._log_traffic("--", "disconnected")
            _LOGGER.info("MT-VIKI %s: disconnected", self.target)
        self._reader = None
        self._disconnected.set()
        self._set_connected(False)

    # --------------------------------------------------------------- reading

    def _split_lines(self, final: bool = False) -> list[str]:
        """Pop every complete ``\\r?\\n`` terminated line out of the byte buffer."""
        lines: list[str] = []
        while True:
            idx = self._buffer.find(b"\n")
            if idx < 0:
                break
            raw = bytes(self._buffer[:idx])
            del self._buffer[: idx + 1]
            lines.append(raw.decode("ascii", errors="replace").rstrip("\r"))
        if final and self._buffer:
            lines.append(
                bytes(self._buffer).decode("ascii", errors="replace").rstrip("\r")
            )
            self._buffer.clear()
        return lines

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        """Buffer inbound bytes and dispatch complete lines until the peer goes away."""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    for text in self._split_lines(final=True):
                        self._handle_line(text)
                    _LOGGER.debug("MT-VIKI %s: peer closed the connection", self.target)
                    break
                self._buffer.extend(chunk)
                for text in self._split_lines():
                    self._handle_line(text)
        except asyncio.CancelledError:
            raise
        except (OSError, asyncio.IncompleteReadError) as err:
            _LOGGER.debug("MT-VIKI %s: read error: %s", self.target, err)
        finally:
            self._set_connected(False)
            self._resp_queue.put_nowait(_CONN_LOST)
            self._disconnected.set()

    def _handle_line(self, text: str) -> None:
        """One inbound line: log it, parse it, and hand it to a waiting request."""
        if not text.strip():
            return
        self._log_traffic("RX", text)
        _LOGGER.debug("MT-VIKI %s: RX <<< %s", self.target, text)
        self._parse_line(text)
        if self._in_request:
            self._resp_queue.put_nowait(text)

    def _drain_resp_queue(self) -> None:
        while not self._resp_queue.empty():
            self._resp_queue.get_nowait()

    # ---------------------------------------------------------------- parser

    def _parse_line(self, line: str) -> None:
        """Single parser for *every* inbound line (solicited and unsolicited).

        Updates :class:`MatrixState` in place and fires the state callback if
        anything actually changed.  Unknown lines are debug-logged and ignored;
        malformed values are warned about and ignored.
        """
        tokens = line.split()
        if not tokens:
            return
        before = self._snapshot()
        keyword = tokens[0]
        args = tokens[1:]

        if keyword == "SWS":
            self._parse_positional(line, args, self._state.routes, "route")
        elif keyword == "KeyLockStatus":
            value = self._parse_flag(line, args)
            if value is not None:
                self._state.keylock = value
        elif keyword == "BeepEn":
            value = self._parse_flag(line, args)
            if value is not None:
                self._state.beep_en = value
        elif keyword == "MCUVer":
            self._state.firmware = self._parse_single(line, args)
        elif keyword == "IP":
            self._state.ip = self._parse_single(line, args)
        elif keyword == "IPMask":
            self._state.ip_mask = self._parse_single(line, args)
        elif keyword == "InPortHDCPS":
            self._parse_positional(line, args, self._state.input_hdcp, "input HDCP")
        elif keyword == "OutPortHDCPS":
            self._parse_positional(line, args, self._state.output_hdcp, "output HDCP")
        elif keyword == "TitleLable":  # vendor misspelling, load-bearing
            self._state.title = self._parse_text(line)
        elif keyword == "ServiceType":
            self._state.service_type = self._parse_text(line)
        elif keyword == "ServiceNum":
            self._state.service_num = self._parse_text(line)
        elif keyword == "InPortEdid":
            # No MatrixState field for per-input EDID (valid values are
            # undocumented); the echo is only used as the command ack.
            _LOGGER.debug("MT-VIKI %s: EDID ack: %s", self.target, line)
        elif keyword in ("EDIDData", "SetEDIDData", "SceneSaveOK"):
            _LOGGER.debug("MT-VIKI %s: ack: %s", self.target, line)
        elif (
            self._awaiting_ping and len(tokens) == 1 and keyword not in _KNOWN_KEYWORDS
        ):
            # PING answers with a bare model literal (e.g. FHDM88LAMG) which is
            # model specific -- never match on the literal, only on the context.
            self._state.model = tokens[0]
        else:
            _LOGGER.debug("MT-VIKI %s: unhandled line: %s", self.target, line)

        if self._snapshot() != before:
            self._notify()

    def _parse_flag(self, line: str, args: list[str]) -> bool | None:
        """``0``/``1`` -> bool; anything else warns and yields ``None``."""
        if len(args) != 1 or args[0] not in ("0", "1"):
            _LOGGER.warning("MT-VIKI %s: malformed boolean line: %r", self.target, line)
            return None
        return args[0] == "1"

    def _parse_single(self, line: str, args: list[str]) -> str | None:
        if len(args) != 1:
            _LOGGER.warning("MT-VIKI %s: malformed value line: %r", self.target, line)
            return None
        return args[0]

    def _parse_text(self, line: str) -> str:
        """Free-form trailing text (labels may legitimately contain spaces)."""
        parts = line.split(None, 1)
        return parts[1].strip() if len(parts) > 1 else ""

    def _parse_positional(
        self,
        line: str,
        args: list[str],
        target: dict[int, int],
        what: str,
    ) -> None:
        """Apply a positional ``keyword v1 v2 ... vN`` list.

        Position *N* (1-based, after the keyword) is the port number and the
        token is its value.  The field count is whatever the device sent -- it
        is **never** compared against the configured matrix size, so a 16x16
        unit works unchanged.
        """
        if not args:
            _LOGGER.warning(
                "MT-VIKI %s: %s line has no values: %r", self.target, what, line
            )
            return
        for index, token in enumerate(args, start=1):
            try:
                value = int(token)
            except ValueError:
                _LOGGER.warning(
                    "MT-VIKI %s: malformed %s value %r at position %d in %r",
                    self.target,
                    what,
                    token,
                    index,
                    line,
                )
                continue
            if value < 0:
                _LOGGER.warning(
                    "MT-VIKI %s: out-of-range %s value %d at position %d in %r",
                    self.target,
                    what,
                    value,
                    index,
                    line,
                )
                continue
            target[index] = value

    # --------------------------------------------------------------- writing

    def _require_writer(self) -> asyncio.StreamWriter:
        if not self.connected or self._writer is None:
            raise MTVikiConnectionError(f"not connected to {self.target}")
        return self._writer

    async def _write(self, writer: asyncio.StreamWriter, command: str) -> None:
        self._log_traffic("TX", command)
        _LOGGER.debug("MT-VIKI %s: TX >>> %s", self.target, command)
        try:
            writer.write(f"{command}\r\n".encode("ascii", errors="replace"))
            await writer.drain()
        except (OSError, RuntimeError) as exc:
            self._set_connected(False)
            raise MTVikiConnectionError(
                f"write failed on {self.target}: {exc}"
            ) from exc

    async def _request(
        self,
        command: str,
        *,
        expect: Iterable[str] | None = None,
        timeout: float | None = None,
        window: float | None = None,
        required: bool = False,
        wait: bool = True,
    ) -> list[str]:
        """Send ``command`` and optionally collect the reply lines.

        The request/response window is serialised with a lock so two concurrent
        senders can never steal each other's replies.  Note that every inbound
        line has already been through the parser by the time it lands here.

        ``expect``    reply keywords that terminate the wait immediately.
        ``required``  raise :class:`MTVikiError` when nothing matched in time
                      (used for verified state-changing commands: total silence
                      means the device rejected the command or went away).
                      Spec-only commands pass ``required=False`` and simply
                      return ``[]`` with a debug log.
        ``wait``      ``False`` for fire-and-forget (``BeepONOnce`` never replies).
        """
        expected = frozenset(expect) if expect else frozenset()
        timeout = self.timeout if timeout is None else timeout
        window = DEFAULT_WINDOW if window is None else window

        async with self._lock:
            writer = self._require_writer()
            if not wait:
                await self._write(writer, command)
                return []

            self._drain_resp_queue()
            self._in_request = True
            try:
                await self._write(writer, command)
                lines: list[str] = []
                budget = timeout
                matched = False
                while True:
                    try:
                        item = await asyncio.wait_for(self._resp_queue.get(), budget)
                    except TimeoutError:
                        break
                    if item is _CONN_LOST:
                        raise MTVikiConnectionError(
                            f"connection to {self.target} lost while awaiting"
                            f" a reply to {command!r}"
                        )
                    assert isinstance(item, str)
                    lines.append(item)
                    if expected and item.split()[0] in expected:
                        matched = True
                        break
                    budget = window
            finally:
                self._in_request = False

        if expected and not matched:
            if required:
                raise MTVikiError(
                    f"no reply to {command!r} within {timeout:.1f}s"
                    f" (expected {'/'.join(sorted(expected))})"
                )
            _LOGGER.debug(
                "MT-VIKI %s: no reply to spec-only command %r (this is tolerated)",
                self.target,
                command,
            )
        return lines

    # -------------------------------------------------------------- queries

    async def _async_on_connect(self) -> None:
        """Run after every successful (re)connect.

        ``GetSW``, ``GetKeyLock``, ``GetBeepEn`` every time (exactly the order
        the reference module uses), then -- once per client lifetime -- the
        spec-only static identity/HDCP probes, whose absence is tolerated.
        """
        await self.async_refresh()
        if self._static_info_done:
            return
        self._static_info_done = True
        for coro in (
            self._async_get_firmware,
            self._async_ping,
            self._async_get_ip,
            self._async_get_ip_mask,
            self._async_get_input_hdcp,
            self._async_get_output_hdcp,
        ):
            try:
                await coro()
            except MTVikiConnectionError:
                raise
            except MTVikiError as err:  # pragma: no cover - defensive
                _LOGGER.debug("MT-VIKI %s: %s", self.target, err)

    async def async_refresh(self) -> MatrixState:
        """``GetSW``, ``GetKeyLock``, ``GetBeepEn`` -> fresh state snapshot."""
        # GetSW is the liveness check: a device that does not answer it is not
        # speaking this protocol, so it is the one query allowed to raise.
        await self._request("GetSW", expect=("SWS",), required=True)
        await self._request("GetKeyLock", expect=("KeyLockStatus",))
        await self._request("GetBeepEn", expect=("BeepEn",))
        return self._snapshot()

    async def async_full_refresh(self) -> MatrixState:
        """Everything :meth:`async_refresh` fetches plus the spec-only extras.

        Every extra tolerates a timeout individually -- none of them has ever
        been verified against hardware.
        """
        await self.async_refresh()
        for coro in (
            self._async_get_firmware,
            self._async_ping,
            self._async_get_ip,
            self._async_get_ip_mask,
            self._async_get_input_hdcp,
            self._async_get_output_hdcp,
            self._async_get_title,
            self._async_get_service_type,
            self._async_get_service_num,
        ):
            try:
                await coro()
            except MTVikiConnectionError:
                raise
            except MTVikiError as err:  # pragma: no cover - defensive
                _LOGGER.debug("MT-VIKI %s: %s", self.target, err)
        return self._snapshot()

    async def _async_get_firmware(self) -> None:
        await self._request("GetMCUFWVer", expect=("MCUVer",), timeout=PROBE_TIMEOUT)

    async def _async_ping(self) -> None:
        """``PING`` answers with a bare model literal, not a keyword line."""
        self._awaiting_ping = True
        try:
            await self._request("PING", timeout=PROBE_TIMEOUT, window=0.2)
        finally:
            self._awaiting_ping = False

    async def _async_get_ip(self) -> None:
        await self._request("GetIP", expect=("IP",), timeout=PROBE_TIMEOUT)

    async def _async_get_ip_mask(self) -> None:
        await self._request("GetIPMask", expect=("IPMask",), timeout=PROBE_TIMEOUT)

    async def _async_get_input_hdcp(self) -> None:
        await self._request(
            "GetInPortHDCP", expect=("InPortHDCPS",), timeout=PROBE_TIMEOUT
        )

    async def _async_get_output_hdcp(self) -> None:
        await self._request(
            "GetOutPortHDCP", expect=("OutPortHDCPS",), timeout=PROBE_TIMEOUT
        )

    async def _async_get_title(self) -> None:
        await self._request(
            "GetTitleLable", expect=("TitleLable",), timeout=PROBE_TIMEOUT
        )

    async def _async_get_service_type(self) -> None:
        await self._request(
            "GetServiceType", expect=("ServiceType",), timeout=PROBE_TIMEOUT
        )

    async def _async_get_service_num(self) -> None:
        await self._request(
            "GetServiceNum", expect=("ServiceNum",), timeout=PROBE_TIMEOUT
        )

    # ------------------------------------------------------------- commands

    async def async_switch(self, input: int, outputs: int | list[int]) -> None:
        """Route ``input`` to one or more ``outputs`` (all 1-based).

        Sends ``SW <in> <out1> [out2 ...]`` and waits for the ``SWS`` echo,
        which is what actually updates :class:`MatrixState`.
        """
        targets = [outputs] if isinstance(outputs, int) else list(outputs)
        if not targets:
            raise MTVikiError("async_switch requires at least one output")
        self._validate_port(input, self.inputs, "input")
        for output in targets:
            self._validate_port(output, self.outputs, "output")
        command = " ".join(["SW", str(input), *(str(o) for o in targets)])
        await self._request(command, expect=("SWS",), required=True)

    async def async_switch_all(self, input: int) -> None:
        """Route ``input`` to every output: ``SW <in> 1 2 ... N``."""
        await self.async_switch(input, list(range(1, self.outputs + 1)))

    async def async_scene_save(self, scene: int) -> None:
        """``SceneSave <n>`` -> ``SceneSaveOK``."""
        self._validate_scene(scene)
        await self._request(
            f"SceneSave {scene}", expect=("SceneSaveOK",), required=True
        )

    async def async_scene_recall(self, scene: int) -> None:
        """``SceneCall <n>`` -> ``SWS ...`` (the recall self-syncs the routes)."""
        self._validate_scene(scene)
        await self._request(f"SceneCall {scene}", expect=("SWS",), required=True)

    async def async_set_keylock(self, on: bool) -> None:
        """Lock / unlock the front panel keys."""
        await self._request(
            f"SetKeyLock {1 if on else 0}", expect=("KeyLockStatus",), required=True
        )

    async def async_set_beep(self, on: bool) -> None:
        """Enable / disable the front-panel key-click beep."""
        await self._request(
            f"SetBeepEn {1 if on else 0}", expect=("BeepEn",), required=True
        )

    async def async_beep_once(self) -> None:
        """``BeepONOnce`` -- documented to produce **no reply at all**.

        Pure fire-and-forget: never waits, never raises on silence.  The exact
        capitalisation matters.
        """
        await self._request("BeepONOnce", wait=False)

    async def async_locate(self, count: int = 4, interval: float = 0.35) -> None:
        """Beep the unit ``count`` times to physically identify it.

        Whether ``BeepONOnce`` is gated by the ``BeepEn`` setting is
        **UNDOCUMENTED and UNVERIFIED** -- neither the vendor spec nor the
        reference Companion module says anything about it.  We assume the
        pessimistic case (that it *is* gated), so when we know the beeper is
        disabled (``state.beep_en is False``) we temporarily enable it and
        restore the original setting afterwards.  When ``beep_en`` is ``True``
        or unknown (``None``) we just fire the pattern and touch nothing.
        If the gating assumption turns out to be wrong on real hardware, the
        only cost is two harmless extra ``SetBeepEn`` round trips.
        """
        if count < 1:
            raise MTVikiError("async_locate requires count >= 1")
        restore_beep = self.state.beep_en is False
        if restore_beep:
            await self.async_set_beep(True)
        try:
            for index in range(count):
                if index:
                    await asyncio.sleep(interval)
                await self.async_beep_once()
        finally:
            if restore_beep:
                # Shielded so the restore still reaches the device even when
                # the caller (or Home Assistant shutdown) cancels us mid-pattern.
                with contextlib.suppress(MTVikiError):
                    await asyncio.shield(self.async_set_beep(False))

    async def async_set_output_hdcp(self, output: int, mode: int) -> None:
        """``SetOutPortHDCP <out> <mode>`` (UNVERIFIED).

        We adopt ``0=off, 1=HDCP1.4, 2=HDCP2.0, 3=HDCP2.2``; the vendor doc's
        other reading is ``0=disable, 1=enable, 2=follow-input``.
        """
        self._validate_port(output, self.outputs, "output")
        if mode not in (0, 1, 2, 3):
            raise MTVikiError(f"HDCP mode {mode} out of range 0-3")
        await self._request(
            f"SetOutPortHDCP {output} {mode}",
            expect=("OutPortHDCPS",),
            timeout=PROBE_TIMEOUT,
        )

    async def async_set_input_edid(self, input: int, edid: int) -> None:
        """``SetEDID <in> <sel>`` (UNVERIFIED -- valid ``sel`` values unknown)."""
        self._validate_port(input, self.inputs, "input")
        if not 1 <= edid <= 16:
            raise MTVikiError(f"EDID selection {edid} out of the accepted range 1-16")
        await self._request(
            f"SetEDID {input} {edid}", expect=("InPortEdid",), timeout=PROBE_TIMEOUT
        )

    async def async_get_edid_data(self, slot: int) -> str | None:
        """``GetEDIDData <slot>`` -> the payload, or ``None`` on silence (UNVERIFIED)."""
        lines = await self._request(
            f"GetEDIDData {slot}", expect=("EDIDData",), timeout=PROBE_TIMEOUT
        )
        for line in lines:
            tokens = line.split(None, 2)
            if tokens and tokens[0] == "EDIDData":
                return tokens[2] if len(tokens) > 2 else ""
        return None

    async def async_set_edid_data(self, slot: int, payload: str) -> None:
        """``SetEDIDData <slot> <payload>`` (UNVERIFIED).

        The spec says only "ASCII format 256byte"; 512 hex characters is the
        likely-but-unconfirmed reading, so the payload is passed through as-is.
        """
        if not payload:
            raise MTVikiError("async_set_edid_data requires a payload")
        await self._request(
            f"SetEDIDData {slot} {payload}",
            expect=("SetEDIDData",),
            timeout=PROBE_TIMEOUT,
        )

    async def async_set_title(self, s: str) -> None:
        """``SetTitleLable <s>`` (UNVERIFIED; the misspelling is load-bearing)."""
        await self._request(
            f"SetTitleLable {s}", expect=("TitleLable",), timeout=PROBE_TIMEOUT
        )

    async def async_set_service_type(self, s: str) -> None:
        """``SetServiceType <s>`` -- front LCD line 1 (UNVERIFIED)."""
        await self._request(
            f"SetServiceType {s}", expect=("ServiceType",), timeout=PROBE_TIMEOUT
        )

    async def async_set_service_num(self, s: str) -> None:
        """``SetServiceNum <s>`` -- front LCD line 2 (UNVERIFIED)."""
        await self._request(
            f"SetServiceNum {s}", expect=("ServiceNum",), timeout=PROBE_TIMEOUT
        )

    async def async_send_raw(self, cmd: str, window: float = 1.0) -> list[str]:
        """Send an arbitrary command and return whatever came back.

        Collects lines until ``window`` seconds of silence; returns ``[]`` when
        the device never answers.  Everything received still goes through the
        normal parser first.
        """
        return await self._request(cmd.strip(), window=window)

    # ------------------------------------------------------------ validation

    def _validate_port(self, value: int, limit: int, what: str) -> None:
        if not 1 <= value <= limit:
            raise MTVikiError(f"{what} {value} out of range 1-{limit}")

    def _validate_scene(self, scene: int) -> None:
        if not SCENE_MIN <= scene <= SCENE_MAX:
            raise MTVikiError(f"scene {scene} out of range {SCENE_MIN}-{SCENE_MAX}")


# ---------------------------------------------------------------------------
# Opt-in network-scan discovery
# ---------------------------------------------------------------------------
#
# This is a best-effort fingerprint used only to pre-fill the config flow's
# "scan network" step -- it is never run automatically, and it is a separate,
# lightweight, one-shot probe rather than a full :class:`MTVikiClient`. A host
# only counts as *discovered* when it answers ``GetSW`` with a well-formed
# ``SWS`` line; a ``PING`` reply alone is far too weak a signal (almost
# anything that accepts a TCP connection and echoes a line could be mistaken
# for a matrix), so ``PING`` is only ever used to enrich a *confirmed* hit
# with a model string.

#: Matches a well-formed ``SWS`` reply: the keyword followed by one or more
#: whitespace-separated integers, and nothing else.
_SWS_RE: Final = re.compile(r"^SWS(?:\s+\d+)+$")

#: Model literals observed so far look like ``FHDM<in><out><suffix>``, e.g.
#: ``FHDM88LAMG`` (8x8) or ``FHDM1616LAMG`` (16x16).
_MODEL_DIGITS_RE: Final = re.compile(r"FHDM(\d+)", re.IGNORECASE)

#: Default probe port for :func:`async_discover` (same as the protocol default).
DISCOVERY_DEFAULT_PORT: Final = DEFAULT_PORT


@dataclass
class DiscoveredMatrix:
    """One host that answered a discovery probe with a valid ``SWS`` reply."""

    host: str
    port: int
    #: Bare model literal from the ``PING`` reply, if any was seen.
    model: str | None
    #: Derived from the model string; ``None`` when it cannot be parsed
    #: conservatively (see :func:`_derive_inputs_from_model`).
    inputs: int | None
    #: The actual field count of the device's own ``SWS`` reply --
    #: authoritative, never guessed.
    outputs: int | None


def _derive_inputs_from_model(model: str | None) -> int | None:
    """Best-effort, conservative input count from a ``PING`` model literal.

    Only ever trusts an even-length digit run immediately after ``FHDM`` that
    splits cleanly in half, e.g. ``88`` -> 8x8, ``1616`` -> 16x16, ``44`` ->
    4x4, ``42`` -> 4x2 (the reported *inputs* half). Anything else -- no
    digits, an odd-length run that cannot be split evenly, or a non-numeric
    half -- yields ``None`` rather than a guess: this is only ever used to
    pre-fill a form field the user can still override.
    """
    if not model:
        return None
    match = _MODEL_DIGITS_RE.search(model)
    if not match:
        return None
    digits = match.group(1)
    if len(digits) % 2 != 0:
        return None
    half = len(digits) // 2
    try:
        return int(digits[:half])
    except ValueError:
        return None


async def _discover_one(
    host: str, port: int, connect_timeout: float, reply_window: float
) -> DiscoveredMatrix | None:
    """Probe a single host.

    Never raises: any failure (refused connection, timeout, garbled reply,
    ...) is swallowed and reported as "not discovered" (``None``).
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), connect_timeout
        )
    except Exception:  # noqa: BLE001 - a probe must never raise per-host
        return None

    model: str | None = None
    outputs: int | None = None
    try:
        try:
            writer.write(b"PING\r\n")
            writer.write(b"GetSW\r\n")
            await writer.drain()
        except (OSError, RuntimeError):
            return None

        loop = asyncio.get_running_loop()
        deadline = loop.time() + reply_window
        buffer = bytearray()
        while outputs is None:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(reader.read(4096), remaining)
            except TimeoutError:
                break
            if not chunk:
                break
            buffer.extend(chunk)
            while b"\n" in buffer:
                idx = buffer.index(b"\n")
                raw = bytes(buffer[:idx])
                del buffer[: idx + 1]
                line = raw.decode("ascii", errors="replace").strip("\r").strip()
                if not line:
                    continue
                if _SWS_RE.match(line):
                    outputs = len(line.split()) - 1
                    break
                if model is None and len(line.split()) == 1:
                    model = line
    except Exception:  # noqa: BLE001 - defensive: a probe must never raise
        return None
    finally:
        with contextlib.suppress(OSError, RuntimeError):
            writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    if outputs is None:
        # A PING reply alone (or nothing at all) is too weak a signal.
        return None
    return DiscoveredMatrix(
        host=host,
        port=port,
        model=model,
        inputs=_derive_inputs_from_model(model),
        outputs=outputs,
    )


async def async_discover(
    hosts: Iterable[str],
    port: int = DISCOVERY_DEFAULT_PORT,
    *,
    connect_timeout: float = 0.7,
    reply_window: float = 1.0,
    concurrency: int = 100,
) -> list[DiscoveredMatrix]:
    """Concurrently probe ``hosts`` for an MT-VIKI matrix listening on ``port``.

    Opt-in only: this function is never called automatically, it exists to
    back the config flow's "scan network" step. Each host gets its own TCP
    connection attempt, a ``PING`` + ``GetSW`` probe, and up to
    ``reply_window`` seconds to answer; concurrency is bounded by a semaphore
    so a large subnet cannot open hundreds of sockets at once. Failure of any
    kind for a single host never aborts the scan. Only hosts that answered
    with a well-formed ``SWS`` line are returned, sorted by host.
    """
    host_list = list(hosts)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _bounded(host: str) -> DiscoveredMatrix | None:
        async with semaphore:
            return await _discover_one(host, port, connect_timeout, reply_window)

    results = await asyncio.gather(*(_bounded(host) for host in host_list))
    discovered = [result for result in results if result is not None]
    discovered.sort(key=lambda d: d.host)
    return discovered
