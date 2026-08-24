"""Unit tests for the vendored protocol client ``custom_components.mtviki_matrix.api``.

Everything here talks to :class:`tests.mock_matrix.MockMatrix` over a real
loopback TCP socket -- no monkeypatching of the transport -- so the framing,
reconnect and fire-and-forget behaviours are exercised for real.

The public surface under test is exactly the build contract:

    MTVikiClient, MatrixState, MTVikiError, MTVikiConnectionError, MATRIX_SIZES
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from custom_components.mtviki_matrix.api import (
    MATRIX_SIZES,
    MatrixState,
    MTVikiClient,
    MTVikiConnectionError,
    MTVikiError,
)

from .mock_matrix import MockMatrix

# Real loopback TCP is used throughout, so opt out of pytest-socket blocking.
# (`asyncio_mode = auto` in pytest.ini is what collects the async tests.)
pytestmark = [pytest.mark.usefixtures("socket_enabled")]

ON_CONNECT = ["GetSW", "GetKeyLock", "GetBeepEn"]
ONE_TIME = [
    "GetMCUFWVer",
    "PING",
    "GetIP",
    "GetIPMask",
    "GetInPortHDCP",
    "GetOutPortHDCP",
]


async def make_client(mock: MockMatrix, **kwargs) -> MTVikiClient:
    """Build a client pointed at ``mock`` with its matrix size."""
    kwargs.setdefault("inputs", mock.inputs)
    kwargs.setdefault("outputs", mock.outputs)
    kwargs.setdefault("timeout", 2.0)
    return MTVikiClient(mock.host, mock.port, **kwargs)


async def started(
    mock: MockMatrix, *, wait_discovery: bool = True, **kwargs
) -> MTVikiClient:
    """Start a client and wait until the whole on-connect sequence has landed.

    The one-time discovery block (firmware/model/IP/HDCP) is issued behind the
    three live commands and tolerates per-command timeouts, so it can still be
    in flight for a while. Tests that assert on exact TX ordering must not race
    it -- hence waiting for the last discovery command here by default.
    """
    client = await make_client(mock, **kwargs)
    await client.start()
    await mock.wait_for("GetBeepEn", timeout=10)
    if wait_discovery:
        for command in ONE_TIME:
            if command not in mock.unsupported:
                await mock.wait_for(command, timeout=30)
    await asyncio.sleep(0.15)  # let the last replies be parsed
    return client


# ======================================================================
# module surface
# ======================================================================


def test_matrix_sizes_table():
    assert MATRIX_SIZES == {
        "2x2": (2, 2),
        "4x2": (4, 2),
        "4x4": (4, 4),
        "8x8": (8, 8),
        "16x16": (16, 16),
    }


def test_exception_hierarchy():
    assert issubclass(MTVikiConnectionError, MTVikiError)
    assert issubclass(MTVikiError, Exception)


def test_state_dataclass_defaults():
    state = MatrixState()
    assert state.routes == {}
    assert state.keylock is None
    assert state.beep_en is None
    assert state.input_hdcp == {}
    assert state.output_hdcp == {}
    assert state.connected is False


# ======================================================================
# connection lifecycle
# ======================================================================


async def test_connect_runs_on_connect_sequence():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            # exact order of the three per-connect commands
            assert mock.received[:3] == ON_CONNECT
            # one-time discovery follows (order not pinned, membership is)
            assert set(ONE_TIME).issubset(set(mock.received))
            state = client.state
            assert state.connected is True
            assert state.routes == {o: o for o in range(1, 9)}
            assert state.keylock is False
            assert state.beep_en is False
            assert state.firmware == "01.00.00"
            assert state.model == "FHDM88LAMG"
            assert state.ip == "192.168.1.186"
            assert state.ip_mask == "255.255.255.0"
            assert state.input_hdcp == {i: 1 for i in range(1, 9)}
            assert state.output_hdcp == {o: 0 for o in range(1, 9)}
        finally:
            await client.stop()


async def test_async_connect_single_attempt_succeeds():
    async with MockMatrix() as mock:
        client = await make_client(mock)
        try:
            await client.async_connect()
            assert client.state.connected is True
        finally:
            await client.stop()


async def test_async_connect_raises_on_dead_port():
    """Config-flow validation path: single attempt, no retries, typed error."""
    mock = MockMatrix()
    await mock.start()
    dead_port = mock.port
    await mock.stop()
    await asyncio.sleep(0.05)

    client = MTVikiClient("127.0.0.1", dead_port, timeout=1.0)
    with pytest.raises(MTVikiConnectionError):
        await client.async_connect()
    assert client.state.connected is False
    await client.stop()


async def test_commands_raise_when_not_connected():
    mock = MockMatrix()
    await mock.start()
    dead_port = mock.port
    await mock.stop()
    client = MTVikiClient("127.0.0.1", dead_port, timeout=1.0)
    with pytest.raises(MTVikiConnectionError):
        await client.async_switch(1, 1)
    await client.stop()


async def test_clean_stop_closes_socket_and_cancels_tasks():
    async with MockMatrix() as mock:
        client = await started(mock)
        await client.stop()
        await asyncio.sleep(0.2)
        assert client.state.connected is False
        assert mock.client_count == 0
        # stop() must be idempotent and must not leave stray tasks behind
        await client.stop()
        pending = [
            t
            for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done()
        ]
        assert not [t for t in pending if "mtviki" in repr(t).lower()]


async def test_reconnect_after_drop():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            assert mock.connection_count == 1
            mock.clear()
            await mock.drop_connections()
            # backoff starts at ~1s; give it room
            await mock.wait_for_connection(2, timeout=15)
            await mock.wait_for("GetBeepEn", timeout=5)
            await asyncio.sleep(0.1)
            # the on-connect sequence is re-run after every reconnect
            assert mock.received[:3] == ON_CONNECT
            assert client.state.connected is True
        finally:
            await client.stop()


async def test_connected_flag_flips_fire_the_state_callback():
    async with MockMatrix() as mock:
        seen: list[bool] = []
        client = await make_client(mock)
        client.set_state_callback(lambda state: seen.append(state.connected))
        try:
            await client.start()
            await mock.wait_for("GetBeepEn")
            await asyncio.sleep(0.1)
            assert True in seen
        finally:
            await client.stop()
            await asyncio.sleep(0.2)
        assert False in seen


# ======================================================================
# routing
# ======================================================================


async def test_switch_single_output_echo_updates_state():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            await client.async_switch(3, 1)
            await asyncio.sleep(0.1)
            assert "SW 3 1" in mock.received
            assert client.state.routes[1] == 3
            # other outputs unchanged
            assert client.state.routes[2] == 2
        finally:
            await client.stop()


async def test_switch_multiple_outputs():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            await client.async_switch(5, [2, 4, 6])
            await asyncio.sleep(0.1)
            assert "SW 5 2 4 6" in mock.received
            assert client.state.routes[2] == 5
            assert client.state.routes[4] == 5
            assert client.state.routes[6] == 5
            assert client.state.routes[3] == 3
        finally:
            await client.stop()


async def test_switch_all_enumerates_every_output():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            await client.async_switch_all(2)
            await asyncio.sleep(0.1)
            assert "SW 2 1 2 3 4 5 6 7 8" in mock.received
            assert client.state.routes == {o: 2 for o in range(1, 9)}
        finally:
            await client.stop()


async def test_no_optimistic_update():
    """State must only ever change from a device line -- never from a send."""
    async with MockMatrix(unsupported=["SW"]) as mock:
        client = await started(mock)
        try:
            before = dict(client.state.routes)
            # A device that ignores the command sends no echo. Whether the
            # client surfaces that as an error is its own business; what matters
            # here is that local state is NOT optimistically mutated.
            with contextlib.suppress(MTVikiError):
                await client.async_switch(4, 1)
            await asyncio.sleep(0.3)
            assert "SW 4 1" in mock.received
            assert client.state.routes == before
        finally:
            await client.stop()


async def test_unsolicited_push_updates_state_and_fires_callback():
    async with MockMatrix() as mock:
        client = await started(mock)
        updates: list[MatrixState] = []
        client.set_state_callback(updates.append)
        try:
            await mock.push_route(2, 7)
            await asyncio.sleep(0.2)
            assert client.state.routes[2] == 7
            assert updates, "state callback never fired for the push"
            assert updates[-1].routes[2] == 7
        finally:
            await client.stop()


async def test_state_property_returns_a_snapshot_copy():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            snapshot = client.state
            snapshot.routes[1] = 99
            assert client.state.routes[1] != 99
        finally:
            await client.stop()


@pytest.mark.parametrize("size", list(MATRIX_SIZES))
async def test_sws_width_never_hardcoded(size):
    ins, outs = MATRIX_SIZES[size]
    async with MockMatrix(inputs=ins, outputs=outs) as mock:
        client = await started(mock)
        try:
            assert sorted(client.state.routes) == list(range(1, outs + 1))
        finally:
            await client.stop()


async def test_16x16_full_width_parse_and_switch():
    async with MockMatrix(inputs=16, outputs=16) as mock:
        client = await started(mock)
        try:
            assert len(client.state.routes) == 16
            assert len(client.state.input_hdcp) == 16
            assert len(client.state.output_hdcp) == 16
            await client.async_switch(16, 16)
            await asyncio.sleep(0.1)
            assert client.state.routes[16] == 16
            await client.async_switch_all(9)
            await asyncio.sleep(0.1)
            assert "SW 9 " + " ".join(str(i) for i in range(1, 17)) in mock.received
            assert client.state.routes == {o: 9 for o in range(1, 17)}
        finally:
            await client.stop()


# ======================================================================
# framing / reassembly
# ======================================================================


async def test_split_frame_reassembly():
    """A reply chopped across two TCP writes must parse as one line."""
    async with MockMatrix(frame_mode="split", split_at=5) as mock:
        client = await started(mock)
        try:
            assert client.state.routes == {o: o for o in range(1, 9)}
            await client.async_switch(4, 3)
            await asyncio.sleep(0.3)
            assert client.state.routes[3] == 4
        finally:
            await client.stop()


async def test_batched_lines_in_one_write():
    """Several reply lines in one TCP segment must all be parsed."""
    async with MockMatrix(frame_mode="batch") as mock:
        client = await started(mock)
        try:
            state = client.state
            assert state.routes
            assert state.keylock is False
            assert state.beep_en is False
            assert state.firmware == "01.00.00"
        finally:
            await client.stop()


async def test_lf_only_and_blank_lines_are_tolerated():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            await mock.write_raw(b"\r\n\r\nSWS 8 7 6 5 4 3 2 1\n\r\n")
            await asyncio.sleep(0.2)
            assert client.state.routes == {
                1: 8,
                2: 7,
                3: 6,
                4: 5,
                5: 4,
                6: 3,
                7: 2,
                8: 1,
            }
        finally:
            await client.stop()


async def test_unknown_and_malformed_lines_are_ignored_not_fatal():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            before = dict(client.state.routes)
            await mock.push_line("SomethingCompletelyUnknown 1 2 3")
            await mock.push_line("SWS")
            await mock.push_line("KeyLockStatus banana")
            await mock.push_line("BeepEn 7")
            await asyncio.sleep(0.2)
            assert client.state.connected is True
            assert client.state.keylock is False
            assert client.state.beep_en is False
            # a still-live client keeps parsing afterwards
            await mock.push_route(1, 5)
            await asyncio.sleep(0.2)
            assert client.state.routes[1] == 5
            assert before[1] == 1
        finally:
            await client.stop()


# ======================================================================
# scenes
# ======================================================================


async def test_scene_save_and_recall_round_trip():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            await client.async_switch_all(4)
            await asyncio.sleep(0.1)
            await client.async_scene_save(3)
            await asyncio.sleep(0.1)
            assert "SceneSave 3" in mock.received

            await client.async_switch_all(7)
            await asyncio.sleep(0.1)
            assert client.state.routes[1] == 7

            await client.async_scene_recall(3)
            await asyncio.sleep(0.2)
            assert "SceneCall 3" in mock.received
            # SceneCall self-syncs via the SWS reply
            assert client.state.routes == {o: 4 for o in range(1, 9)}
        finally:
            await client.stop()


@pytest.mark.parametrize("scene", [0, 17, -1, 100])
async def test_scene_range_validation(scene):
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            with pytest.raises((MTVikiError, ValueError)):
                await client.async_scene_save(scene)
            with pytest.raises((MTVikiError, ValueError)):
                await client.async_scene_recall(scene)
            assert not mock.commands("SceneSave")
            assert not mock.commands("SceneCall")
        finally:
            await client.stop()


# ======================================================================
# key lock / beep
# ======================================================================


async def test_keylock_set_and_echo():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            await client.async_set_keylock(True)
            await asyncio.sleep(0.1)
            assert "SetKeyLock 1" in mock.received
            assert client.state.keylock is True
            await client.async_set_keylock(False)
            await asyncio.sleep(0.1)
            assert "SetKeyLock 0" in mock.received
            assert client.state.keylock is False
        finally:
            await client.stop()


async def test_beep_enable_set_and_echo():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            await client.async_set_beep(True)
            await asyncio.sleep(0.1)
            assert "SetBeepEn 1" in mock.received
            assert client.state.beep_en is True
        finally:
            await client.stop()


async def test_beep_once_is_fire_and_forget():
    """BeepONOnce has no documented reply -- the client must not block on one."""
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            mock.clear()
            await asyncio.wait_for(client.async_beep_once(), timeout=1.0)
            await mock.wait_for("BeepONOnce")
            assert mock.commands() == ["BeepONOnce"]
        finally:
            await client.stop()


async def test_keylock_push_from_front_panel():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            await mock.push_line("KeyLockStatus 1")
            await asyncio.sleep(0.2)
            assert client.state.keylock is True
        finally:
            await client.stop()


# ======================================================================
# locate (the BeepEn-gating workaround)
# ======================================================================


async def test_locate_wraps_beeps_when_beep_en_is_false():
    """BeepEn gating of BeepONOnce is UNKNOWN, so the client enables it first.

    Expected TX order with beep_en False:
        SetBeepEn 1, BeepONOnce x N, SetBeepEn 0
    """
    async with MockMatrix(beep_en=False) as mock:
        client = await started(mock)
        try:
            assert client.state.beep_en is False
            mock.clear()
            await client.async_locate(count=4, interval=0.05)
            await asyncio.sleep(0.3)
            assert mock.commands() == [
                "SetBeepEn 1",
                "BeepONOnce",
                "BeepONOnce",
                "BeepONOnce",
                "BeepONOnce",
                "SetBeepEn 0",
            ]
            # the pre-locate beep setting is restored
            assert client.state.beep_en is False
        finally:
            await client.stop()


async def test_locate_does_not_touch_beep_en_when_already_enabled():
    async with MockMatrix(beep_en=True) as mock:
        client = await started(mock)
        try:
            assert client.state.beep_en is True
            mock.clear()
            await client.async_locate(count=3, interval=0.05)
            await asyncio.sleep(0.3)
            assert mock.commands() == ["BeepONOnce"] * 3
            assert client.state.beep_en is True
        finally:
            await client.stop()


async def test_locate_respects_the_interval():
    async with MockMatrix(beep_en=True) as mock:
        client = await started(mock)
        try:
            mock.clear()
            interval = 0.2
            count = 3
            await client.async_locate(count=count, interval=interval)
            await asyncio.sleep(0.2)
            stamps = [
                ts
                for cmd, ts in zip(mock.received, mock.received_at)
                if cmd == "BeepONOnce"
            ]
            assert len(stamps) == count
            elapsed = stamps[-1] - stamps[0]
            assert elapsed >= (count - 1) * interval * 0.8, (
                f"beeps fired too fast: {elapsed:.3f}s for {count} beeps "
                f"at {interval}s interval"
            )
        finally:
            await client.stop()


async def test_locate_default_count_and_interval():
    async with MockMatrix(beep_en=True) as mock:
        client = await started(mock)
        try:
            mock.clear()
            await client.async_locate()
            await asyncio.sleep(0.2)
            assert mock.count("BeepONOnce") == 4  # contract default
        finally:
            await client.stop()


# ======================================================================
# HDCP / EDID / labels
# ======================================================================


async def test_set_output_hdcp_parses_full_positional_reply():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            await client.async_set_output_hdcp(3, 2)
            await asyncio.sleep(0.1)
            assert "SetOutPortHDCP 3 2" in mock.received
            assert client.state.output_hdcp[3] == 2
            assert client.state.output_hdcp[1] == 0
        finally:
            await client.stop()


@pytest.mark.parametrize("mode", [-1, 4, 99])
async def test_set_output_hdcp_rejects_out_of_range_mode(mode):
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            with pytest.raises((MTVikiError, ValueError)):
                await client.async_set_output_hdcp(1, mode)
            assert not mock.commands("SetOutPortHDCP")
        finally:
            await client.stop()


async def test_set_input_edid_echo():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            await client.async_set_input_edid(2, 5)
            await asyncio.sleep(0.1)
            assert "SetEDID 2 5" in mock.received
        finally:
            await client.stop()


async def test_edid_data_round_trip():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            payload = "AB" * 256
            await client.async_set_edid_data(2, payload)
            await asyncio.sleep(0.1)
            assert f"SetEDIDData 2 {payload}" in mock.received
            data = await client.async_get_edid_data(2)
            assert data == payload
        finally:
            await client.stop()


async def test_label_setters_use_the_misspelled_keyword():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            await client.async_set_title("RACK1")
            await client.async_set_service_type("MEET")
            await client.async_set_service_num("0007")
            await asyncio.sleep(0.2)
            assert "SetTitleLable RACK1" in mock.received
            assert "SetServiceType MEET" in mock.received
            assert "SetServiceNum 0007" in mock.received
            assert client.state.title == "RACK1"
            assert client.state.service_type == "MEET"
            assert client.state.service_num == "0007"
        finally:
            await client.stop()


# ======================================================================
# refresh helpers / diagnostics
# ======================================================================


async def test_async_refresh_sends_the_three_live_commands():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            mock.clear()
            state = await client.async_refresh()
            assert isinstance(state, MatrixState)
            assert mock.commands() == ON_CONNECT
        finally:
            await client.stop()


async def test_full_refresh_tolerates_unanswered_spec_only_commands():
    """A device that ignores the spec-only commands must not break refresh."""
    async with MockMatrix(unsupported=ONE_TIME) as mock:
        client = await started(mock, timeout=0.5)
        try:
            state = await asyncio.wait_for(client.async_full_refresh(), timeout=20)
            assert state.routes
            assert state.keylock is False
            assert state.firmware is None
            assert state.model is None
            assert state.input_hdcp == {}
            assert state.output_hdcp == {}
        finally:
            await client.stop()


async def test_send_raw_returns_lines_seen_in_the_window():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            lines = await client.async_send_raw("GetSW", window=1.0)
            assert any(line.startswith("SWS ") for line in lines)
        finally:
            await client.stop()


async def test_send_raw_on_silent_command_returns_empty():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            lines = await client.async_send_raw("BeepONOnce", window=0.5)
            assert lines == []
        finally:
            await client.stop()


async def test_recent_traffic_ring_buffer():
    async with MockMatrix() as mock:
        client = await started(mock)
        try:
            traffic = client.recent_traffic()
            assert isinstance(traffic, list)
            assert len(traffic) <= 200
            assert any("TX >>>" in line and "GetSW" in line for line in traffic)
            assert any("RX <<<" in line and "SWS" in line for line in traffic)
            for _ in range(120):
                await client.async_switch(1, 1)
            await asyncio.sleep(0.5)
            assert len(client.recent_traffic()) <= 200
        finally:
            await client.stop()
