from __future__ import annotations

import unittest

from video_study.desktop.settings import DesktopSettingsInput, validate_input


class DesktopSettingsTests(unittest.TestCase):
    def test_validated_settings_do_not_contain_secret(self) -> None:
        raw = DesktopSettingsInput("https://example.com/v1", "model-a", "faster-whisper", api_key="secret")
        value = validate_input(raw)
        self.assertNotIn("secret", repr(raw))
        self.assertNotIn("secret", repr(value))


if __name__ == "__main__":
    unittest.main()
