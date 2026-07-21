import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_intelligence_sdk.runtime.llm_client import OpenAICompatibleLLMClient


class OpenAICompatibleLLMClientTests(unittest.TestCase):
    def test_loads_provider_settings_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "models.toml"
            config_path.write_text(
                """
[models]
[[models.llms]]
name = "configured-model"
provider = "proxy/openrouter"
api_base = "https://configured.example/v1"
api_key = "configured-key"
""".strip(),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "OPENAI_COMPATIBLE_BASE_URL": "https://deprecated.example/v1",
                    "OPENAI_COMPATIBLE_API_KEY": "deprecated-key",
                    "OPENAI_COMPATIBLE_MODEL": "deprecated-model",
                },
                clear=True,
            ):
                client = OpenAICompatibleLLMClient(config_path=config_path)

        self.assertEqual(client.base_url, "https://configured.example/v1")
        self.assertEqual(client.api_key, "configured-key")
        self.assertEqual(client.model, "configured-model")


if __name__ == "__main__":
    unittest.main()
