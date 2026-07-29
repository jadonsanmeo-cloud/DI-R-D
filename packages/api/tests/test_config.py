import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_intelligence_api.infrastructure.config.settings import ApiSettings


class ApiSettingsTests(unittest.TestCase):
    def test_defaults_use_repository_root_and_local_frontend_origins(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = ApiSettings.from_env()

        self.assertEqual(settings.data_corpus_root, Path.cwd().resolve())
        self.assertEqual(
            settings.cors_origins,
            ("http://localhost:3000", "http://127.0.0.1:3000"),
        )
        self.assertEqual(settings.pipeline_timeout_seconds, 300.0)
        self.assertIsNone(settings.database_url)
        self.assertEqual(settings.spec_confirmation_ttl_seconds, 86400)
        self.assertEqual(settings.max_spec_revision_rounds, 5)
        self.assertIsNone(settings.model_config_path)
        self.assertEqual(settings.default_organization_id, "test-org")

    def test_environment_overrides_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "DATA_CORPUS_ROOT": temp_dir,
                    "API_CORS_ORIGINS": "http://localhost:4000, http://127.0.0.1:4000 ",
                    "PIPELINE_TIMEOUT_SECONDS": "12.5",
                    "DATABASE_URL": "postgresql://user:pass@db:5432/app",
                    "SPEC_CONFIRMATION_TTL_SECONDS": "600",
                    "MAX_SPEC_REVISION_ROUNDS": "7",
                    "DEFAULT_ORGANIZATION_ID": "naph-org",
                },
                clear=True,
            ):
                settings = ApiSettings.from_env()

        self.assertEqual(settings.data_corpus_root, Path(temp_dir).resolve())
        self.assertEqual(
            settings.cors_origins,
            ("http://localhost:4000", "http://127.0.0.1:4000"),
        )
        self.assertEqual(settings.pipeline_timeout_seconds, 12.5)
        self.assertEqual(
            settings.database_url, "postgresql://user:pass@db:5432/app"
        )
        self.assertEqual(settings.spec_confirmation_ttl_seconds, 600)
        self.assertEqual(settings.max_spec_revision_rounds, 7)
        self.assertEqual(settings.default_organization_id, "naph-org")

    def test_model_config_path_loads_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"MODEL_CONFIG_PATH": "/app/configs/custom-openrouter.toml"},
            clear=True,
        ):
            settings = ApiSettings.from_env()

        self.assertEqual(
            settings.model_config_path,
            Path("/app/configs/custom-openrouter.toml"),
        )

    def test_openrouter_environment_configures_the_compatible_chat_client(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "secret",
                "OPENROUTER_BASE_URL": "https://openrouter.example/api/v1",
                "LLM_MODEL_NAME": "qwen/qwen3-30b-a3b",
            },
            clear=True,
        ):
            settings = ApiSettings.from_env()

        self.assertEqual(settings.openai_compatible_api_key, "secret")
        self.assertEqual(
            settings.openai_compatible_base_url,
            "https://openrouter.example/api/v1",
        )
        self.assertEqual(
            settings.openai_compatible_model,
            "qwen/qwen3-30b-a3b",
        )

    def test_timeout_must_be_positive(self) -> None:
        with patch.dict(
            os.environ,
            {"PIPELINE_TIMEOUT_SECONDS": "0"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "PIPELINE_TIMEOUT_SECONDS"):
                ApiSettings.from_env()

    def test_confirmation_limits_must_be_positive(self) -> None:
        for name in ("SPEC_CONFIRMATION_TTL_SECONDS", "MAX_SPEC_REVISION_ROUNDS"):
            with self.subTest(name=name):
                with patch.dict(os.environ, {name: "0"}, clear=True):
                    with self.assertRaisesRegex(ValueError, name):
                        ApiSettings.from_env()


if __name__ == "__main__":
    unittest.main()
