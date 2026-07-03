import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_intelligence_sdk.runtime.config import ConfigManager, get_config_manager


class RuntimeConfigTests(unittest.TestCase):
    def test_config_manager_loads_openrouter_settings_from_toml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "proxy-openrouter.toml"
            config_path.write_text(
                "[models]\n"
                "[[models.llms]]\n"
                'name = "${env:LLM_MODEL_NAME}"\n'
                'provider = "proxy/openrouter"\n'
                'api_base = "https://openrouter.ai/api/v1"\n'
                'api_key = "${env:OPENROUTER_API_KEY}"\n',
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "LLM_MODEL_NAME": "openrouter/test-model",
                    "OPENROUTER_API_KEY": "test-key",
                },
                clear=True,
            ):
                settings = ConfigManager(config_path).openrouter_settings()

        self.assertEqual(settings.model, "openrouter/test-model")
        self.assertEqual(settings.api_key, "test-key")
        self.assertEqual(settings.base_url, "https://openrouter.ai/api/v1")

    def test_config_manager_caches_raw_toml_but_resolves_env_per_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "proxy-openrouter.toml"
            config_path.write_text(
                "[models]\n"
                "[[models.llms]]\n"
                'name = "${env:LLM_MODEL_NAME}"\n'
                'provider = "proxy/openrouter"\n'
                'api_key = "${env:OPENROUTER_API_KEY}"\n',
                encoding="utf-8",
            )
            manager = ConfigManager(config_path)

            import data_intelligence_sdk.runtime.config as config_module

            with patch.object(
                config_module.tomllib, "load", wraps=config_module.tomllib.load
            ) as load_toml:
                with patch.dict(
                    os.environ,
                    {
                        "LLM_MODEL_NAME": "first-model",
                        "OPENROUTER_API_KEY": "first-key",
                    },
                    clear=True,
                ):
                    first = manager.openrouter_settings()
                with patch.dict(
                    os.environ,
                    {
                        "LLM_MODEL_NAME": "second-model",
                        "OPENROUTER_API_KEY": "second-key",
                    },
                    clear=True,
                ):
                    second = manager.openrouter_settings()

        self.assertEqual(load_toml.call_count, 1)
        self.assertEqual(first.model, "first-model")
        self.assertEqual(second.model, "second-model")
        self.assertEqual(second.api_key, "second-key")

    def test_get_config_manager_reuses_manager_for_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = str(Path(temp_dir) / "proxy-openrouter.toml")

            first = get_config_manager(config_path)
            second = get_config_manager(config_path)

        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
