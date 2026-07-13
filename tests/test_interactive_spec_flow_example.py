import importlib.util
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path


def load_interactive_spec_flow_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "run_interactive_spec_flow.py"
    )
    spec = importlib.util.spec_from_file_location(
        "interactive_spec_flow_example", module_path
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeLLMClient:
    def __init__(self):
        self.messages = []

    def complete_json(self, messages):
        self.messages.append(messages)
        return {"ok": True}


class InteractiveSpecFlowExampleTests(unittest.TestCase):
    def test_tracing_llm_client_prints_messages_and_response(self) -> None:
        module = load_interactive_spec_flow_module()
        fake = FakeLLMClient()
        tracer = module.TracingLLMClient(fake)

        with redirect_stdout(io.StringIO()) as stdout:
            result = tracer.complete_json(
                [{"role": "user", "content": "select data"}]
            )

        output = stdout.getvalue()
        self.assertEqual(result, {"ok": True})
        self.assertIn("=== LLM Call 1 Input ===", output)
        self.assertIn("select data", output)
        self.assertIn("=== LLM Call 1 Output ===", output)
        self.assertIn('"ok": true', output)


if __name__ == "__main__":
    unittest.main()
