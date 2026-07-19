import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_intelligence_api.infrastructure.workflow.docker_sandbox import (
    DockerSandbox,
    DockerSandboxProvider,
    docker_provider_from_env,
)
from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
    _configure_request_sandbox_provider,
)
from data_intelligence_sdk.core.types import DataCorpusPackage


class FakeSandbox:
    def __init__(self):
        self.id = "sandbox-1"
        self.ready = False
        self.deleted = False
        self.files = {}

    def wait_until_ready(self):
        self.ready = True

    def write(self, path, content):
        self.files[path] = content

    def read(self, path):
        return self.files[path]

    def run(self, source, *, timeout_seconds=120, wait=True):
        raise AssertionError("Execution is not expected in this test.")

    def delete(self):
        self.deleted = True


class DockerSandboxProviderTests(unittest.TestCase):
    def test_provider_stages_sources_and_cleans_up(self):
        sandbox = FakeSandbox()
        provider = DockerSandboxProvider(
            image="sandbox:test",
            sandbox_factory=lambda: sandbox,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir, "sales.csv")
            source.write_text("revenue\n42\n", encoding="utf-8")
            corpus = DataCorpusPackage(sources=[str(source)])

            with provider.open(corpus) as session:
                self.assertTrue(sandbox.ready)
                self.assertEqual(
                    session.source_paths[str(source)],
                    "/workspace/input/sales.csv",
                )
                self.assertEqual(
                    sandbox.files["input/sales.csv"],
                    source.read_bytes(),
                )

        self.assertTrue(sandbox.deleted)

    def test_provider_cleans_up_when_staging_fails(self):
        sandbox = FakeSandbox()
        provider = DockerSandboxProvider(
            image="sandbox:test",
            sandbox_factory=lambda: sandbox,
        )

        with self.assertRaisesRegex(ValueError, "requires local source files"):
            with provider.open(DataCorpusPackage(sources=["missing.csv"])):
                pass

        self.assertTrue(sandbox.deleted)

    def test_duplicate_filenames_receive_distinct_staged_paths(self):
        sandbox = FakeSandbox()
        provider = DockerSandboxProvider(
            image="sandbox:test",
            sandbox_factory=lambda: sandbox,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            first_dir = Path(temp_dir, "first")
            second_dir = Path(temp_dir, "second")
            first_dir.mkdir()
            second_dir.mkdir()
            first = first_dir / "data.csv"
            second = second_dir / "data.csv"
            first.write_text("value\n1\n", encoding="utf-8")
            second.write_text("value\n2\n", encoding="utf-8")

            with provider.open(
                DataCorpusPackage(sources=[str(first), str(second)])
            ) as session:
                self.assertEqual(
                    session.source_paths[str(first)],
                    "/workspace/input/data.csv",
                )
                self.assertEqual(
                    session.source_paths[str(second)],
                    "/workspace/input/1_data.csv",
                )

    def test_execute_returns_command_observation(self):
        calls = []

        def command_runner(arguments, **kwargs):
            calls.append((arguments, kwargs))
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=b"analysis complete\n",
                stderr=b"",
            )

        sandbox = DockerSandbox(
            image="sandbox:test",
            command_runner=command_runner,
        )
        sandbox.id = "container-1"

        result = sandbox.run("result = {'total': 42}", timeout_seconds=7)

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stdout, "analysis complete\n")
        self.assertEqual(
            calls[0][0][-5:],
            ["timeout", "--signal=KILL", "7s", "python", "-"],
        )
        self.assertEqual(
            calls[0][1]["input"],
            b"result = {'total': 42}",
        )

    def test_write_rejects_parent_traversal(self):
        sandbox = DockerSandbox(image="sandbox:test")
        sandbox.id = "container-1"

        with self.assertRaisesRegex(ValueError, "cannot traverse"):
            sandbox.write("../secret.txt", b"secret")

    def test_environment_builds_docker_provider(self):
        with patch.dict(
            "os.environ",
            {
                "SANDBOX_DOCKER_IMAGE": "custom:image",
                "SANDBOX_DOCKER_MEMORY": "2g",
                "SANDBOX_DOCKER_CPUS": "2.5",
                "SANDBOX_DOCKER_PIDS_LIMIT": "64",
                "SANDBOX_DOCKER_WORKSPACE_SIZE": "1g",
            },
            clear=True,
        ):
            provider = docker_provider_from_env()

        self.assertEqual(provider.image, "custom:image")
        self.assertEqual(provider.memory, "2g")
        self.assertEqual(provider.cpus, "2.5")
        self.assertEqual(provider.pids_limit, 64)
        self.assertEqual(provider.workspace_size, "1g")

    def test_pipeline_factory_selects_docker_without_axiom_configuration(self):
        sentinel = object()
        with (
            patch.dict(
                "os.environ",
                {"SANDBOX_BACKEND": "docker"},
                clear=True,
            ),
            patch(
                "data_intelligence_api.infrastructure.workflow."
                "pipeline_factory.docker_provider_from_env",
                return_value=sentinel,
            ) as create_provider,
        ):
            provider = _configure_request_sandbox_provider(
                config_manager=object(),
            )

        self.assertIs(provider, sentinel)
        create_provider.assert_called_once_with()

    def test_pipeline_factory_rejects_unknown_backend(self):
        with patch.dict(
            "os.environ",
            {"SANDBOX_BACKEND": "unknown"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "axiom.*docker"):
                _configure_request_sandbox_provider(config_manager=object())


if __name__ == "__main__":
    unittest.main()
