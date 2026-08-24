"""Constants for the MT-VIKI HDMI Matrix integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "mtviki_matrix"

CONF_MATRIX_SIZE = "matrix_size"
CONF_ENABLE_POLLING = "enable_polling"
CONF_POLL_INTERVAL = "poll_interval"

DEFAULT_PORT = 8080
DEFAULT_MATRIX_SIZE = "8x8"
DEFAULT_ENABLE_POLLING = False
DEFAULT_POLL_INTERVAL = 60
MIN_POLL_INTERVAL = 10
MAX_POLL_INTERVAL = 3600

MANUFACTURER = "MT-VIKI"
DEFAULT_MODEL = "MT-HD0808"

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

# Scene recall buttons: the device supports scenes 1-16 (module convention,
# not device-documented); only 1-8 are enabled in the entity registry by default.
SCENE_COUNT = 16
SCENES_ENABLED_BY_DEFAULT = 8

# EDID presets: the device accepts "SetEDID <input> <sel>" but the valid <sel>
# values are UNVERIFIED (the vendor documentation does not enumerate them).
# We expose presets 1-16.
EDID_PRESET_COUNT = 16

# Output HDCP modes, positionally mapped to raw values 0-3.
# NOTE: the vendor spec self-contradicts on the value semantics. We adopt
# 0=off, 1=HDCP 1.4, 2=HDCP 2.0, 3=HDCP 2.2; the alternative reading
# (0=disable, 1=enable, 2=follow input) is also possible. UNVERIFIED on hardware.
HDCP_MODES: list[str] = ["off", "hdcp_1_4", "hdcp_2_0", "hdcp_2_2"]

# Hard protocol bounds (largest supported matrix is 16x16).
MAX_PORT_NUMBER = 16

# Services
SERVICE_SET_ROUTE = "set_route"
SERVICE_SAVE_SCENE = "save_scene"
SERVICE_RECALL_SCENE = "recall_scene"
SERVICE_SET_OUTPUT_HDCP = "set_output_hdcp"
SERVICE_SET_INPUT_EDID = "set_input_edid"
SERVICE_LOCATE = "locate"

ATTR_DEVICE_ID = "device_id"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_INPUT = "input"
ATTR_OUTPUT = "output"
ATTR_OUTPUTS = "outputs"
ATTR_SCENE = "scene"
ATTR_MODE = "mode"
ATTR_EDID = "edid"
ATTR_COUNT = "count"
ATTR_INTERVAL = "interval"

ALL_OUTPUTS = "all"

# Event fired on the HA bus when an output's routed input changes (see coordinator.py).
EVENT_ROUTE_CHANGED = "mtviki_matrix_route_changed"

# Opt-in network-scan discovery (config flow only; never runs automatically).
CONF_NETWORK = "network"
CONF_DEVICE = "device"
DEFAULT_SCAN_NETWORK = "192.168.1.0/24"
# Upper bound on how many hosts a single scan may probe (see api.async_discover).
MAX_SCAN_HOSTS = 1024
