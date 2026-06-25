"""Constants for the Polyconnect integration."""
from logging import getLogger

DOMAIN = "polyconnect"
LOGGER = getLogger(__package__)

PLATFORMS = ["sensor", "climate", "binary_sensor", "switch"]

# Config entry keys
CONF_BRIDGE_URL = "bridge_url"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 60  # seconds

# ── Operating mode mappings ───────────────────────────────────────────────────
# Polyconnect app name → HA HVACMode value
# UI labels: "Auto", "Chauffage", "Climatisation" (= Froid in the app)
POLYCONNECT_TO_HA_MODE: dict[str, str] = {
    "Chauffage":   "heat",
    "Froid":       "cool",   # app internal name
    "Automatique": "auto",
    # English variants from some firmware versions
    "Heating":     "heat",
    "Cooling":     "cool",
    "Auto":        "auto",
    # Off variants — mapped but heat pump off is handled via turn_off(), not mode
    "Off":         "off",
    "Eteint":      "off",
}

# HA HVACMode → Polyconnect app button text (used when sending commands)
HA_TO_POLYCONNECT_MODE: dict[str, str] = {
    "heat": "Chauffage",
    "cool": "Froid",       # button text on the edit-mode page
    "auto": "Automatique",
}

# ── Regulation mode (preset) ──────────────────────────────────────────────────
# These are the only valid presets — no "Normal" (that concept doesn't exist in the app)
REGULATION_MODES: list[str] = ["Eco", "Smart", "Boost"]
