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

from homeassistant import config_entries  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.mtviki_matrix.api import MTVikiConnectionError  # noqa: E402

from .conftest import (  # noqa: E402
    DOMAIN,
    MOCK_CONFIG,
    MOCK_HOST,
    MOCK_PORT,
    build_mock_client,
)

pytestmark = pytest.mark.asyncio

CLIENT_PATH = f"custom_components.{DOMAIN}.config_flow.MTVikiClient"


async def _start_flow(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.async_block_till_done()
    return result


# ======================================================================
# user step
# ======================================================================


async def test_user_step_shows_form(hass: HomeAssistant) -> None:
    result = await _start_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] in ({}, None)


async def test_user_step_schema_defaults(hass: HomeAssistant) -> None:
    """Port defaults to 8080 and matrix size to 8x8 per the contract."""
    result = await _start_flow(hass)
    schema = result["data_schema"].schema
    keys = {str(key): key for key in schema}
    assert "host" in keys
    assert "port" in keys
    assert "matrix_size" in keys
    assert keys["port"].default() == 8080
    assert keys["matrix_size"].default() == "8x8"


async def test_user_step_creates_entry(
    hass: HomeAssistant, mock_client, mock_setup_entry
) -> None:
    result = await _start_flow(hass)
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
    [("2x2", "2x2"), ("4x2", "4x2"), ("4x4", "4x4"), ("8x8", "8x8"), ("16x16", "16x16")],
)
async def test_all_matrix_sizes_accepted(
    hass: HomeAssistant, mock_client, mock_setup_entry, size, expected
) -> None:
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**MOCK_CONFIG, "matrix_size": size}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["matrix_size"] == expected


async def test_custom_port_is_stored_and_used_for_unique_id(
    hass: HomeAssistant, mock_client, mock_setup_entry
) -> None:
    result = await _start_flow(hass)
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
        result = await _start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(MOCK_CONFIG)
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_unknown_error(hass: HomeAssistant, mock_setup_entry) -> None:
    client = build_mock_client()
    client.async_connect.side_effect = RuntimeError("kaboom")
    with patch(CLIENT_PATH, return_value=client):
        result = await _start_flow(hass)
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
        result = await _start_flow(hass)
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

    result = await _start_flow(hass)
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

    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**MOCK_CONFIG, "port": 5000}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


# ======================================================================
# options flow
# ======================================================================


async def test_options_flow_defaults(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    schema = result["data_schema"].schema
    keys = {str(key): key for key in schema}
    assert "enable_polling" in keys
    assert "poll_interval" in keys
    assert keys["enable_polling"].default() is False
    assert keys["poll_interval"].default() == 60


async def test_options_flow_saves(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
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

    mock_config_entry.add_to_hass(hass)
    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"enable_polling": True, "poll_interval": 1}
        )


# ======================================================================
# setup / unload round trip
# ======================================================================


async def test_setup_and_unload_entry(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """The client must be started on setup and stopped on unload."""
    client = build_mock_client()
    mock_config_entry.add_to_hass(hass)
    with patch(f"custom_components.{DOMAIN}.MTVikiClient", return_value=client), patch(
        CLIENT_PATH, return_value=client
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert mock_config_entry.state is config_entries.ConfigEntryState.LOADED
        assert client.start.await_count == 1

        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert mock_config_entry.state is config_entries.ConfigEntryState.NOT_LOADED
        assert client.stop.await_count >= 1
