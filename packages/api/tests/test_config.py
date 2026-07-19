import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_intelligence_api.infrastructure.config.settings import ApiSettings


class ApiSettingsTests(unittest.TestCase):
    def test_missing_config_uses_local_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_config = Path(temp_dir) / "missing.toml"
            with patch.dict(
                os.environ,
                {"MODEL_CONFIG_PATH": str(missing_config)},
                clear=True,
            ):
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
        self.assertEqual(settings.model_config_path, missing_config)

    def test_toml_loads_non_secret_api_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "runtime.toml"
            config_path.write_text(
                """
[api]
cors_origins = ["https://frontend.example", "http://localhost:3000"]
pipeline_timeout_seconds = 12.5
spec_confirmation_ttl_seconds = 600
max_spec_revision_rounds = 7
max_upload_bytes = 123456
""".strip(),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "DATA_CORPUS_ROOT": temp_dir,
                    "DATABASE_URL": "postgresql://user:pass@db:5432/app",
                    "MODEL_CONFIG_PATH": str(config_path),
                },
                clear=True,
            ):
                settings = ApiSettings.from_env()

        self.assertEqual(settings.data_corpus_root, Path(temp_dir).resolve())
        self.assertEqual(
            settings.cors_origins,
            ("https://frontend.example", "http://localhost:3000"),
        )
        self.assertEqual(settings.pipeline_timeout_seconds, 12.5)
        self.assertEqual(settings.database_url, "postgresql://user:pass@db:5432/app")
        self.assertEqual(settings.spec_confirmation_ttl_seconds, 600)
        self.assertEqual(settings.max_spec_revision_rounds, 7)
        self.assertEqual(settings.max_upload_bytes, 123456)
        self.assertFalse(hasattr(settings, "openai_compatible_base_url"))
        self.assertFalse(hasattr(settings, "openai_compatible_api_key"))
        self.assertFalse(hasattr(settings, "openai_compatible_model"))

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

    def test_timeout_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "runtime.toml"
            config_path.write_text(
                "[api]\npipeline_timeout_seconds = 0\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"MODEL_CONFIG_PATH": str(config_path)},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "pipeline_timeout_seconds"):
                    ApiSettings.from_env()

    def test_confirmation_limits_must_be_positive(self) -> None:
        for name in ("spec_confirmation_ttl_seconds", "max_spec_revision_rounds"):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    config_path = Path(temp_dir) / "runtime.toml"
                    config_path.write_text(
                        f"[api]\n{name} = 0\n",
                        encoding="utf-8",
                    )
                    with patch.dict(
                        os.environ,
                        {"MODEL_CONFIG_PATH": str(config_path)},
                        clear=True,
                    ):
                        with self.assertRaisesRegex(ValueError, name):
                            ApiSettings.from_env()


if __name__ == "__main__":
    unittest.main()
