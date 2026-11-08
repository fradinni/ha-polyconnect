"""Tests for Polyconnect display-language handling."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
CONST_PATH = ROOT / "custom_components" / "polyconnect" / "const.py"
SPEC = importlib.util.spec_from_file_location("polyconnect_const", CONST_PATH)
assert SPEC is not None and SPEC.loader is not None
const = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(const)


class LanguageTests(unittest.TestCase):
    """Verify language defaults, display translations, and device commands."""

    def test_existing_installations_default_to_french(self) -> None:
        self.assertEqual(const.get_language({}), const.LANGUAGE_FRENCH)
        self.assertEqual(
            const.get_language({"language": "unsupported"}),
            const.LANGUAGE_FRENCH,
        )

    def test_explicit_language_is_respected(self) -> None:
        self.assertEqual(
            const.get_language({"language": const.LANGUAGE_ENGLISH}),
            const.LANGUAGE_ENGLISH,
        )
        self.assertEqual(
            const.get_language({"language": const.LANGUAGE_FRENCH}),
            const.LANGUAGE_FRENCH,
        )

    def test_english_operating_modes_are_translated(self) -> None:
        expected = {
            "Chauffage": "Heating",
            "Froid": "Cooling",
            "Climatisation": "Cooling",
            "Automatique": "Automatic",
            "Eteint": "Off",
            "Éteint": "Off",
        }
        for french, english in expected.items():
            with self.subTest(french=french):
                self.assertEqual(
                    const.translate_operating_mode(french, const.LANGUAGE_ENGLISH),
                    english,
                )

    def test_french_and_unknown_values_are_unchanged(self) -> None:
        for value in ("Chauffage", "Froid", "Automatique", "FutureMode"):
            with self.subTest(value=value):
                self.assertEqual(
                    const.translate_operating_mode(value, const.LANGUAGE_FRENCH),
                    value,
                )
        self.assertEqual(
            const.translate_operating_mode("FutureMode", const.LANGUAGE_ENGLISH),
            "FutureMode",
        )

    def test_commands_remain_french(self) -> None:
        self.assertEqual(
            const.HA_TO_POLYCONNECT_MODE,
            {
                "heat": "Chauffage",
                "cool": "Froid",
                "auto": "Automatique",
            },
        )

    def test_option_is_localized_in_english_and_french(self) -> None:
        translations = ROOT / "custom_components" / "polyconnect" / "translations"
        for language in ("en", "fr"):
            with self.subTest(language=language):
                data = json.loads((translations / f"{language}.json").read_text())
                option = data["options"]["step"]["init"]
                self.assertIn("language", option["data"])
                self.assertIn("language", option["data_description"])


if __name__ == "__main__":
    unittest.main()
