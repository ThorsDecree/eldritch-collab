from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestigia.home import initialize_home
from vestigia.web_app import (
    LOOPBACK_HOSTS,
    _display_speaker,
    _web_profile,
    _write_web_profile,
    remembered_home,
    remember_home,
    write_home_env,
)


class WebOnboardingTests(unittest.TestCase):
    def test_home_local_env_never_exposes_a_key_in_its_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = initialize_home(Path(temporary) / "home", name="Lumen")
            path = write_home_env(home, api_key="test-secret", model="gpt-5-mini")
            self.assertEqual(home / ".env", path)
            self.assertIn("OPENAI_API_KEY=test-secret", path.read_text(encoding="utf-8"))

    def test_web_doorway_is_loopback_only(self) -> None:
        self.assertEqual({"127.0.0.1", "localhost", "::1"}, LOOPBACK_HOSTS)

    def test_last_home_pointer_is_only_a_local_convenience_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = initialize_home(root / "home", name="Lumen")
            marker = root / ".vestigia-last-home"
            remember_home(home, marker=marker)
            self.assertEqual(home, remembered_home(marker=marker))

    def test_imported_home_remains_in_orientation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "conversation.txt"
            source.write_text("User: Hello\nAssistant: I am Lumen.\n", encoding="utf-8")
            from vestigia.onboarding import onboard

            home = onboard(source, home_path=root / "imported", resident_name="Lumen")
            self.assertTrue((home / "imports" / "original-materials" / source.name).is_file())

    def test_web_profile_names_the_human_without_affecting_runtime_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = initialize_home(Path(temporary) / "home", name="Lumen")
            self.assertEqual("Humie", _web_profile(home)["human_name"])
            _write_web_profile(home, human_name="Jeff")
            self.assertEqual("Jeff", _web_profile(home)["human_name"])
            self.assertEqual(
                "Lumen",
                _display_speaker(
                    {"speaker_role": "assistant"},
                    resident_name="Lumen",
                    human_name="Jeff",
                ),
            )
            self.assertEqual(
                "Jeff",
                _display_speaker(
                    {"speaker_role": "user"},
                    resident_name="Lumen",
                    human_name="Jeff",
                ),
            )
