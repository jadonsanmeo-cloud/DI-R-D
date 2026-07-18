import json
import tempfile
import unittest
from pathlib import Path

from langchain.agents.middleware.types import ModelRequest

from data_intelligence_sdk.core.types import DataCorpusPackage, UserQuery
from data_intelligence_sdk.engines.general import (
    _NO_SUCCESSFUL_EXECUTION,
    _RequireSuccessfulExecutionMiddleware,
    _execution_attempt_count,
    _last_successful_execution_result,
    _trusted_profile_code,
)
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.run_context import EngineRunContext
from data_intelligence_sdk.sandbox.artifacts import FilesystemArtifactStore


class GeneralEngineRuntimeTests(unittest.TestCase):
    def test_first_model_call_requires_execute_python(self):
        runtime = EngineRuntimeContext(run_context=EngineRunContext())
        middleware = _RequireSuccessfulExecutionMiddleware(
            runtime,
            max_attempts=3,
        )

        tool_choice = middleware.wrap_model_call(
            ModelRequest(model=object(), messages=[]),
            lambda updated: updated.tool_choice,
        )

        self.assertEqual(tool_choice, "execute_python")

    def test_model_can_answer_after_execution_result(self):
        runtime, artifact = self._runtime_with_artifact()
        successful = artifact.record_code_attempt("result = {'total': 60}")
        artifact.record_execution(
            successful,
            {"success": True, "result": {"total": 60}},
        )
        middleware = _RequireSuccessfulExecutionMiddleware(
            runtime,
            max_attempts=3,
        )

        tool_choice = middleware.wrap_model_call(
            ModelRequest(model=object(), messages=[]),
            lambda updated: updated.tool_choice,
        )

        self.assertIsNone(tool_choice)

    def test_failed_execution_forces_another_attempt(self):
        runtime, artifact = self._runtime_with_artifact()
        failed = artifact.record_code_attempt("read_file('sales.csv')")
        artifact.record_execution(
            failed,
            {"success": False, "result": None},
        )
        middleware = _RequireSuccessfulExecutionMiddleware(
            runtime,
            max_attempts=3,
        )

        tool_choice = middleware.wrap_model_call(
            ModelRequest(model=object(), messages=[]),
            lambda updated: updated.tool_choice,
        )

        self.assertEqual(tool_choice, "execute_python")
        self.assertEqual(_execution_attempt_count(runtime), 1)

    def test_retry_limit_stops_additional_model_calls(self):
        runtime, artifact = self._runtime_with_artifact()
        for attempt_number in range(3):
            failed = artifact.record_code_attempt(f"result = {attempt_number}")
            artifact.record_execution(
                failed,
                {"success": False, "result": None},
            )
        middleware = _RequireSuccessfulExecutionMiddleware(
            runtime,
            max_attempts=3,
        )

        with self.assertRaisesRegex(RuntimeError, "retry limit"):
            middleware.wrap_model_call(
                ModelRequest(model=object(), messages=[]),
                lambda updated: updated.tool_choice,
            )

    def test_reads_latest_successful_execution_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            corpus = DataCorpusPackage(sources=["sales.csv"])
            artifact = FilesystemArtifactStore(Path(temp_dir)).create_run(
                UserQuery(text="Total sales"),
                corpus,
            )
            failed = artifact.record_code_attempt("result = None")
            artifact.record_execution(
                failed,
                {"success": False, "result": None},
            )
            successful = artifact.record_code_attempt("result = {'total': 60}")
            artifact.record_execution(
                successful,
                {"success": True, "result": {"total": 60}},
            )
            runtime = EngineRuntimeContext(
                run_context=EngineRunContext(),
                run_artifact=artifact,
            )

            result = _last_successful_execution_result(runtime)

        self.assertEqual(result, {"total": 60})

    def test_missing_successful_execution_uses_sentinel(self):
        runtime = EngineRuntimeContext(run_context=EngineRunContext())

        result = _last_successful_execution_result(runtime)

        self.assertIs(result, _NO_SUCCESSFUL_EXECUTION)

    def test_trusted_profile_code_is_valid_python(self):
        code = _trusted_profile_code(
            ["/workspace/input/sales.csv", "/workspace/input/notes.txt"]
        )

        compile(code, "<trusted-profile>", "exec")

        self.assertIn("/workspace/input/sales.csv", code)
        self.assertIn('result = {"sources": profiles}', code)

    def _runtime_with_artifact(self):
        self.addCleanup(self._cleanup_temp_dir)
        self._temp_dir = tempfile.TemporaryDirectory()
        corpus = DataCorpusPackage(sources=["sales.csv"])
        artifact = FilesystemArtifactStore(Path(self._temp_dir.name)).create_run(
            UserQuery(text="Total sales"),
            corpus,
        )
        return (
            EngineRuntimeContext(
                run_context=EngineRunContext(),
                run_artifact=artifact,
            ),
            artifact,
        )

    def _cleanup_temp_dir(self):
        self._temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
