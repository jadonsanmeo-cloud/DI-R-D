import os
import unittest
from unittest.mock import patch

from data_intelligence_api.infrastructure.config.settings import ApiSettings


class MemorySettingsTests(unittest.TestCase):
    def test_memory_settings_are_loaded_from_environment(self) -> None:
        values = {
            "MEMORY_ENABLED": "true",
            "MEMORY_SERVICE_URL": "http://memory.local:8005/",
            "MEMORY_TENANT_ID": "tenant-1",
            "MEMORY_DEFAULT_USER_ID": "user-1",
            "MEMORY_SEARCH_LIMIT": "17",
            "MEMORY_TIMEOUT_SECONDS": "1.5",
            "MEMORY_SERVICE_TOKEN": "service-token",
        }

        with patch.dict(os.environ, values, clear=True):
            settings = ApiSettings.from_env()

        self.assertTrue(settings.memory_enabled)
        self.assertEqual(settings.memory_service_url, "http://memory.local:8005/")
        self.assertEqual(settings.memory_tenant_id, "tenant-1")
        self.assertEqual(settings.memory_default_user_id, "user-1")
        self.assertEqual(settings.memory_search_limit, 17)
        self.assertEqual(settings.memory_timeout_seconds, 1.5)
        self.assertEqual(settings.memory_service_token, "service-token")

    def test_memory_enabled_rejects_unknown_boolean_value(self) -> None:
        with patch.dict(os.environ, {"MEMORY_ENABLED": "sometimes"}, clear=True):
            with self.assertRaisesRegex(ValueError, "MEMORY_ENABLED"):
                ApiSettings.from_env()

    def test_memory_limit_and_timeout_must_be_positive(self) -> None:
        for name, value in (
            ("MEMORY_SEARCH_LIMIT", "0"),
            ("MEMORY_TIMEOUT_SECONDS", "-1"),
        ):
            with self.subTest(name=name):
                with patch.dict(os.environ, {name: value}, clear=True):
                    with self.assertRaisesRegex(ValueError, name):
                        ApiSettings.from_env()


if __name__ == "__main__":
    unittest.main()
