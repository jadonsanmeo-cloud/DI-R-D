import unittest

from data_intelligence_sdk.core.types import IntentAnalysis, UserQuery
from data_intelligence_sdk.spec.markdown_builder import (
    LLMMarkdownSpecBuilder,
    extract_presentation_contract,
)


def _markdown(contract: str) -> str:
    return f"""# Interactive Execution Spec

## User Request

Create a report.

## Intent

Analyze evidence.

## Preparation Guidance

Use the available corpus.

## Execution Instructions

Retrieve relevant evidence.

## Expected Output

An evidence-based report.

## Presentation Contract

```json
{contract}
```
"""


class MarkdownPresentationContractTests(unittest.TestCase):
    def test_extracts_semantic_roles_without_layout_contract(self):
        contract = extract_presentation_contract(
            _markdown(
                '{"report_content_roles": ["narrative", "recommendation", "chart"]}'
            ),
            required=True,
        )

        self.assertEqual(
            contract["report_content_roles"],
            ["narrative", "recommendation", "chart"],
        )

    def test_rejects_unknown_role(self):
        with self.assertRaises(ValueError):
            extract_presentation_contract(
                _markdown('{"report_content_roles": ["finance-dashboard"]}'),
                required=True,
            )

    def test_builder_retries_when_contract_is_missing(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            def complete_text(self, messages, stage):
                del messages, stage
                self.calls += 1
                if self.calls == 1:
                    return _markdown('{"report_content_roles": []}').split(
                        "## Presentation Contract", 1
                    )[0]
                return _markdown(
                    '{"report_content_roles": ["executive_summary", "narrative"]}'
                )

        client = FakeClient()
        result = LLMMarkdownSpecBuilder(client).build(
            UserQuery(text="Create a report"),
            IntentAnalysis(intent="report"),
        )

        self.assertEqual(client.calls, 2)
        self.assertEqual(
            extract_presentation_contract(result)["report_content_roles"],
            ["executive_summary", "narrative"],
        )


if __name__ == "__main__":
    unittest.main()
