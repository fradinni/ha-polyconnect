"""Constants for the Polyconnect integration."""
from collections.abc import Mapping
from logging import getLogger

DOMAIN = "polyconnect"
LOGGER = getLogger(__package__)

PLATFORMS = ["sensor", "climate", "binary_sensor", "switch"]

# Config entry keys
CONF_BRIDGE_URL = "bridge_url"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_LANGUAGE = "language"

DEFAULT_SCAN_INTERVAL = 1  # minute

LANGUAGE_FRENCH = "fr"
LANGUAGE_ENGLISH = "en"
DEFAULT_LANGUAGE = LANGUAGE_FRENCH
LANGUAGE_OPTIONS: dict[str, str] = {
    LANGUAGE_FRENCH: "Français",
    LANGUAGE_ENGLISH: "English",
}


def get_language(options: Mapping[str, object]) -> str:
    """Return the configured display language, defaulting safely to French."""
    language = options.get(CONF_LANGUAGE)
    if isinstance(language, str) and language in LANGUAGE_OPTIONS:
        return language
    return DEFAULT_LANGUAGE


OPERATING_MODE_TRANSLATIONS: dict[str, dict[str, str]] = {
    LANGUAGE_ENGLISH: {
        "Chauffage": "Heating",
        "Froid": "Cooling",
        "Climatisation": "Cooling",
        "Automatique": "Automatic",
        "Eteint": "Off",
        "Éteint": "Off",
    },
}


def translate_operating_mode(value: str, language: str) -> str:
    """Translate a displayed operating mode without changing device commands."""
    return OPERATING_MODE_TRANSLATIONS.get(language, {}).get(value, value)


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
