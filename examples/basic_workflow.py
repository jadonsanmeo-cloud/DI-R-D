"""Compatibility entry point for the application-owned example pipeline."""

from pathlib import Path
import sys


AXIOM_CLIENT_SRC = (
    Path(__file__).resolve().parent.parent.parent
    / "AXIOM"
    / "packages"
    / "axiom-sandbox-client"
    / "src"
)
if AXIOM_CLIENT_SRC.is_dir() and str(AXIOM_CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(AXIOM_CLIENT_SRC))

from data_intelligence_api.infrastructure.workflow.pipeline_factory import (  # noqa: E402
    ExampleIntentAnalyzer,
    ExampleEvidenceCollector,
    ExampleSpecBuilder,
    ExampleSpecConfirmation,
    ExampleSynthesizer,
    create_example_pipeline,
    create_report_pipeline,
)

__all__ = [
    "ExampleIntentAnalyzer",
    "ExampleEvidenceCollector",
    "ExampleSpecBuilder",
    "ExampleSpecConfirmation",
    "ExampleSynthesizer",
    "create_example_pipeline",
    "create_report_pipeline",
]
