"""Entity-level tests for input/scene naming and the current-scene sensor.

These build entities directly against a bare ``MTVikiCoordinator`` plus a
mocked client -- the same pattern ``tests/test_events.py`` uses for the
coordinator's route-diffing -- rather than a full platform setup through
``hass.config_entries.async_setup``. None of the code under test here
(select/media_player/button naming, or the current-scene sensor) touches
``hass.states``, so the lighter-weight construction is enough and avoids
spinning up a real TCP mock matrix just to read entity properties.

Requires ``pytest-homeassistant-custom-component`` -- see requirements_test.txt.
The whole module skips cleanly if the harness is unavailable so the
HA-independent tests in ``test_mock_matrix.py`` can still run.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip(
    "pytest_homeassistant_custom_component",
    reason="pytest-homeassistant-custom-component is required for entity tests",
)

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mtviki_matrix.button import MTVikiSceneButton
from custom_components.mtviki_matrix.const import (
    CONF_INPUT_NAMES,
    CONF_SCENE_NAMES,
    DOMAIN,
)
from custom_components.mtviki_matrix.coordinator import MTVikiCoordinator
from custom_components.mtviki_matrix.media_player import MTVikiOutputMediaPlayer
from custom_components.mtviki_matrix.select import MTVikiOutputRouteSelect
from custom_components.mtviki_matrix.sensor import MTVikiCurrentSceneSensor

from .conftest import MOCK_CONFIG, build_mock_client

pytestmark = pytest.mark.asyncio


def _coordinator(hass: HomeAssistant, *, options=None) -> MTVikiCoordinator:
    """A bare MTVikiCoordinator, seeded with a default 1:1 routing table."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=dict(MOCK_CONFIG), options=options or {}
    )
    entry.add_to_hass(hass)
    client = build_mock_client(routes={out: out for out in range(1, 9)})
    coordinator = MTVikiCoordinator(hass, entry, client)
    coordinator.async_set_updated_data(client.state)
    return coordinator


def _push_callback(coordinator: MTVikiCoordinator):
    """Return the callback the coordinator registered with the client."""
    return coordinator.client.set_state_callback.call_args.args[0]


def _state(routes: dict[int, int]):
    """Build a MagicMock MatrixState-alike with the given routes."""
    return build_mock_client(routes=dict(routes)).state


# ======================================================================
# output select: named inputs
# ======================================================================


async def test_output_select_options_use_input_names(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, options={CONF_INPUT_NAMES: {"2": "PlayStation"}})
    select = MTVikiOutputRouteSelect(coordinator, 1)
    assert select.options == [
        "Input 1",
        "PlayStation",
        "Input 3",
        "Input 4",
        "Input 5",
        "Input 6",
        "Input 7",
        "Input 8",
    ]


async def test_output_select_current_option_shows_input_name(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass, options={CONF_INPUT_NAMES: {"2": "PlayStation"}})
    # Default routing is out N -> in N, so output 2 is fed by (named) input 2.
    select = MTVikiOutputRouteSelect(coordinator, 2)
    assert select.current_option == "PlayStation"


async def test_selecting_named_input_routes_the_right_port(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass, options={CONF_INPUT_NAMES: {"2": "PlayStation"}})
    select = MTVikiOutputRouteSelect(coordinator, 5)

    await select.async_select_option("PlayStation")

    coordinator.client.async_switch.assert_awaited_once_with(2, 5)


async def test_selecting_unnamed_input_still_works(hass: HomeAssistant) -> None:
    """Inputs that were never renamed keep working through their default label."""
    coordinator = _coordinator(hass)
    select = MTVikiOutputRouteSelect(coordinator, 1)

    await select.async_select_option("Input 7")

    coordinator.client.async_switch.assert_awaited_once_with(7, 1)


async def test_selecting_unknown_option_raises(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    select = MTVikiOutputRouteSelect(coordinator, 1)

    with pytest.raises(HomeAssistantError):
        await select.async_select_option("Does Not Exist")

    coordinator.client.async_switch.assert_not_awaited()


# ======================================================================
# media player: named inputs
# ======================================================================


async def test_media_player_source_list_uses_input_names(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass, options={CONF_INPUT_NAMES: {"3": "Apple TV"}})
    player = MTVikiOutputMediaPlayer(coordinator, 1)
    assert player.source_list[2] == "Apple TV"
    assert "Input 3" not in player.source_list


async def test_media_player_source_reflects_current_route_by_name(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass, options={CONF_INPUT_NAMES: {"3": "Apple TV"}})
    player = MTVikiOutputMediaPlayer(coordinator, 3)
    assert player.source == "Apple TV"


async def test_media_player_select_source_routes_the_right_port(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass, options={CONF_INPUT_NAMES: {"3": "Apple TV"}})
    player = MTVikiOutputMediaPlayer(coordinator, 6)

    await player.async_select_source("Apple TV")

    coordinator.client.async_switch.assert_awaited_once_with(3, 6)


async def test_media_player_select_unknown_source_raises(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass)
    player = MTVikiOutputMediaPlayer(coordinator, 1)

    with pytest.raises(HomeAssistantError):
        await player.async_select_source("Does Not Exist")


# ======================================================================
# scene buttons: named scenes
# ======================================================================


async def test_scene_button_uses_configured_label(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, options={CONF_SCENE_NAMES: {"4": "Movie Night"}})
    button = MTVikiSceneButton(coordinator, 4)
    assert button.name == "Movie Night"


async def test_scene_button_defaults_to_scene_n(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    button = MTVikiSceneButton(coordinator, 4)
    assert button.name == "Scene 4"


async def test_scene_button_press_recalls_through_the_coordinator(
    hass: HomeAssistant,
) -> None:
    """The button must go through coordinator.async_recall_scene, not the
    client directly, so the current-scene sensor can track the recall."""
    coordinator = _coordinator(hass, options={CONF_SCENE_NAMES: {"4": "Movie Night"}})
    button = MTVikiSceneButton(coordinator, 4)

    await button.async_press()

    coordinator.client.async_scene_recall.assert_awaited_once_with(4)
    assert coordinator.current_scene_name == "Movie Night"


# ======================================================================
# current-scene sensor lifecycle
# ======================================================================


async def test_current_scene_sensor_reports_none_before_any_recall(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass)
    sensor = MTVikiCurrentSceneSensor(coordinator)
    assert sensor.native_value == "none"


async def test_current_scene_sensor_shows_name_after_recall(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass, options={CONF_SCENE_NAMES: {"4": "Movie Night"}})
    sensor = MTVikiCurrentSceneSensor(coordinator)

    await coordinator.async_recall_scene(4)

    assert sensor.native_value == "Movie Night"


async def test_current_scene_sensor_falls_back_to_default_scene_name(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass)
    sensor = MTVikiCurrentSceneSensor(coordinator)

    await coordinator.async_recall_scene(7)

    assert sensor.native_value == "Scene 7"


async def test_current_scene_sensor_reverts_to_none_on_diverging_route_change(
    hass: HomeAssistant,
) -> None:
    """Any routing change (front panel, IR, another select) that no longer
    matches what the recall produced clears the tracked scene."""
    coordinator = _coordinator(hass, options={CONF_SCENE_NAMES: {"4": "Movie Night"}})
    sensor = MTVikiCurrentSceneSensor(coordinator)

    await coordinator.async_recall_scene(4)
    assert sensor.native_value == "Movie Night"

    push = _push_callback(coordinator)
    push(_state({out: out for out in range(1, 9)} | {1: 9}))

    assert sensor.native_value == "none"


async def test_current_scene_sensor_survives_a_no_op_state_update(
    hass: HomeAssistant,
) -> None:
    """A pushed state with the SAME routing must not clear the tracked scene."""
    coordinator = _coordinator(hass, options={CONF_SCENE_NAMES: {"4": "Movie Night"}})
    sensor = MTVikiCurrentSceneSensor(coordinator)

    await coordinator.async_recall_scene(4)
    push = _push_callback(coordinator)
    push(_state({out: out for out in range(1, 9)}))

    assert sensor.native_value == "Movie Night"


async def test_current_scene_sensor_switches_to_new_recall(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(
        hass,
        options={CONF_SCENE_NAMES: {"4": "Movie Night", "5": "Gaming"}},
    )
    sensor = MTVikiCurrentSceneSensor(coordinator)

    await coordinator.async_recall_scene(4)
    assert sensor.native_value == "Movie Night"

    await coordinator.async_recall_scene(5)
    assert sensor.native_value == "Gaming"


async def test_current_scene_sensor_diverges_on_poll_path(
    hass: HomeAssistant,
) -> None:
    """The polling refresh path must also clear stale scene tracking."""
    coordinator = _coordinator(hass, options={CONF_SCENE_NAMES: {"4": "Movie Night"}})
    sensor = MTVikiCurrentSceneSensor(coordinator)

    await coordinator.async_recall_scene(4)
    assert sensor.native_value == "Movie Night"

    coordinator.client.async_refresh = AsyncMock(
        return_value=_state({out: out for out in range(1, 9)} | {1: 9})
    )
    await coordinator._async_update_data()

    assert sensor.native_value == "none"
