"""Tests for EVENT_ROUTE_CHANGED events fired by MTVikiCoordinator.

Requires ``pytest-homeassistant-custom-component`` -- see requirements_test.txt.
The whole module skips cleanly if the harness is unavailable so the
HA-independent tests in ``test_mock_matrix.py`` can still run.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip(
    "pytest_homeassistant_custom_component",
    reason="pytest-homeassistant-custom-component is required for coordinator tests",
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.mtviki_matrix.const import DOMAIN, EVENT_ROUTE_CHANGED
from custom_components.mtviki_matrix.coordinator import MTVikiCoordinator

from .conftest import MOCK_CONFIG, build_mock_client

pytestmark = pytest.mark.asyncio


@pytest.fixture
def coordinator(hass: HomeAssistant) -> MTVikiCoordinator:
    """A bare MTVikiCoordinator wired to a mocked client.

    Not run through hass.config_entries.async_setup -- these are unit tests
    of the coordinator's route-diffing, not a full setup/unload round trip
    (that's covered in test_config_flow.py).
    """
    entry = MockConfigEntry(domain=DOMAIN, data=dict(MOCK_CONFIG), options={})
    entry.add_to_hass(hass)
    client = build_mock_client()
    return MTVikiCoordinator(hass, entry, client)


def _push_callback(coordinator: MTVikiCoordinator):
    """Return the callback the coordinator registered with the client."""
    return coordinator.client.set_state_callback.call_args.args[0]


def _state(routes: dict[int, int]):
    """Build a MagicMock MatrixState-alike with the given routes."""
    return build_mock_client(routes=dict(routes)).state


# ======================================================================
# push path
# ======================================================================


async def test_push_route_change_fires_event(
    hass: HomeAssistant, coordinator: MTVikiCoordinator
) -> None:
    events = async_capture_events(hass, EVENT_ROUTE_CHANGED)
    push = _push_callback(coordinator)

    # Baseline sync: no previous routes to diff against.
    push(_state({1: 1, 2: 2}))
    await hass.async_block_till_done()
    assert events == []

    push(_state({1: 1, 2: 5}))
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data == {
        "entry_id": coordinator.config_entry.entry_id,
        "output": 2,
        "old_input": 2,
        "new_input": 5,
    }


async def test_no_event_on_initial_baseline_state(
    hass: HomeAssistant, coordinator: MTVikiCoordinator
) -> None:
    events = async_capture_events(hass, EVENT_ROUTE_CHANGED)
    push = _push_callback(coordinator)

    push(_state({1: 1, 2: 2, 3: 3}))
    await hass.async_block_till_done()

    assert events == []


async def test_no_event_when_state_is_identical(
    hass: HomeAssistant, coordinator: MTVikiCoordinator
) -> None:
    events = async_capture_events(hass, EVENT_ROUTE_CHANGED)
    push = _push_callback(coordinator)

    push(_state({1: 1, 2: 2}))
    await hass.async_block_till_done()
    push(_state({1: 1, 2: 2}))
    await hass.async_block_till_done()

    assert events == []


async def test_multiple_outputs_changed_fire_one_event_each(
    hass: HomeAssistant, coordinator: MTVikiCoordinator
) -> None:
    events = async_capture_events(hass, EVENT_ROUTE_CHANGED)
    push = _push_callback(coordinator)

    push(_state({1: 1, 2: 2, 3: 3}))
    await hass.async_block_till_done()
    push(_state({1: 4, 2: 4, 3: 3}))
    await hass.async_block_till_done()

    assert len(events) == 2
    by_output = {event.data["output"]: event.data for event in events}
    assert set(by_output) == {1, 2}
    assert by_output[1] == {
        "entry_id": coordinator.config_entry.entry_id,
        "output": 1,
        "old_input": 1,
        "new_input": 4,
    }
    assert by_output[2] == {
        "entry_id": coordinator.config_entry.entry_id,
        "output": 2,
        "old_input": 2,
        "new_input": 4,
    }


async def test_event_includes_device_id_when_device_exists(
    hass: HomeAssistant, coordinator: MTVikiCoordinator
) -> None:
    """device_id is added when it's already sitting in the registry."""
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=coordinator.config_entry.entry_id,
        identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
    )
    events = async_capture_events(hass, EVENT_ROUTE_CHANGED)
    push = _push_callback(coordinator)

    push(_state({1: 1}))
    await hass.async_block_till_done()
    push(_state({1: 2}))
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["device_id"] == device.id


# ======================================================================
# polling / reconnect-resync path
# ======================================================================


async def test_reconnect_resync_fires_with_last_known_old_input(
    hass: HomeAssistant, coordinator: MTVikiCoordinator
) -> None:
    """A poll after a reconnect must diff against pre-disconnect routes."""
    events = async_capture_events(hass, EVENT_ROUTE_CHANGED)
    push = _push_callback(coordinator)

    # Baseline while connected.
    push(_state({1: 1, 2: 2}))
    await hass.async_block_till_done()

    # Connection drops and the front panel re-routes output 2 while we're
    # not listening; a reconnect resync (polling refresh) reveals it.
    coordinator.client.async_refresh = AsyncMock(return_value=_state({1: 1, 2: 7}))
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data == {
        "entry_id": coordinator.config_entry.entry_id,
        "output": 2,
        "old_input": 2,
        "new_input": 7,
    }


async def test_poll_path_no_event_when_unchanged(
    hass: HomeAssistant, coordinator: MTVikiCoordinator
) -> None:
    events = async_capture_events(hass, EVENT_ROUTE_CHANGED)
    push = _push_callback(coordinator)

    push(_state({1: 1, 2: 2}))
    await hass.async_block_till_done()

    coordinator.client.async_refresh = AsyncMock(return_value=_state({1: 1, 2: 2}))
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events == []
