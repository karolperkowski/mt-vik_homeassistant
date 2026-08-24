"""Shared pytest fixtures for the mtviki_matrix test suite.

This conftest is deliberately defensive: ``tests/test_mock_matrix.py`` has no
Home Assistant dependency at all and must stay collectable/runnable even when
``pytest-homeassistant-custom-component`` is not installed (see README /
requirements_test.txt). Everything HA-specific is therefore guarded.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

DOMAIN = "mtviki_matrix"

# Entry data used by the config-flow tests; mirrors the build contract.
MOCK_HOST = "192.168.1.200"
MOCK_PORT = 8080
MOCK_SIZE = "8x8"
MOCK_CONFIG = {
    "host": MOCK_HOST,
    "port": MOCK_PORT,
    "matrix_size": MOCK_SIZE,
}

try:  # pragma: no cover - environment probe
    import pytest_homeassistant_custom_component  # noqa: F401

    HAS_HA_TEST_HARNESS = True
except ImportError:  # pragma: no cover
    HAS_HA_TEST_HARNESS = False

try:  # pragma: no cover - environment probe
    import pytest_socket  # noqa: F401

    HAS_PYTEST_SOCKET = True
except ImportError:  # pragma: no cover
    HAS_PYTEST_SOCKET = False


if not HAS_PYTEST_SOCKET:
    # pytest-socket (pulled in by pytest-homeassistant-custom-component) blocks
    # ALL socket creation during tests. Modules that talk to the loopback mock
    # opt back in with `pytest.mark.usefixtures("socket_enabled")`; when the
    # plugin is absent that marker still has to resolve, hence this no-op.
    @pytest.fixture
    def socket_enabled():
        yield


if HAS_HA_TEST_HARNESS:
    # Required by pytest-homeassistant-custom-component so that HA will load a
    # component out of custom_components/ during tests.
    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Enable loading of custom integrations in every HA test."""
        yield

    @pytest.fixture
    def mock_config_entry():
        """A MockConfigEntry matching the contract's entry data."""
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        return MockConfigEntry(
            domain=DOMAIN,
            title=f"MT-VIKI Matrix ({MOCK_HOST})",
            data=dict(MOCK_CONFIG),
            options={},
            unique_id=f"{MOCK_HOST}:{MOCK_PORT}",
        )


@pytest.fixture
def mock_setup_entry():
    """Prevent the real integration setup from running during flow tests."""
    with patch(
        f"custom_components.{DOMAIN}.async_setup_entry", return_value=True
    ) as mock:
        yield mock


def build_mock_client(**overrides):
    """Build a MagicMock standing in for ``api.MTVikiClient``.

    Only the surface the config flow is contracted to touch is stubbed:
    ``async_connect``, ``async_refresh``, ``async_full_refresh``, ``stop`` and
    ``state``.
    """
    state = MagicMock()
    state.routes = {o: o for o in range(1, 9)}
    state.keylock = False
    state.beep_en = False
    state.firmware = "01.00.00"
    state.model = "FHDM88LAMG"
    state.ip = "192.168.1.186"
    state.ip_mask = "255.255.255.0"
    state.input_hdcp = {}
    state.output_hdcp = {}
    state.title = None
    state.service_type = None
    state.service_num = None
    state.connected = True
    for key, value in overrides.items():
        setattr(state, key, value)

    client = MagicMock()
    client.async_connect = AsyncMock(return_value=None)
    client.async_refresh = AsyncMock(return_value=state)
    client.async_full_refresh = AsyncMock(return_value=state)
    client.start = AsyncMock(return_value=None)
    client.stop = AsyncMock(return_value=None)
    client.set_state_callback = MagicMock()
    client.recent_traffic = MagicMock(return_value=[])
    # Command methods used by entity-level tests (select/media_player/button);
    # plain MagicMock attributes are not awaitable, so these need to be
    # AsyncMock explicitly.
    client.async_switch = AsyncMock(return_value=None)
    client.async_switch_all = AsyncMock(return_value=None)
    client.async_scene_save = AsyncMock(return_value=None)
    client.async_scene_recall = AsyncMock(return_value=None)
    client.async_set_keylock = AsyncMock(return_value=None)
    client.async_set_beep = AsyncMock(return_value=None)
    client.async_locate = AsyncMock(return_value=None)
    client.async_set_output_hdcp = AsyncMock(return_value=None)
    client.async_set_input_edid = AsyncMock(return_value=None)
    client.state = state
    return client


@pytest.fixture
def mock_client():
    """Patch the client the config flow imported and hand back the instance.

    Assumes ``config_flow.py`` does ``from .api import MTVikiClient`` per the
    build contract; patching the name in the ``config_flow`` module namespace is
    what makes that work.
    """
    client = build_mock_client()
    with patch(
        f"custom_components.{DOMAIN}.config_flow.MTVikiClient", return_value=client
    ):
        yield client
