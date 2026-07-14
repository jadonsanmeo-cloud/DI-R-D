import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class DockerConfigTests(unittest.TestCase):
    def read_required(self, filename: str) -> str:
        path = ROOT / filename
        self.assertTrue(path.exists(), f"{filename} must exist")
        return path.read_text(encoding="utf-8")

    def test_dockerfile_runs_backend_as_non_root_on_port_8000(self) -> None:
        content = self.read_required("docker/Dockerfile")
        self.assertIn("FROM python:3.11-slim", content)
        self.assertIn("USER app", content)
        self.assertIn("EXPOSE 8000", content)
        self.assertIn('"data_intelligence_api.main:app"', content)
        self.assertIn('"--port", "8000"', content)
        self.assertIn("HEALTHCHECK", content)

    def test_dockerfile_installs_locked_dependencies_with_uv(self) -> None:
        content = self.read_required("docker/Dockerfile")
        self.assertIn("ghcr.io/astral-sh/uv:", content)
        self.assertIn("COPY pyproject.toml README.md ./", content)
        self.assertIn("uv pip install --system --no-cache ./packages/sdk ./packages/api", content)
        self.assertIn('ENV PATH="/app/.venv/bin:$PATH"', content)
        self.assertNotIn("python -m pip install", content)

    def test_compose_contains_backend_api_and_private_postgres(self) -> None:
        content = self.read_required("docker/docker-compose.yaml")
        self.assertIn("services:\n  api:", content)
        self.assertNotIn("frontend:", content)
        self.assertNotIn("web:", content)
        self.assertIn('"8000:8000"', content)
        self.assertIn("DATA_CORPUS_ROOT: /app/data", content)
        self.assertIn("../data:/app/data:ro", content)
        self.assertIn("api_uploads:/app/.uploads", content)
        self.assertIn("db:\n    image: postgres:17-alpine", content)
        self.assertIn("DATABASE_URL:", content)
        self.assertIn("condition: service_healthy", content)
        self.assertIn("pg_isready", content)
        self.assertIn("postgres_data:/var/lib/postgresql/data", content)
        self.assertNotIn('"5432:5432"', content)
        self.assertIn("MODEL_CONFIG_PATH: /app/configs/development/proxy-openrouter.toml", content)
        self.assertIn("OPENROUTER_API_KEY: ${OPENROUTER_API_KEY:-}", content)
        self.assertIn("LLM_MODEL_NAME: ${LLM_MODEL_NAME:-}", content)
        self.assertNotIn("OPENROUTE_API_KEY", content)
        self.assertIn("- path: .env", content)

    def test_dockerfile_dockerignore_excludes_secrets_and_frontend(self) -> None:
        lines = self.read_required("docker/Dockerfile.dockerignore").splitlines()
        self.assertIn(".env", lines)
        self.assertIn(".env.*", lines)
        self.assertIn("!.env.example", lines)
        self.assertIn("web/", lines)
        self.assertIn(".git/", lines)


if __name__ == "__main__":
    unittest.main()
