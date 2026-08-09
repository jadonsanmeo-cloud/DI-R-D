"""Internal report engine implementation module."""

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
from data_intelligence_sdk.runtime.config import ConfigManager, get_config_manager
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.sandbox.executor import SandboxRunResult
from data_intelligence_sdk.tools import create_mcp_tools

from data_intelligence_sdk.engines.reporting.utils import (
    _extract_message_content,
    _json_dumps,
    _parse_json_payload,
)

class _PromptAgent:
    def __init__(self, name: str, system_prompt: str, llm: object | None) -> None:
        self.name = name
        self.system_prompt = system_prompt
        self.llm = llm

    def _invoke_text_with_prompt(self, system_prompt: str, **inputs: Any) -> str | None:
        if self.llm is None or not hasattr(self.llm, "invoke"):
            return None
        try:
            prompt = ChatPromptTemplate.from_messages(
                [
                    SystemMessage(content=system_prompt),
                    ("user", "\n\n".join(f"{key}:\n{{{key}}}" for key in inputs)),
                ]
            )
            values = {
                key: _json_dumps(value) if isinstance(value, (dict, list)) else value
                for key, value in inputs.items()
            }
            return _extract_message_content(self.llm.invoke(prompt.invoke(values)))
        except Exception:
            return None

    def _invoke_text(self, **inputs: Any) -> str | None:
        return self._invoke_text_with_prompt(self.system_prompt, **inputs)

    def _invoke_json_with_prompt(self, system_prompt: str, **inputs: Any) -> Any | None:
        text = self._invoke_text_with_prompt(system_prompt, **inputs)
        if text is None:
            return None
        try:
            return _parse_json_payload(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _invoke_json(self, **inputs: Any) -> Any | None:
        return self._invoke_json_with_prompt(self.system_prompt, **inputs)
