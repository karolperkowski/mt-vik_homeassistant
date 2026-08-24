"""Tests for component-level setup in __init__.py.

Covers service registration (lightly -- the services themselves are
exercised end-to-end elsewhere) and the crosspoint Lovelace card's frontend
registration: the static path that serves the JS file, and the frontend
"extra module URL" resource that makes Lovelace load it automatically.

Requires ``pytest-homeassistant-custom-component`` -- see requirements_test.txt.
The whole module skips cleanly if the harness is unavailable so the
HA-independent tests in ``test_mock_matrix.py`` can still run.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip(
    "pytest_homeassistant_custom_component",
    reason="pytest-homeassistant-custom-component is required for setup tests",
)

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from custom_components.mtviki_matrix import (
    _DATA_FRONTEND_RESOURCE_REGISTERED,
    async_setup,
)
from custom_components.mtviki_matrix.const import CARD_FILENAME, CARD_URL_PATH, DOMAIN

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _mock_http(hass: HomeAssistant):
    """Stand in for the `http` component's hass.http (a hard dependency).

    The test hass fixture does not run the real `http` integration's
    async_setup, so hass.http is unset; the mtviki_matrix component assumes
    it exists (manifest.json declares "http" in "dependencies", so real HA
    guarantees this by the time our async_setup runs).
    """
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock(return_value=None)
    yield hass.http


async def test_async_setup_registers_the_card_static_path(
    hass: HomeAssistant, _mock_http
) -> None:
    """async_setup() must serve the card JS at the documented URL path."""
    with patch("custom_components.mtviki_matrix.async_when_setup") as mock_when_setup:
        assert await async_setup(hass, {}) is True

    _mock_http.async_register_static_paths.assert_awaited_once()
    (configs,), _kwargs = _mock_http.async_register_static_paths.call_args
    assert len(configs) == 1
    config = configs[0]
    assert isinstance(config, StaticPathConfig)
    assert config.url_path == CARD_URL_PATH == f"/{DOMAIN}/{CARD_FILENAME}"
    assert config.path.endswith(f"www/{CARD_FILENAME}")
    # And the resource is only registered once frontend is actually up.
    mock_when_setup.assert_called_once()
    assert mock_when_setup.call_args.args[:2] == (hass, "frontend")


async def test_async_setup_adds_the_frontend_resource_once_frontend_is_up(
    hass: HomeAssistant, _mock_http
) -> None:
    """The deferred callback passed to async_when_setup() must add the JS url."""
    with (
        patch("custom_components.mtviki_matrix.async_when_setup") as mock_when_setup,
        patch("custom_components.mtviki_matrix.add_extra_js_url") as mock_add_url,
    ):
        await async_setup(hass, {})
        deferred_callback = mock_when_setup.call_args.args[2]
        await deferred_callback(hass, "frontend")

    mock_add_url.assert_called_once_with(hass, CARD_URL_PATH)


async def test_async_setup_registers_frontend_resources_only_once(
    hass: HomeAssistant, _mock_http
) -> None:
    """Calling async_setup() again (e.g. a second config entry) must not
    re-register the static path or schedule a second async_when_setup call."""
    with patch("custom_components.mtviki_matrix.async_when_setup") as mock_when_setup:
        await async_setup(hass, {})
        await async_setup(hass, {})

    assert hass.data[_DATA_FRONTEND_RESOURCE_REGISTERED] is True
    _mock_http.async_register_static_paths.assert_awaited_once()
    mock_when_setup.assert_called_once()
