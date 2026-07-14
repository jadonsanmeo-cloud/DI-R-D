import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from data_intelligence_sdk.core.types import FinalResponse


class FakePipeline:
    def run(self, query, corpus_package):
        self.query = query
        self.corpus_package = corpus_package
        return FinalResponse(answer="openrouter answer")


def load_run_pipeline_module():
    module_path = Path(__file__).resolve().parents[3] / "examples" / "run_pipeline.py"
    spec = importlib.util.spec_from_file_location("run_pipeline_example", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RunPipelineExampleTests(unittest.TestCase):
    def test_main_uses_openrouter_pipeline_without_flag(self) -> None:
        module = load_run_pipeline_module()
        fake_pipeline = FakePipeline()

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sales.csv"
            csv_path.write_text(
                "country,status,revenue\nUS,complete,10\n", encoding="utf-8"
            )
            argv = [
                "run_pipeline.py",
                "--source",
                str(csv_path),
                "--query",
                "What columns are in this file?",
            ]

            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    module,
                    "create_example_pipeline",
                    return_value=fake_pipeline,
                ) as create_pipeline,
                redirect_stdout(io.StringIO()) as stdout,
            ):
                module.main()

        create_pipeline.assert_called_once()
        self.assertIsInstance(
            create_pipeline.call_args.kwargs["logger"], module.FileRuntimeLogger
        )
        self.assertEqual(fake_pipeline.query.text, "What columns are in this file?")
        self.assertEqual(fake_pipeline.corpus_package.sources, [str(csv_path)])
        self.assertEqual(stdout.getvalue().strip(), "openrouter answer")

    def test_no_trace_flag_disables_pipeline_log_file(self) -> None:
        module = load_run_pipeline_module()
        fake_pipeline = FakePipeline()

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sales.csv"
            csv_path.write_text(
                "country,status,revenue\nUS,complete,10\n", encoding="utf-8"
            )
            argv = [
                "run_pipeline.py",
                "--source",
                str(csv_path),
                "--query",
                "What columns are in this file?",
                "--no-trace",
            ]

            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    module,
                    "create_example_pipeline",
                    return_value=fake_pipeline,
                ) as create_pipeline,
                redirect_stdout(io.StringIO()),
            ):
                module.main()

        create_pipeline.assert_called_once_with(logger=None)

    def test_repeated_source_arguments_build_corpus_sources(self) -> None:
        module = load_run_pipeline_module()
        fake_pipeline = FakePipeline()

        with tempfile.TemporaryDirectory() as temp_dir:
            sales_path = Path(temp_dir) / "sales.csv"
            customers_path = Path(temp_dir) / "customers.csv"
            sales_path.write_text(
                "country,status,revenue\nUS,complete,10\n", encoding="utf-8"
            )
            customers_path.write_text("customer_id,country\n1,US\n", encoding="utf-8")
            argv = [
                "run_pipeline.py",
                "--source",
                str(sales_path),
                "--source",
                str(customers_path),
                "--query",
                "What sources are available?",
            ]

            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    module, "create_example_pipeline", return_value=fake_pipeline
                ),
                redirect_stdout(io.StringIO()) as stdout,
            ):
                module.main()

        self.assertEqual(fake_pipeline.query.text, "What sources are available?")
        self.assertEqual(
            fake_pipeline.corpus_package.sources, [str(sales_path), str(customers_path)]
        )
        self.assertEqual(fake_pipeline.corpus_package.schemas, {})
        self.assertEqual(stdout.getvalue().strip(), "openrouter answer")

    def test_package_json_builds_full_corpus_package(self) -> None:
        module = load_run_pipeline_module()
        fake_pipeline = FakePipeline()

        with tempfile.TemporaryDirectory() as temp_dir:
            package_dir = Path(temp_dir)
            vector_dir = package_dir / "vectordb"
            vector_dir.mkdir()
            db_path = package_dir / "warehouse.db"
            db_path.write_text("mock sqlite bytes", encoding="utf-8")
            schema_path = package_dir / "schema.json"
            schema_path.write_text(
                json.dumps({"tables": {"orders": {"columns": ["country", "revenue"]}}}),
                encoding="utf-8",
            )
            catalog_path = package_dir / "catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {"datasets": [{"name": "orders", "description": "Mock orders"}]}
                ),
                encoding="utf-8",
            )
            package_path = Path(temp_dir) / "package.json"
            package_path.write_text(
                json.dumps(
                    {
                        "vectordb": "vectordb",
                        "db": "warehouse.db",
                        "schema": "schema.json",
                        "catalog": "catalog.json",
                    }
                ),
                encoding="utf-8",
            )
            argv = [
                "run_pipeline.py",
                "--package",
                str(package_path),
                "--query",
                "Summarize this package",
            ]

            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    module, "create_example_pipeline", return_value=fake_pipeline
                ),
                redirect_stdout(io.StringIO()) as stdout,
            ):
                module.main()

        self.assertEqual(fake_pipeline.query.text, "Summarize this package")
        self.assertEqual(
            fake_pipeline.corpus_package.sources, [str(vector_dir), str(db_path)]
        )
        self.assertEqual(
            fake_pipeline.corpus_package.schemas,
            {"tables": {"orders": {"columns": ["country", "revenue"]}}},
        )
        self.assertEqual(
            fake_pipeline.corpus_package.metadata,
            {
                "catalog": {
                    "datasets": [{"name": "orders", "description": "Mock orders"}]
                },
                "package": {
                    "vectordb": str(vector_dir),
                    "db": str(db_path),
                    "schema": str(schema_path),
                    "catalog": str(catalog_path),
                },
            },
        )
        self.assertEqual(stdout.getvalue().strip(), "openrouter answer")

    def test_package_loader_preserves_postgres_connection_refs(self) -> None:
        module = load_run_pipeline_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            package_dir = Path(temp_dir)
            schema_path = package_dir / "schema.json"
            schema_path.write_text(json.dumps({"tables": {}}), encoding="utf-8")
            catalog_path = package_dir / "catalog.json"
            catalog_path.write_text(json.dumps({"datasets": []}), encoding="utf-8")
            package_path = package_dir / "data_corpus_package.json"
            package_path.write_text(
                json.dumps(
                    {
                        "vectordb": "postgresql://demo:demo@localhost:5432/data_corpus?schema=vectordb",
                        "db": "postgresql://demo:demo@localhost:5432/data_corpus",
                        "schema": "schema.json",
                        "catalog": "catalog.json",
                    }
                ),
                encoding="utf-8",
            )

            corpus_package = module._load_package_json(str(package_path))

        self.assertEqual(
            corpus_package.sources,
            [
                "postgresql://demo:demo@localhost:5432/data_corpus?schema=vectordb",
                "postgresql://demo:demo@localhost:5432/data_corpus",
            ],
        )

    def test_repo_mock_package_loads_full_corpus_package(self) -> None:
        module = load_run_pipeline_module()
        package_path = (
            Path(__file__).resolve().parents[3]
            / "examples"
            / "data_corpus_package"
            / "data_corpus_package.json"
        )

        corpus_package = module._load_package_json(str(package_path))

        self.assertEqual(
            corpus_package.sources,
            [
                "postgresql://data_intelligence:data_intelligence@localhost:2345/data_corpus?schema=vectordb",
                "postgresql://data_intelligence:data_intelligence@localhost:2345/data_corpus",
            ],
        )
        self.assertEqual(
            corpus_package.schemas["tables"]["orders"]["columns"],
            ["order_id", "customer_id", "country", "status", "revenue"],
        )
        self.assertEqual(
            corpus_package.schemas["vector_collections"]["document_chunks"]["columns"],
            ["chunk_id", "document_id", "content", "embedding", "metadata"],
        )
        self.assertEqual(
            corpus_package.metadata["catalog"]["datasets"][0]["name"], "orders"
        )
        self.assertEqual(
            corpus_package.metadata["catalog"]["datasets"][1]["name"],
            "document_chunks",
        )

    def test_repo_data_corpus_package_includes_docker_and_seed_content(self) -> None:
        package_dir = (
            Path(__file__).resolve().parents[3] / "examples" / "data_corpus_package"
        )
        csv_files = sorted((package_dir / "raw" / "csv").glob("*.csv"))
        txt_files = sorted((package_dir / "raw" / "txt").glob("*.txt"))
        compose_text = (package_dir / "docker-compose.yml").read_text(encoding="utf-8")
        seed_text = (package_dir / "initdb" / "001_schema_and_seed.sql").read_text(
            encoding="utf-8"
        )
        schema_payload = json.loads(
            (package_dir / "schema.json").read_text(encoding="utf-8")
        )
        catalog_payload = json.loads(
            (package_dir / "catalog.json").read_text(encoding="utf-8")
        )

        self.assertEqual(len(csv_files), 5)
        self.assertEqual(len(txt_files), 5)
        self.assertIn("pgvector/pgvector", compose_text)
        self.assertIn("OPENROUTER_EMBEDDING_MODEL", compose_text)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS vector", seed_text)
        for table_name in [
            "customers",
            "orders",
            "products",
            "support_tickets",
            "web_events",
        ]:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table_name}", seed_text)
        self.assertIn("CREATE TABLE IF NOT EXISTS vectordb.document_chunks", seed_text)
        for chunk_id in [
            "chunk_orders_summary_001",
            "chunk_customer_segments_001",
            "chunk_product_catalog_001",
            "chunk_support_notes_001",
            "chunk_web_activity_001",
        ]:
            self.assertIn(chunk_id, seed_text)
        self.assertIn("content TEXT NOT NULL", seed_text)
        self.assertIn("embedding vector(5) NOT NULL", seed_text)
        self.assertIn("metadata JSONB NOT NULL", seed_text)
        self.assertEqual(
            schema_payload["embedding"]["environment_variable"],
            "OPENROUTER_EMBEDDING_MODEL",
        )
        self.assertEqual(
            schema_payload["embedding"]["default_model"],
            "openai/text-embedding-3-small",
        )
        self.assertEqual(len(catalog_payload["raw_files"]["csv"]), 5)
        self.assertEqual(len(catalog_payload["raw_files"]["txt"]), 5)

    def test_direct_script_help_execution_can_import_example_workflow(self) -> None:
        script_path = (
            Path(__file__).resolve().parents[3] / "examples" / "run_pipeline.py"
        )

        result = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Run the Data Intelligence SDK pipeline", result.stdout)

    def test_openrouter_flag_is_not_supported(self) -> None:
        module = load_run_pipeline_module()

        with patch.object(sys, "argv", ["run_pipeline.py", "--openrouter"]):
            with self.assertRaises(SystemExit) as raised:
                module.main()

        self.assertNotEqual(raised.exception.code, 0)

    def test_csv_flag_is_not_supported(self) -> None:
        module = load_run_pipeline_module()

        with patch.object(sys, "argv", ["run_pipeline.py", "--csv", "sales.csv"]):
            with self.assertRaises(SystemExit) as raised:
                module.main()

        self.assertNotEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
