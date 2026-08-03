"""Backward-compatible facade for the split report engine implementation."""

from __future__ import annotations

import ast
import hashlib
import json
import operator
import os
import re
import threading
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineInput,
    EngineOutput,
    ExecutionSpec,
    InterfaceDefinition,
    UserContext,
)
from data_intelligence_sdk.engines.reporting.base import _PromptAgent
from data_intelligence_sdk.engines.reporting.composition import (
    ChartAgent,
    ReportAgent,
)
from data_intelligence_sdk.engines.reporting.engine import (
    ReportEngine,
    _DataStepState,
    _ReportGraphState,
)
from data_intelligence_sdk.engines.reporting.execution import (
    CodeAgent,
    DataScienceAgent,
    RouterAgent,
    SemanticAnalysisAgent,
    ToolExecutor,
    ValidatorAgent,
)
from data_intelligence_sdk.engines.reporting.planning import (
    PlanAgent,
    TemplateAgent,
    TemplatePool,
)
from data_intelligence_sdk.engines.reporting.policies import (
    AnalysisSamplingPolicy,
    ChartPolicy,
    LocalePolicy,
    ReportAssetPolicy,
    ReportFormat,
    ReportFormatRegistry,
    ReportPresentationPolicy,
    SourceHandlerPolicy,
    SourceMaterializationRegistry,
)
from data_intelligence_sdk.engines.reporting.processing import (
    ChartInputAssembler,
    DataScienceProcessor,
)
from data_intelligence_sdk.engines.reporting.prompts import (
    CHART_AGENT_PROMPT,
    CODE_AGENT_PROMPT,
    DATASCIENCE_AGENT_PROMPT,
    GENERATED_TOOL_CAPABILITY,
    PLAN_AGENT_PROMPT,
    REPORT_AGENT_PROMPT,
    ROUTER_AGENT_PROMPT,
    STRUCTURED_REPORT_AGENT_PROMPT,
    TEMPLATE_AGENT_PROMPT,
    TEMPLATE_POOL_PACKAGE,
    VALIDATOR_AGENT_PROMPT,
)
from data_intelligence_sdk.engines.reporting.rendering import ReportRenderer
from data_intelligence_sdk.engines.reporting.utils import (
    _DOWNSTREAM_OWNED_OPERATIONS,
    _STEP_OUTPUT_REF,
    _StepInputResolver,
    _StepOutputRecord,
    _StepOutputRegistry,
    _bind_dependency_inputs,
    _compatible_plan_outputs,
    _dataset_summary,
    _execution_spec_payload,
    _extract_message_content,
    _first_source,
    _first_source_with_suffixes,
    _infer_schema,
    _int_value,
    _is_downstream_owned_step,
    _json_dumps,
    _json_structure,
    _list_value,
    _method_hub_payload,
    _negotiation_hash,
    _normalize_generated_source,
    _normalize_plan_inputs,
    _normalize_plan_outputs,
    _normalize_rows,
    _parse_json_payload,
    _profile_rows,
    _python_argument_name,
    _safe_id,
    _schema_summary,
    _scope_from_spec,
    _scoped_corpus_payload,
    _semantic_role_groups,
    _shape_compatible,
    _source_summary,
    _step_id_from_input_ref,
    _table_columns,
    _to_jsonable,
)
from data_intelligence_sdk.runtime.config import ConfigManager, get_config_manager
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.sandbox.executor import SandboxRunResult
from data_intelligence_sdk.tools import create_mcp_tools

__all__ = [
    "AnalysisSamplingPolicy",
    "ChartAgent",
    "ChartInputAssembler",
    "ChartPolicy",
    "CodeAgent",
    "DataScienceAgent",
    "DataScienceProcessor",
    "PlanAgent",
    "LocalePolicy",
    "ReportAssetPolicy",
    "ReportAgent",
    "ReportEngine",
    "ReportFormat",
    "ReportFormatRegistry",
    "ReportPresentationPolicy",
    "ReportRenderer",
    "RouterAgent",
    "SemanticAnalysisAgent",
    "SourceHandlerPolicy",
    "SourceMaterializationRegistry",
    "TemplateAgent",
    "TemplatePool",
    "ToolExecutor",
    "ValidatorAgent",
]
