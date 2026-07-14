"""Compatibility entry point for the application-owned example pipeline."""

from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
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
