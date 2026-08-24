"""Self-tests for :mod:`tests.mock_matrix`.

These drive the mock with a bare ``asyncio`` client and therefore have NO
dependency on the integration under test -- they are runnable the moment the
repository is checked out, and they are what proves the mock itself is a faithful
stand-in before any api.py assertions are trusted.
"""

from __future__ import annotations

import asyncio

import pytest

from .mock_matrix import DEFAULT_EDID_DATA, MockMatrix

# Real loopback TCP is used throughout, so opt out of pytest-socket blocking.
pytestmark = [pytest.mark.usefixtures("socket_enabled")]


class RawClient:
    """Minimal line-oriented TCP client, mirroring the api.py framing rules."""

    def __init__(self) -> None:
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._buffer = b""

    async def connect(self, host: str, port: int) -> None:
        self.reader, self.writer = await asyncio.open_connection(host, port)

    async def send(self, cmd: str) -> None:
        assert self.writer is not None
        self.writer.write(f"{cmd}\r\n".encode("ascii"))
        await self.writer.drain()

    async def send_raw(self, data: bytes) -> None:
        assert self.writer is not None
        self.writer.write(data)
        await self.writer.drain()

    async def read_line(self, timeout: float = 2.0) -> str:
        """Read one framed line; buffers bytes and splits on \\n like api.py."""
        assert self.reader is not None
        while True:
            if b"\n" in self._buffer:
                raw, self._buffer = self._buffer.split(b"\n", 1)
                line = raw.decode("utf-8").strip()
                if line:
                    return line
                continue
            chunk = await asyncio.wait_for(self.reader.read(4096), timeout)
            if not chunk:
                raise ConnectionError("connection closed by mock")
            self._buffer += chunk

    async def read_lines(self, count: int, timeout: float = 2.0) -> list[str]:
        return [await self.read_line(timeout) for _ in range(count)]

    async def ask(self, cmd: str, timeout: float = 2.0) -> str:
        await self.send(cmd)
        return await self.read_line(timeout)

    async def expect_silence(self, seconds: float = 0.25) -> None:
        assert self.reader is not None
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(self.reader.read(4096), seconds)

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass


async def _mock_and_client(**kwargs):
    mock = MockMatrix(**kwargs)
    await mock.start()
    client = RawClient()
    await client.connect(mock.host, mock.port)
    return mock, client


# ----------------------------------------------------------------------
# command table fidelity
# ----------------------------------------------------------------------


async def test_default_state_and_getsw():
    mock, client = await _mock_and_client()
    try:
        assert await client.ask("GetSW") == "SWS 1 2 3 4 5 6 7 8"
    finally:
        await client.close()
        await mock.stop()


async def test_switch_updates_table_and_echoes_full_sws():
    mock, client = await _mock_and_client()
    try:
        assert await client.ask("SW 3 1") == "SWS 3 2 3 4 5 6 7 8"
        assert mock.routes[1] == 3
        # multi-output form
        assert await client.ask("SW 5 2 4 6") == "SWS 3 5 3 5 5 5 7 8"
        # "all" form
        assert await client.ask("SW 2 1 2 3 4 5 6 7 8") == "SWS 2 2 2 2 2 2 2 2"
        assert mock.commands("SW") == ["SW 3 1", "SW 5 2 4 6", "SW 2 1 2 3 4 5 6 7 8"]
    finally:
        await client.close()
        await mock.stop()


async def test_switch_out_of_range_is_silently_ignored():
    """No NAK exists in this protocol: a rejected command yields no echo."""
    mock, client = await _mock_and_client()
    try:
        await client.send("SW 99 1")
        await client.expect_silence()
        assert mock.routes[1] == 1
    finally:
        await client.close()
        await mock.stop()


async def test_scene_save_and_call():
    mock, client = await _mock_and_client()
    try:
        await client.ask("SW 4 1 2 3 4 5 6 7 8")
        assert await client.ask("SceneSave 3") == "SceneSaveOK"
        await client.ask("SW 7 1 2 3 4 5 6 7 8")
        # SceneCall self-syncs by replying with the restored SWS
        assert await client.ask("SceneCall 3") == "SWS 4 4 4 4 4 4 4 4"
        assert mock.routes == {o: 4 for o in range(1, 9)}
        assert 3 in mock.scenes
    finally:
        await client.close()
        await mock.stop()


async def test_keylock_and_beep_state():
    mock, client = await _mock_and_client()
    try:
        assert await client.ask("GetKeyLock") == "KeyLockStatus 0"
        assert await client.ask("SetKeyLock 1") == "KeyLockStatus 1"
        assert await client.ask("GetKeyLock") == "KeyLockStatus 1"
        assert mock.keylock is True

        assert await client.ask("GetBeepEn") == "BeepEn 0"
        assert await client.ask("SetBeepEn 1") == "BeepEn 1"
        assert await client.ask("GetBeepEn") == "BeepEn 1"
        assert mock.beep_en is True
    finally:
        await client.close()
        await mock.stop()


async def test_beep_once_is_silent_but_recorded():
    mock, client = await _mock_and_client()
    try:
        for _ in range(4):
            await client.send("BeepONOnce")
        await client.expect_silence()
        assert mock.count("BeepONOnce") == 4
    finally:
        await client.close()
        await mock.stop()


async def test_identity_and_network_replies():
    mock, client = await _mock_and_client()
    try:
        assert await client.ask("GetMCUFWVer") == "MCUVer 01.00.00"
        assert await client.ask("PING") == "FHDM88LAMG"
        assert await client.ask("GetIP") == "IP 192.168.1.186"
        assert await client.ask("GetIPMask") == "IPMask 255.255.255.0"
    finally:
        await client.close()
        await mock.stop()


async def test_hdcp_full_positional_list():
    mock, client = await _mock_and_client()
    try:
        assert await client.ask("GetInPortHDCP") == "InPortHDCPS 1 1 1 1 1 1 1 1"
        assert await client.ask("GetOutPortHDCP") == "OutPortHDCPS 0 0 0 0 0 0 0 0"
        # The reply is the FULL list, not just the changed port.
        assert await client.ask("SetOutPortHDCP 3 2") == "OutPortHDCPS 0 0 2 0 0 0 0 0"
        assert mock.output_hdcp[3] == 2
    finally:
        await client.close()
        await mock.stop()


async def test_edid_commands():
    mock, client = await _mock_and_client()
    try:
        assert await client.ask("SetEDID 2 5") == "InPortEdid 2 5"
        assert mock.input_edid[2] == 5
        reply = await client.ask("GetEDIDData 1")
        assert reply == f"EDIDData 1 {DEFAULT_EDID_DATA}"
        assert len(reply.split()[2]) == 512
        assert await client.ask("SetEDIDData 2 " + "AB" * 256) == "SetEDIDData OK"
        assert mock.edid_data[2] == "AB" * 256
        assert await client.ask("GetEDIDData 2") == "EDIDData 2 " + "AB" * 256
    finally:
        await client.close()
        await mock.stop()


async def test_label_commands_keep_the_misspelling():
    mock, client = await _mock_and_client()
    try:
        assert await client.ask("SetTitleLable RACK1") == "TitleLable RACK1"
        assert await client.ask("GetTitleLable") == "TitleLable RACK1"
        assert await client.ask("SetServiceType MEET") == "ServiceType MEET"
        assert await client.ask("SetServiceNum 0007") == "ServiceNum 0007"
        assert await client.ask("GetServiceType") == "ServiceType MEET"
        assert await client.ask("GetServiceNum") == "ServiceNum 0007"
    finally:
        await client.close()
        await mock.stop()


async def test_unknown_command_gets_no_reply():
    mock, client = await _mock_and_client()
    try:
        await client.send("NoSuchCommand 1 2 3")
        await client.expect_silence()
        assert "NoSuchCommand 1 2 3" in mock.received
    finally:
        await client.close()
        await mock.stop()


async def test_unsupported_set_models_a_device_that_ignores_spec_only_commands():
    mock, client = await _mock_and_client(unsupported=["PING", "GetMCUFWVer"])
    try:
        await client.send("PING")
        await client.send("GetMCUFWVer")
        await client.expect_silence()
        # still answers the verified commands
        assert await client.ask("GetSW") == "SWS 1 2 3 4 5 6 7 8"
    finally:
        await client.close()
        await mock.stop()


# ----------------------------------------------------------------------
# matrix sizes
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected_outputs"),
    [((2, 2), 2), ((4, 2), 2), ((4, 4), 4), ((8, 8), 8), ((16, 16), 16)],
)
async def test_sws_width_tracks_output_count(size, expected_outputs):
    ins, outs = size
    mock, client = await _mock_and_client(inputs=ins, outputs=outs)
    try:
        reply = await client.ask("GetSW")
        tokens = reply.split()
        assert tokens[0] == "SWS"
        assert len(tokens) - 1 == expected_outputs
    finally:
        await client.close()
        await mock.stop()


async def test_16x16_routing_and_hdcp_widths():
    mock, client = await _mock_and_client(inputs=16, outputs=16)
    try:
        reply = await client.ask("SW 16 16")
        assert reply.split()[-1] == "16"
        assert len(reply.split()) == 17
        assert len((await client.ask("GetInPortHDCP")).split()) == 17
        assert len((await client.ask("GetOutPortHDCP")).split()) == 17
    finally:
        await client.close()
        await mock.stop()


# ----------------------------------------------------------------------
# fault injection
# ----------------------------------------------------------------------


async def test_split_frame_reassembly():
    """A reply arriving in two TCP writes must still reassemble into one line."""
    mock, client = await _mock_and_client(frame_mode="split", split_at=4)
    try:
        assert await client.ask("GetSW") == "SWS 1 2 3 4 5 6 7 8"
    finally:
        await client.close()
        await mock.stop()


async def test_batched_lines_in_one_write():
    """Three commands in one TCP segment -> three reply lines in ONE write."""
    mock, client = await _mock_and_client(frame_mode="batch")
    try:
        await client.send_raw(b"GetSW\r\nGetKeyLock\r\nGetBeepEn\r\n")
        lines = await client.read_lines(3)
        assert lines == ["SWS 1 2 3 4 5 6 7 8", "KeyLockStatus 0", "BeepEn 0"]
        assert mock.received == ["GetSW", "GetKeyLock", "GetBeepEn"]
    finally:
        await client.close()
        await mock.stop()


async def test_unsolicited_push():
    mock, client = await _mock_and_client()
    try:
        await client.ask("GetSW")
        await mock.push_route(2, 7)
        assert await client.read_line() == "SWS 1 7 3 4 5 6 7 8"
    finally:
        await client.close()
        await mock.stop()


async def test_push_arbitrary_line():
    mock, client = await _mock_and_client()
    try:
        await client.ask("GetSW")
        await mock.push_line("KeyLockStatus 1")
        assert await client.read_line() == "KeyLockStatus 1"
    finally:
        await client.close()
        await mock.stop()


async def test_drop_connection_and_reconnect():
    mock, client = await _mock_and_client()
    try:
        await client.ask("GetSW")
        assert mock.client_count == 1
        await mock.drop_connections()
        with pytest.raises((ConnectionError, ConnectionResetError)):
            await client.read_line(timeout=1.0)
        await client.close()

        # the mock is still listening: a fresh client connects and is served
        client = RawClient()
        await client.connect(mock.host, mock.port)
        assert await client.ask("GetSW") == "SWS 1 2 3 4 5 6 7 8"
        assert mock.connection_count == 2
    finally:
        await client.close()
        await mock.stop()


async def test_stopped_mock_refuses_connections():
    mock = MockMatrix()
    await mock.start()
    port = mock.port
    await mock.stop()
    with pytest.raises(OSError):
        await asyncio.wait_for(asyncio.open_connection(mock.host, port), 2.0)


async def test_wait_for_helper_times_out_with_diagnostic():
    mock, client = await _mock_and_client()
    try:
        await client.send("GetSW")
        await mock.wait_for("GetSW", timeout=2.0)
        with pytest.raises(AssertionError):
            await mock.wait_for("PING", timeout=0.2)
    finally:
        await client.close()
        await mock.stop()
