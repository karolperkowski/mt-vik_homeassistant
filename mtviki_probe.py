#!/usr/bin/env python3
"""mtviki_probe.py -- asyncio TCP probe / REPL for MT-VIKI HDMI matrix switches.

A dependency-free (stdlib only) command line tool used to validate the documented
MT-VIKI ASCII-over-TCP control protocol against real hardware before the logic is
lifted into a Home Assistant integration.  The :class:`MTVikiClient` class in this
module is deliberately self-contained and free of any Home Assistant imports so it
can later be moved verbatim into an ``api.py``.

Protocol summary (plain ASCII, raw TCP, default port 8080, default IP 192.168.1.200).
Commands are terminated with ``\\r\\n``; replies are newline delimited and may be
batched into a single TCP segment or split across several, so the reader always
buffers bytes and splits on ``\\r?\\n``.  The device also pushes unsolicited lines
(for example ``SWS 1 2 3 4`` when routing is changed from the front panel).

    | Function                  | Send                                | Reply                      |
    |---------------------------|-------------------------------------|----------------------------|
    | Switch input to output(s) | SW Inport Outport1 [Outport2 ...]   | SWS 1 2 3 4                |
    | Query current routing     | GetSW                               | SWS 1 2 3 4                |
    | Firmware version          | GetMCUFWVer                         | MCUVer 01.00.00            |
    | Title label               | SetTitleLable xxxxx / GetTitleLable | TitleLable xxxxx           |
    | Service type (LCD line 1) | SetServiceType xxxx / GetServiceType| ServiceType xxxx           |
    | Service num  (LCD line 2) | SetServiceNum xxxx / GetServiceNum  | ServiceNum xxxx            |
    | Set EDID for input        | SetEDID Inport EdidSelect           | InPortEdid Inport EdidSel  |
    | Key lock                  | SetKeyLock 1|0 / GetKeyLock         | KeyLockStatus 1            |
    | IP / mask                 | GetIP / GetIPMask                   | IP ... / IPMask ...        |
    | Input HDCP                | GetInPortHDCP                       | InPortHDCPS 1 0 1 1        |
    | Output HDCP               | GetOutPortHDCP / SetOutPortHDCP n s | OutPortHDCPS 0 1 2 2       |
    | EDID raw data             | SetEDIDData x <256B hex> / GetEDIDData x | SetEDIDData OK / EDIDData y xx |
    | Save scene                | SceneSave x                         | SceneSaveOK                |
    | Recall scene              | SceneCall x                         | SWS 1 2 3 4                |
    | Ping / identify           | PING                                | model id, e.g. FHDM88LAMG  |
    | Beep enable               | GetBeepEn / SetBeepEn 1|0           | BeepEn 0|1                 |
    | Beep once                 | BeepONOnce                          | (no reply)                 |

In ``SWS a b c d`` the value at position N is the input currently routed to output N.

Usage examples
--------------

    # Interactive REPL (default mode) -- type raw protocol commands
    python3 mtviki_probe.py --host 192.168.1.200
    python3 mtviki_probe.py --host 192.168.1.200 repl --log session.txt

    # Safe read-only sweep of every documented query command
    python3 mtviki_probe.py --host 192.168.1.200 probe
    python3 mtviki_probe.py --host 192.168.1.200 probe --md PROTOCOL_VALIDATION.md

    # Find which TCP port speaks the ASCII protocol
    python3 mtviki_probe.py --host 192.168.1.200 scan

REPL helpers: ``/help`` (protocol cheat sheet), ``/log`` (dump traffic log),
``/quit`` (exit; Ctrl-D also works, Ctrl-C returns to the prompt).

``probe`` mode is strictly read-only: it never sends ``SW``, ``Set*`` or ``Scene*``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Self

__version__ = "1.0.0"

DEFAULT_PORT = 8080
DEFAULT_HOST_HINT = "192.168.1.200"
DEFAULT_TIMEOUT = 5.0
DEFAULT_WINDOW = 1.0
SCAN_PORTS: tuple[int, ...] = (8080, 5000, 23, 4001)

# --------------------------------------------------------------------------------------
# Protocol reference -- (syntax, expected reply, description, read_only)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProtocolCommand:
    """One documented protocol command."""

    syntax: str
    reply: str
    description: str
    read_only: bool


PROTOCOL_COMMANDS: tuple[ProtocolCommand, ...] = (
    ProtocolCommand("PING", "FHDM88LAMG", "Identify device / model id", True),
    ProtocolCommand("GetMCUFWVer", "MCUVer 01.00.00", "Firmware version", True),
    ProtocolCommand(
        "GetSW", "SWS 1 2 3 4", "Query routing (pos N = input on output N)", True
    ),
    ProtocolCommand(
        "SW <in> <out> [out...]",
        "SWS 1 2 3 4",
        "Route input to one or more outputs",
        False,
    ),
    ProtocolCommand(
        "GetKeyLock", "KeyLockStatus 1", "Front panel key lock state", True
    ),
    ProtocolCommand(
        "SetKeyLock <1|0>", "KeyLockStatus 1", "Lock/unlock front panel", False
    ),
    ProtocolCommand("GetBeepEn", "BeepEn 0", "Buzzer enabled state", True),
    ProtocolCommand("SetBeepEn <1|0>", "BeepEn 1", "Enable/disable buzzer", False),
    ProtocolCommand("BeepONOnce", "(no reply)", "Beep once", False),
    ProtocolCommand("GetIP", "IP 192.168.1.186", "Device IP address", True),
    ProtocolCommand("GetIPMask", "IPMask 255.255.255.0", "Device netmask", True),
    ProtocolCommand(
        "GetInPortHDCP", "InPortHDCPS 1 0 1 1", "Per-input HDCP status", True
    ),
    ProtocolCommand(
        "GetOutPortHDCP", "OutPortHDCPS 0 1 2 2", "Per-output HDCP mode", True
    ),
    ProtocolCommand(
        "SetOutPortHDCP <out> <0|1|2|3>",
        "OutPortHDCPS 0 1 2 2",
        "Output HDCP: 0=off 1=HDCP1.4 2=HDCP2.0 3=HDCP2.2",
        False,
    ),
    ProtocolCommand("GetTitleLable", "TitleLable xxxxx", "Read title label", True),
    ProtocolCommand(
        "SetTitleLable <text>", "TitleLable xxxxx", "Write title label", False
    ),
    ProtocolCommand("GetServiceType", "ServiceType xxxx", "Read LCD line 1", True),
    ProtocolCommand(
        "SetServiceType <text>", "ServiceType xxxx", "Write LCD line 1", False
    ),
    ProtocolCommand("GetServiceNum", "ServiceNum xxxx", "Read LCD line 2", True),
    ProtocolCommand(
        "SetServiceNum <text>", "ServiceNum xxxx", "Write LCD line 2", False
    ),
    ProtocolCommand(
        "SetEDID <in> <edid_select>",
        "InPortEdid <in> <sel>",
        "Assign preset EDID to input",
        False,
    ),
    ProtocolCommand(
        "GetEDIDData <in>", "EDIDData y xxxxxx", "Read raw EDID of input", True
    ),
    ProtocolCommand(
        "SetEDIDData <in> <256-byte-hex>",
        "SetEDIDData OK",
        "Write raw EDID to input",
        False,
    ),
    ProtocolCommand(
        "SceneSave <n>", "SceneSaveOK", "Save current routing as scene n", False
    ),
    ProtocolCommand("SceneCall <n>", "SWS 1 2 3 4", "Recall scene n", False),
)

#: Read-only sweep executed by ``probe`` mode, in order.  Never contains a mutating command.
PROBE_SEQUENCE: tuple[str, ...] = (
    "PING",
    "GetMCUFWVer",
    "GetSW",
    "GetKeyLock",
    "GetBeepEn",
    "GetIP",
    "GetIPMask",
    "GetInPortHDCP",
    "GetOutPortHDCP",
    "GetTitleLable",
    "GetServiceType",
    "GetServiceNum",
    "GetEDIDData 1",
)

#: Command prefixes that mutate device state -- refused in probe mode as a safety net.
_MUTATING_PREFIXES: tuple[str, ...] = ("SW ", "SET", "SCENE", "BEEPONONCE")


# --------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------


class MTVikiError(Exception):
    """Base error for this module."""


class MTVikiConnectionError(MTVikiError):
    """Raised when the TCP connection cannot be established or is lost."""


# --------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------


@dataclass
class TrafficEntry:
    """A single timestamped line of TX or RX traffic."""

    timestamp: float
    direction: str  # "TX", "RX" or "--"
    text: str

    def format(self) -> str:
        """Render as ``12:34:56.789 TX >>> GetSW``."""
        stamp = (
            datetime.fromtimestamp(self.timestamp, tz=UTC)
            .astimezone()
            .strftime("%H:%M:%S.%f")[:-3]
        )
        arrow = {"TX": ">>>", "RX": "<<<"}.get(self.direction, "---")
        return f"{stamp} {self.direction} {arrow} {self.text}"


@dataclass
class ReceivedLine:
    """A decoded line received from the device."""

    timestamp: float
    text: str
    solicited: bool


_CONN_LOST = object()  # queue sentinel


class MTVikiClient:
    """Asyncio client for the MT-VIKI ASCII TCP control protocol.

    The client keeps a permanently running reader task that buffers raw bytes,
    splits them on ``\\r?\\n`` and timestamps every line.  Lines that arrive while a
    request window is open are returned by :meth:`send_command`; anything else is
    treated as an unsolicited push and handed to :attr:`on_push` / :attr:`push_queue`.

    Example::

        client = MTVikiClient("192.168.1.200")
        await client.connect()
        print(await client.send_command("GetSW"))   # -> ["SWS 1 2 3 4"]
        await client.close()
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        window: float = DEFAULT_WINDOW,
        log_path: str | None = None,
        on_push: Callable[[ReceivedLine], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.window = window
        self.on_push: Callable[[ReceivedLine], None] | None = on_push

        self.traffic_log: list[TrafficEntry] = []
        self.push_queue: asyncio.Queue[ReceivedLine] = asyncio.Queue()

        self._log_path = log_path
        self._log_fh = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._buffer = bytearray()
        self._resp_queue: asyncio.Queue[object] = asyncio.Queue()
        self._send_lock = asyncio.Lock()
        self._in_request = False
        self._connected = False
        self._conn_error: MTVikiError | None = None

    # -- properties ---------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """True while the socket is usable."""
        return self._connected

    @property
    def target(self) -> str:
        """``host:port`` for display."""
        return f"{self.host}:{self.port}"

    # -- logging ------------------------------------------------------------------

    def _log(self, direction: str, text: str) -> TrafficEntry:
        entry = TrafficEntry(time.time(), direction, text)
        self.traffic_log.append(entry)
        if self._log_fh is not None:
            try:
                self._log_fh.write(entry.format() + "\n")
                self._log_fh.flush()
            except OSError:  # pragma: no cover - disk problems must not kill a probe
                pass
        return entry

    def dump_log(self) -> str:
        """Return the whole in-memory traffic log as text."""
        return "\n".join(entry.format() for entry in self.traffic_log)

    # -- connection ---------------------------------------------------------------

    def _ensure_log_open(self) -> None:
        """Open the on-disk traffic log (long-lived handle, closed on disconnect)."""
        if self._log_path and self._log_fh is None:
            try:
                # The handle deliberately outlives this scope (closed on disconnect).
                self._log_fh = open(self._log_path, "a", encoding="utf-8")  # noqa: SIM115
            except OSError as exc:
                raise MTVikiError(
                    f"cannot open log file {self._log_path!r}: {exc}"
                ) from exc

    async def connect(self) -> None:
        """Open the TCP connection and start the reader task."""
        self._ensure_log_open()

        self._log("--", f"connecting to {self.target} (timeout {self.timeout}s)")
        try:
            self._reader, self._writer = await asyncio.wait_for(
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

        self._connected = True
        self._conn_error = None
        self._buffer.clear()
        self._reader_task = asyncio.create_task(
            self._reader_loop(), name="mtviki-reader"
        )
        self._log("--", f"connected to {self.target}")

    async def close(self) -> None:
        """Close the connection and stop the reader task."""
        self._connected = False
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
            self._reader_task = None
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None
        self._reader = None
        self._log("--", "disconnected")
        if self._log_fh is not None:
            with contextlib.suppress(OSError):
                self._log_fh.close()
            self._log_fh = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # -- reading ------------------------------------------------------------------

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

    def _handle_line(self, text: str) -> None:
        if not text.strip():
            return
        entry = self._log("RX", text)
        line = ReceivedLine(entry.timestamp, text, solicited=self._in_request)
        if self._in_request:
            self._resp_queue.put_nowait(line)
            return
        self.push_queue.put_nowait(line)
        if self.on_push is not None:
            try:
                self.on_push(line)
            except Exception as exc:  # noqa: BLE001 - callback must not kill reader
                self._log("--", f"push callback error: {exc!r}")

    async def _reader_loop(self) -> None:
        """Continuously buffer incoming bytes and dispatch complete lines."""
        assert self._reader is not None
        try:
            while True:
                chunk = await self._reader.read(4096)
                if not chunk:
                    for text in self._split_lines(final=True):
                        self._handle_line(text)
                    raise MTVikiConnectionError(f"{self.target} closed the connection")
                self._buffer.extend(chunk)
                for text in self._split_lines():
                    self._handle_line(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any read failure ends the session
            self._connected = False
            self._conn_error = (
                exc
                if isinstance(exc, MTVikiError)
                else MTVikiConnectionError(f"read error on {self.target}: {exc}")
            )
            self._log("--", f"connection lost: {self._conn_error}")
            self._resp_queue.put_nowait(_CONN_LOST)

    # -- writing ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if not self._connected or self._writer is None:
            raise self._conn_error or MTVikiConnectionError(
                f"not connected to {self.target}"
            )

    async def send_command(
        self,
        command: str,
        *,
        window: float | None = None,
        timeout: float | None = None,
    ) -> list[str]:
        """Send ``command`` + CRLF and collect the reply lines.

        Waits up to ``timeout`` seconds for the first reply line, then keeps
        gathering lines until ``window`` seconds of silence.  Returns an empty
        list when the device never answers (several commands, e.g. ``BeepONOnce``,
        legitimately produce no reply).
        """
        window = self.window if window is None else window
        timeout = self.timeout if timeout is None else timeout
        command = command.strip()

        async with self._send_lock:
            self._ensure_connected()
            while not self._resp_queue.empty():  # discard stragglers from a prior call
                self._resp_queue.get_nowait()

            self._in_request = True
            try:
                self._log("TX", command)
                assert self._writer is not None
                self._writer.write((command + "\r\n").encode("ascii", errors="replace"))
                await self._writer.drain()

                lines: list[str] = []
                budget = timeout
                while True:
                    try:
                        item = await asyncio.wait_for(self._resp_queue.get(), budget)
                    except TimeoutError:
                        break
                    if item is _CONN_LOST:
                        raise self._conn_error or MTVikiConnectionError(
                            "connection lost"
                        )
                    assert isinstance(item, ReceivedLine)
                    lines.append(item.text)
                    budget = window  # subsequent lines only get the silence window
                return lines
            except (ConnectionResetError, BrokenPipeError, OSError) as exc:
                self._connected = False
                raise MTVikiConnectionError(
                    f"write failed on {self.target}: {exc}"
                ) from exc
            finally:
                self._in_request = False

    async def drain_pushes(self) -> list[ReceivedLine]:
        """Pop and return every unsolicited line queued so far."""
        out: list[ReceivedLine] = []
        while not self.push_queue.empty():
            out.append(self.push_queue.get_nowait())
        return out


# --------------------------------------------------------------------------------------
# probe mode
# --------------------------------------------------------------------------------------


@dataclass
class ProbeResult:
    """Outcome of one probed command."""

    command: str
    responses: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def status(self) -> str:
        """``OK`` / ``NO RESPONSE`` / ``ERROR``."""
        if self.error:
            return "ERROR"
        return "OK" if self.responses else "NO RESPONSE"


def _is_mutating(command: str) -> bool:
    upper = command.strip().upper()
    return any(upper.startswith(prefix) for prefix in _MUTATING_PREFIXES)


async def run_probe(args: argparse.Namespace) -> int:
    """Run the safe read-only command sweep."""
    client = MTVikiClient(
        args.host,
        args.port,
        timeout=args.timeout,
        window=args.window,
        log_path=args.log,
    )
    client.on_push = lambda line: print(f"  [PUSH] {line.text}")

    print(f"MT-VIKI probe (read-only) -> {client.target}")
    print(f"started {datetime.now().astimezone().isoformat(timespec='seconds')}\n")
    try:
        await client.connect()
    except MTVikiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    results: list[ProbeResult] = []
    try:
        for command in PROBE_SEQUENCE:
            if _is_mutating(command):  # pragma: no cover - guard against future edits
                print(f"SKIPPED (mutating command in probe sequence): {command}")
                continue
            result = ProbeResult(command)
            print(f"TX >>> {command}")
            try:
                result.responses = await client.send_command(command)
            except MTVikiError as exc:
                result.error = str(exc)
                print(f"    ERROR: {exc}")
                results.append(result)
                break
            if result.responses:
                for line in result.responses:
                    print(f"RX <<< {line}")
            else:
                print(f"    NO RESPONSE ({args.timeout:.1f}s timeout)")
            results.append(result)
            print()
    finally:
        await client.close()

    _print_probe_summary(results)
    if args.md:
        _append_markdown(args.md, args.host, args.port, results)
        print(f"\nMarkdown results appended to {args.md}")
    return 0 if any(r.status == "OK" for r in results) else 1


def _print_probe_summary(results: Sequence[ProbeResult]) -> None:
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    cmd_width = max([len(r.command) for r in results] + [7])
    print(f"{'COMMAND'.ljust(cmd_width)}  {'STATUS'.ljust(11)}  RESPONSE")
    print(f"{'-' * cmd_width}  {'-' * 11}  {'-' * 30}")
    for result in results:
        if result.error:
            detail = result.error
        elif result.responses:
            detail = " | ".join(result.responses)
        else:
            detail = "-"
        if len(detail) > 60:
            detail = detail[:57] + "..."
        print(f"{result.command.ljust(cmd_width)}  {result.status.ljust(11)}  {detail}")
    ok = sum(1 for r in results if r.status == "OK")
    print(f"\n{ok}/{len(results)} commands answered.")


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|")


def _append_markdown(
    path: str, host: str, port: int, results: Sequence[ProbeResult]
) -> None:
    """Append a PROTOCOL_VALIDATION.md style results section."""
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "",
        f"## Probe run {stamp}",
        "",
        f"- Device: `{host}:{port}`",
        f"- Tool: `mtviki_probe.py` v{__version__} (read-only probe mode)",
        "",
        "| Command | Status | Raw response |",
        "| --- | --- | --- |",
    ]
    for result in results:
        if result.error:
            raw = f"_error: {_md_escape(result.error)}_"
        elif result.responses:
            raw = "<br>".join(f"`{_md_escape(line)}`" for line in result.responses)
        else:
            raw = "_no response_"
        lines.append(f"| `{_md_escape(result.command)}` | {result.status} | {raw} |")
    lines.append("")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


# --------------------------------------------------------------------------------------
# scan mode
# --------------------------------------------------------------------------------------


async def _read_raw(reader: asyncio.StreamReader, seconds: float) -> bytes:
    """Collect whatever arrives within ``seconds``."""
    deadline = time.monotonic() + seconds
    buf = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            chunk = await asyncio.wait_for(reader.read(4096), remaining)
        except TimeoutError:
            break
        if not chunk:
            break
        buf += chunk
    return buf


async def run_scan(args: argparse.Namespace) -> int:
    """Try a handful of well-known ports and see which one speaks the protocol."""
    print(f"Scanning {args.host} on ports {', '.join(str(p) for p in SCAN_PORTS)}\n")
    open_ports: list[int] = []
    for port in SCAN_PORTS:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(args.host, port), 3.0
            )
        except TimeoutError:
            print(f"port {port:>5}: FILTERED (no response within 3s)")
            continue
        except ConnectionRefusedError:
            print(f"port {port:>5}: CLOSED (connection refused)")
            continue
        except OSError as exc:
            print(f"port {port:>5}: ERROR ({exc})")
            continue

        open_ports.append(port)
        print(f"port {port:>5}: OPEN")
        try:
            banner = await _read_raw(reader, 0.5)
            if banner:
                print(f"          banner   {banner!r}")
            for command in ("PING", "GetSW"):
                writer.write((command + "\r\n").encode("ascii"))
                await writer.drain()
                data = await _read_raw(reader, 2.0)
                print(
                    f"          {command:<6} -> {data!r}"
                    if data
                    else f"          {command:<6} -> (nothing within 2s)"
                )
        except OSError as exc:
            print(f"          I/O error: {exc}")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        print()

    if not open_ports:
        print(
            "No open ports found. Check the IP address and that the device is powered on."
        )
        return 1
    print(f"Open port(s): {', '.join(str(p) for p in open_ports)}")
    return 0


# --------------------------------------------------------------------------------------
# repl mode
# --------------------------------------------------------------------------------------

REPL_BANNER = """MT-VIKI probe REPL -- type a raw protocol command and press Enter.
Helpers: /help  /log  /quit      (Ctrl-C aborts the line, Ctrl-D exits)"""

_HISTORY_FILE = os.path.expanduser("~/.mtviki_probe_history")


def _repl_help() -> str:
    lines = [
        "",
        "Protocol commands (anything you type is sent verbatim + CRLF):",
        "",
        f"  {'SEND'.ljust(34)} {'REPLY'.ljust(26)} DESCRIPTION",
        f"  {'-' * 34} {'-' * 26} {'-' * 40}",
    ]
    for cmd in PROTOCOL_COMMANDS:
        flag = " " if cmd.read_only else "!"
        lines.append(
            f" {flag}{cmd.syntax.ljust(34)} {cmd.reply.ljust(26)} {cmd.description}"
        )
    lines += [
        "",
        "  '!' marks commands that CHANGE device state.",
        "  In 'SWS a b c d' the value at position N is the input routed to output N.",
        "",
        "REPL helpers:",
        "  /help    this table",
        "  /log     dump the session traffic log",
        "  /quit    exit (Ctrl-D does the same)",
        "",
    ]
    return "\n".join(lines)


def _setup_readline() -> object | None:
    try:
        import readline
    except ImportError:
        return None
    with contextlib.suppress(OSError):
        readline.read_history_file(_HISTORY_FILE)
    readline.set_history_length(1000)
    return readline


def run_repl(args: argparse.Namespace) -> int:
    """Interactive prompt.

    The asyncio client runs in a background event loop thread so the main thread can
    use blocking :func:`input` with full readline support and sane Ctrl-C handling.
    """
    readline_mod = _setup_readline()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="mtviki-loop", daemon=True)
    thread.start()

    def submit(coro, wait: float | None = None):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(wait)

    client = MTVikiClient(
        args.host,
        args.port,
        timeout=args.timeout,
        window=args.window,
        log_path=args.log,
    )
    client.on_push = lambda line: print(
        f"\n[PUSH] {line.text}\nmtviki> ", end="", flush=True
    )

    print(REPL_BANNER)
    print(f"Connecting to {client.target} ...")
    try:
        submit(client.connect())
    except MTVikiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        loop.call_soon_threadsafe(loop.stop)
        return 2
    print(f"Connected to {client.target}. Type /help for the protocol cheat sheet.\n")

    exit_code = 0
    try:
        while True:
            try:
                raw = input("mtviki> ")
            except KeyboardInterrupt:
                print("^C  (use /quit or Ctrl-D to exit)")
                continue
            except EOFError:
                print()
                break

            command = raw.strip()
            if not command:
                continue
            if command.startswith("/"):
                helper = command.lower().split()[0]
                if helper in ("/quit", "/exit", "/q"):
                    break
                if helper in ("/help", "/h", "/?"):
                    print(_repl_help())
                elif helper == "/log":
                    dump = client.dump_log()
                    print(dump if dump else "(traffic log is empty)")
                else:
                    print(f"Unknown helper {command!r}. Try /help, /log or /quit.")
                continue

            try:
                responses = submit(client.send_command(command))
            except MTVikiError as exc:
                print(f"ERROR: {exc}")
                exit_code = 2
                break
            except Exception as exc:  # noqa: BLE001 - REPL must not crash on odd input
                print(f"ERROR: unexpected failure: {exc!r}")
                exit_code = 2
                break

            if responses:
                for line in responses:
                    print(f"  <<< {line}")
            else:
                print(f"  (no response within {args.timeout:.1f}s)")
    finally:
        with contextlib.suppress(Exception):
            submit(client.close(), 5)
        loop.call_soon_threadsafe(loop.stop)
        if readline_mod is not None:
            with contextlib.suppress(OSError):
                readline_mod.write_history_file(_HISTORY_FILE)
    print("bye.")
    return exit_code


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser (global flags work before or after the sub-command)."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--host", help=f"device IP, e.g. {DEFAULT_HOST_HINT}", default=argparse.SUPPRESS
    )
    common.add_argument(
        "--port",
        type=int,
        help=f"TCP port (default {DEFAULT_PORT})",
        default=argparse.SUPPRESS,
    )
    common.add_argument(
        "--timeout",
        type=float,
        help=f"seconds to wait for the first reply line (default {DEFAULT_TIMEOUT})",
        default=argparse.SUPPRESS,
    )
    common.add_argument(
        "--window",
        type=float,
        help=f"silence window that ends a reply (default {DEFAULT_WINDOW}s)",
        default=argparse.SUPPRESS,
    )
    common.add_argument(
        "--log",
        metavar="FILE",
        help="append the raw TX/RX traffic log to FILE",
        default=argparse.SUPPRESS,
    )

    parser = argparse.ArgumentParser(
        prog="mtviki_probe.py",
        parents=[common],
        description="Probe / REPL for MT-VIKI HDMI matrix switches (stdlib only).",
        epilog=(
            "examples:\n"
            "  mtviki_probe.py --host 192.168.1.200\n"
            "  mtviki_probe.py --host 192.168.1.200 probe --md PROTOCOL_VALIDATION.md\n"
            "  mtviki_probe.py --host 192.168.1.200 scan\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"mtviki_probe {__version__}"
    )
    # NOTE: no parser.set_defaults() here -- it mutates the shared parent actions, which
    # would make a sub-command reset a --host given before it.  Defaults are applied in
    # apply_defaults() instead, after parsing (every option uses default=SUPPRESS).

    subparsers = parser.add_subparsers(dest="mode", metavar="{repl,probe,scan}")
    subparsers.add_parser("repl", parents=[common], help="interactive prompt (default)")
    probe = subparsers.add_parser(
        "probe", parents=[common], help="automated read-only command sweep"
    )
    probe.add_argument(
        "--md",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="append a markdown results section to FILE",
    )
    subparsers.add_parser(
        "scan", parents=[common], help="find which TCP port speaks the protocol"
    )
    return parser


#: Applied after parsing because every CLI option declares ``default=SUPPRESS``.
CLI_DEFAULTS: dict[str, object] = {
    "mode": "repl",
    "host": None,
    "port": DEFAULT_PORT,
    "timeout": DEFAULT_TIMEOUT,
    "window": DEFAULT_WINDOW,
    "log": None,
    "md": None,
}


def apply_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Fill in any option the user did not supply, in any argument position."""
    for name, value in CLI_DEFAULTS.items():
        if getattr(args, name, None) is None:
            setattr(args, name, value)
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    parser = build_parser()
    args = apply_defaults(parser.parse_args(argv))
    if not args.host:
        parser.error("--host is required (e.g. --host 192.168.1.200)")
    if args.mode == "repl":
        return run_repl(args)
    runner = run_probe if args.mode == "probe" else run_scan
    try:
        return asyncio.run(runner(args))
    except KeyboardInterrupt:
        print("\ninterrupted.")
        return 130
    except MTVikiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
