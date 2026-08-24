"""Config-flow tests for the MT-VIKI HDMI Matrix integration.

Written against the Home Assistant test conventions current at the time of
writing (``FlowResultType`` enum, ``SOURCE_USER``, ``hass.async_block_till_done()``
after every flow step, ``MockConfigEntry`` from
``pytest_homeassistant_custom_component``).

Requires ``pytest-homeassistant-custom-component`` -- see requirements_test.txt.
The whole module skips cleanly if the harness is unavailable so the
HA-independent tests in ``test_mock_matrix.py`` can still run.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip(
    "pytest_homeassistant_custom_component",
    reason="pytest-homeassistant-custom-component is required for HA flow tests",
)

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.mtviki_matrix.api import DiscoveredMatrix, MTVikiConnectionError

from .conftest import (
    DOMAIN,
    MOCK_CONFIG,
    MOCK_HOST,
    MOCK_PORT,
    build_mock_client,
)

pytestmark = pytest.mark.asyncio

CLIENT_PATH = f"custom_components.{DOMAIN}.config_flow.MTVikiClient"
DISCOVER_PATH = f"custom_components.{DOMAIN}.config_flow.async_discover"
GUESS_SUBNET_PATH = f"custom_components.{DOMAIN}.config_flow._guess_local_subnet"


async def _start_flow(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.async_block_till_done()
    return result


async def _start_manual_flow(hass: HomeAssistant):
    """Start the flow and hop through the menu into the manual step."""
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    await hass.async_block_till_done()
    return result


# ======================================================================
# user step (menu)
# ======================================================================


async def test_user_step_shows_menu(hass: HomeAssistant) -> None:
    result = await _start_flow(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    assert set(result["menu_options"]) == {"manual", "scan"}


async def test_user_step_menu_routes_to_manual(hass: HomeAssistant) -> None:
    result = await _start_manual_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] in ({}, None)


async def test_user_step_menu_routes_to_scan(hass: HomeAssistant) -> None:
    result = await _start_flow(hass)
    with patch(GUESS_SUBNET_PATH, return_value="192.168.1.0/24"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "scan"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "scan"


# ======================================================================
# manual step
# ======================================================================


async def test_manual_step_schema_defaults(hass: HomeAssistant) -> None:
    """Port defaults to 8080 and matrix size to 8x8 per the contract."""
    result = await _start_manual_flow(hass)
    schema = result["data_schema"].schema
    keys = {str(key): key for key in schema}
    assert "host" in keys
    assert "port" in keys
    assert "matrix_size" in keys
    assert keys["port"].default() == 8080
    assert keys["matrix_size"].default() == "8x8"


async def test_manual_step_creates_entry(
    hass: HomeAssistant, mock_client, mock_setup_entry
) -> None:
    result = await _start_manual_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(MOCK_CONFIG)
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == MOCK_CONFIG
    assert result["result"].unique_id == f"{MOCK_HOST}:{MOCK_PORT}"
    # validation is a single connect attempt plus a GetSW refresh
    assert mock_client.async_connect.await_count == 1
    assert mock_client.async_refresh.await_count >= 1
    # the flow must not leave a socket open
    assert mock_client.stop.await_count >= 1
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ("2x2", "2x2"),
        ("4x2", "4x2"),
        ("4x4", "4x4"),
        ("8x8", "8x8"),
        ("16x16", "16x16"),
    ],
)
async def test_all_matrix_sizes_accepted(
    hass: HomeAssistant, mock_client, mock_setup_entry, size, expected
) -> None:
    result = await _start_manual_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**MOCK_CONFIG, "matrix_size": size}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["matrix_size"] == expected


async def test_custom_port_is_stored_and_used_for_unique_id(
    hass: HomeAssistant, mock_client, mock_setup_entry
) -> None:
    result = await _start_manual_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**MOCK_CONFIG, "port": 5000}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["port"] == 5000
    assert result["result"].unique_id == f"{MOCK_HOST}:5000"


# ======================================================================
# error handling
# ======================================================================


async def test_cannot_connect(hass: HomeAssistant, mock_setup_entry) -> None:
    client = build_mock_client()
    client.async_connect.side_effect = MTVikiConnectionError("boom")
    with patch(CLIENT_PATH, return_value=client):
        result = await _start_manual_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(MOCK_CONFIG)
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_unknown_error(hass: HomeAssistant, mock_setup_entry) -> None:
    client = build_mock_client()
    client.async_connect.side_effect = RuntimeError("kaboom")
    with patch(CLIENT_PATH, return_value=client):
        result = await _start_manual_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(MOCK_CONFIG)
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_recovers_after_error(hass: HomeAssistant, mock_setup_entry) -> None:
    """The form must be re-showable and succeed on a second, good attempt."""
    failing = build_mock_client()
    failing.async_connect.side_effect = MTVikiConnectionError("boom")
    with patch(CLIENT_PATH, return_value=failing):
        result = await _start_manual_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(MOCK_CONFIG)
        )
        await hass.async_block_till_done()
    assert result["errors"] == {"base": "cannot_connect"}

    good = build_mock_client()
    with patch(CLIENT_PATH, return_value=good):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(MOCK_CONFIG)
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == MOCK_CONFIG


async def test_duplicate_host_port_aborts(
    hass: HomeAssistant, mock_client, mock_setup_entry, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)

    result = await _start_manual_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(MOCK_CONFIG)
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_same_host_different_port_is_not_a_duplicate(
    hass: HomeAssistant, mock_client, mock_setup_entry, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)

    result = await _start_manual_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**MOCK_CONFIG, "port": 5000}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


# ======================================================================
# scan / pick steps
# ======================================================================


def _discovered(
    host=MOCK_HOST, port=MOCK_PORT, model="FHDM88LAMG", inputs=8, outputs=8
):
    return DiscoveredMatrix(
        host=host, port=port, model=model, inputs=inputs, outputs=outputs
    )


async def _start_scan_flow(hass: HomeAssistant, *, subnet="192.168.1.0/24"):
    result = await _start_flow(hass)
    with patch(GUESS_SUBNET_PATH, return_value=subnet):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "scan"}
        )
        await hass.async_block_till_done()
    return result


async def test_scan_step_prefills_guessed_subnet(hass: HomeAssistant) -> None:
    result = await _start_scan_flow(hass, subnet="10.20.30.0/24")
    schema = result["data_schema"].schema
    keys = {str(key): key for key in schema}
    assert keys["network"].description == {"suggested_value": "10.20.30.0/24"}


async def test_scan_step_invalid_network(hass: HomeAssistant) -> None:
    result = await _start_scan_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"network": "not-a-network"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "scan"
    assert result["errors"] == {"base": "invalid_network"}


async def test_scan_step_network_too_large(hass: HomeAssistant) -> None:
    result = await _start_scan_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"network": "10.0.0.0/16"}
    )
    await hass.async_block_till_done()
    assert result["errors"] == {"base": "network_too_large"}


async def test_scan_step_no_devices_found(hass: HomeAssistant) -> None:
    result = await _start_scan_flow(hass)
    with patch(DISCOVER_PATH, return_value=[]) as discover:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"network": "192.168.1.0/24"}
        )
        await hass.async_block_till_done()
    assert discover.await_count == 1
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "scan"
    assert result["errors"] == {"base": "no_devices_found"}


async def test_scan_step_finds_devices_and_shows_pick(hass: HomeAssistant) -> None:
    result = await _start_scan_flow(hass)
    device = _discovered()
    with patch(DISCOVER_PATH, return_value=[device]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"network": "192.168.1.0/24"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick"
    schema = result["data_schema"].schema
    keys = {str(key): key for key in schema}
    assert keys["device"].default() == f"{MOCK_HOST}:{MOCK_PORT}"
    # discovered as an 8x8 -> matrix_size pre-filled to "8x8"
    assert keys["matrix_size"].default() == "8x8"


async def test_pick_step_creates_entry(
    hass: HomeAssistant, mock_client, mock_setup_entry
) -> None:
    result = await _start_scan_flow(hass)
    device = _discovered()
    with patch(DISCOVER_PATH, return_value=[device]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"network": "192.168.1.0/24"}
        )
        await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"device": f"{MOCK_HOST}:{MOCK_PORT}", "matrix_size": "8x8"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == MOCK_CONFIG
    assert result["result"].unique_id == f"{MOCK_HOST}:{MOCK_PORT}"


async def test_pick_step_prefills_size_when_undetermined(
    hass: HomeAssistant,
) -> None:
    result = await _start_scan_flow(hass)
    device = _discovered(model=None, inputs=None, outputs=None)
    with patch(DISCOVER_PATH, return_value=[device]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"network": "192.168.1.0/24"}
        )
        await hass.async_block_till_done()
    schema = result["data_schema"].schema
    keys = {str(key): key for key in schema}
    assert keys["matrix_size"].default() == "8x8"


async def test_pick_step_duplicate_aborts(
    hass: HomeAssistant, mock_client, mock_setup_entry, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await _start_scan_flow(hass)
    device = _discovered()
    with patch(DISCOVER_PATH, return_value=[device]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"network": "192.168.1.0/24"}
        )
        await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"device": f"{MOCK_HOST}:{MOCK_PORT}", "matrix_size": "8x8"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_pick_step_cannot_connect(hass: HomeAssistant, mock_setup_entry) -> None:
    result = await _start_scan_flow(hass)
    device = _discovered()
    with patch(DISCOVER_PATH, return_value=[device]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"network": "192.168.1.0/24"}
        )
        await hass.async_block_till_done()

    failing = build_mock_client()
    failing.async_connect.side_effect = MTVikiConnectionError("boom")
    with patch(CLIENT_PATH, return_value=failing):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"device": f"{MOCK_HOST}:{MOCK_PORT}", "matrix_size": "8x8"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick"
    assert result["errors"] == {"base": "cannot_connect"}


# ======================================================================
# options flow
# ======================================================================


async def _setup_mock_entry(hass: HomeAssistant, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()


async def _start_options_menu(hass: HomeAssistant, mock_config_entry):
    return await hass.config_entries.options.async_init(mock_config_entry.entry_id)


async def _start_polling_options(hass: HomeAssistant, mock_config_entry):
    """Start the options flow and hop through the menu into the polling step."""
    result = await _start_options_menu(hass, mock_config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "polling"}
    )
    await hass.async_block_till_done()
    return result


async def _start_input_names_options(hass: HomeAssistant, mock_config_entry):
    """Start the options flow and hop through the menu into the input-names step."""
    result = await _start_options_menu(hass, mock_config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "input_names"}
    )
    await hass.async_block_till_done()
    return result


async def _start_scene_names_options(hass: HomeAssistant, mock_config_entry):
    """Start the options flow and hop through the menu into the scene-names step."""
    result = await _start_options_menu(hass, mock_config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "scene_names"}
    )
    await hass.async_block_till_done()
    return result


async def test_options_flow_shows_menu(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    await _setup_mock_entry(hass, mock_config_entry)
    result = await _start_options_menu(hass, mock_config_entry)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert set(result["menu_options"]) == {"polling", "input_names", "scene_names"}


async def test_options_flow_defaults(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    await _setup_mock_entry(hass, mock_config_entry)
    result = await _start_polling_options(hass, mock_config_entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "polling"

    schema = result["data_schema"].schema
    keys = {str(key): key for key in schema}
    assert "enable_polling" in keys
    assert "poll_interval" in keys
    assert keys["enable_polling"].default() is False
    assert keys["poll_interval"].default() == 60


async def test_options_flow_saves(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    await _setup_mock_entry(hass, mock_config_entry)
    result = await _start_polling_options(hass, mock_config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"enable_polling": True, "poll_interval": 30}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"enable_polling": True, "poll_interval": 30}
    assert mock_config_entry.options["enable_polling"] is True
    assert mock_config_entry.options["poll_interval"] == 30


async def test_options_flow_rejects_short_poll_interval(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    """poll_interval has a documented minimum of 10 seconds."""
    import voluptuous as vol

    await _setup_mock_entry(hass, mock_config_entry)
    result = await _start_polling_options(hass, mock_config_entry)
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"enable_polling": True, "poll_interval": 1}
        )


# ---------------------------------------------------------------- input names


async def test_options_flow_input_names_defaults(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    """8x8 entry -> 8 input-name fields, defaulting to "Input N"."""
    await _setup_mock_entry(hass, mock_config_entry)
    result = await _start_input_names_options(hass, mock_config_entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "input_names"

    schema = result["data_schema"].schema
    keys = {str(key): key for key in schema}
    assert {f"input_{n}" for n in range(1, 9)} == set(keys)
    for n in range(1, 9):
        assert keys[f"input_{n}"].default() == f"Input {n}"


async def test_options_flow_input_names_round_trip(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    await _setup_mock_entry(hass, mock_config_entry)
    result = await _start_input_names_options(hass, mock_config_entry)
    submitted = {f"input_{n}": f"Input {n}" for n in range(1, 9)}
    submitted["input_2"] = "PlayStation"
    submitted["input_5"] = "Apple TV"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options["input_names"]["2"] == "PlayStation"
    assert mock_config_entry.options["input_names"]["5"] == "Apple TV"
    assert mock_config_entry.options["input_names"]["1"] == "Input 1"

    # Reopening the flow reflects the names just saved.
    result = await _start_input_names_options(hass, mock_config_entry)
    schema = result["data_schema"].schema
    keys = {str(key): key for key in schema}
    assert keys["input_2"].default() == "PlayStation"
    assert keys["input_5"].default() == "Apple TV"


async def test_options_flow_input_names_blank_field_keeps_default(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    """A blank/whitespace-only name reverts to the default rather than storing empty."""
    await _setup_mock_entry(hass, mock_config_entry)
    result = await _start_input_names_options(hass, mock_config_entry)
    submitted = {f"input_{n}": f"Input {n}" for n in range(1, 9)}
    submitted["input_3"] = "   "
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options["input_names"]["3"] == "Input 3"


async def test_options_flow_input_names_preserves_other_options(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    """Saving input names must not clobber polling options set earlier."""
    await _setup_mock_entry(hass, mock_config_entry)
    result = await _start_polling_options(hass, mock_config_entry)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"enable_polling": True, "poll_interval": 45}
    )
    await hass.async_block_till_done()

    result = await _start_input_names_options(hass, mock_config_entry)
    submitted = {f"input_{n}": f"Input {n}" for n in range(1, 9)}
    submitted["input_1"] = "Chromecast"
    await hass.config_entries.options.async_configure(result["flow_id"], submitted)
    await hass.async_block_till_done()

    assert mock_config_entry.options["enable_polling"] is True
    assert mock_config_entry.options["poll_interval"] == 45
    assert mock_config_entry.options["input_names"]["1"] == "Chromecast"


# ---------------------------------------------------------------- scene names


async def test_options_flow_scene_names_defaults(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    """Always 16 scene-name fields, regardless of matrix size."""
    await _setup_mock_entry(hass, mock_config_entry)
    result = await _start_scene_names_options(hass, mock_config_entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "scene_names"

    schema = result["data_schema"].schema
    keys = {str(key): key for key in schema}
    assert {f"scene_{n}" for n in range(1, 17)} == set(keys)
    for n in range(1, 17):
        assert keys[f"scene_{n}"].default() == f"Scene {n}"


async def test_options_flow_scene_names_round_trip(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    await _setup_mock_entry(hass, mock_config_entry)
    result = await _start_scene_names_options(hass, mock_config_entry)
    submitted = {f"scene_{n}": f"Scene {n}" for n in range(1, 17)}
    submitted["scene_1"] = "Movie Night"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options["scene_names"]["1"] == "Movie Night"
    assert mock_config_entry.options["scene_names"]["2"] == "Scene 2"


# ======================================================================
# setup / unload round trip
# ======================================================================


async def test_setup_and_unload_entry(hass: HomeAssistant, mock_config_entry) -> None:
    """The client must be started on setup and stopped on unload."""
    client = build_mock_client()
    mock_config_entry.add_to_hass(hass)
    with (
        patch(f"custom_components.{DOMAIN}.MTVikiClient", return_value=client),
        patch(CLIENT_PATH, return_value=client),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert mock_config_entry.state is config_entries.ConfigEntryState.LOADED
        assert client.start.await_count == 1

        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert mock_config_entry.state is config_entries.ConfigEntryState.NOT_LOADED
        assert client.stop.await_count >= 1
