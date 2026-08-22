"""Prompt template for selecting a prepared cluster execution spec."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, TYPE_CHECKING, cast

from data_intelligence_sdk.core.types import (
    ExecutionSpec,
    Intent,
    SessionContext,
    UploadedFile,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.datahub import DataHubClusteringResult

if TYPE_CHECKING:
    from data_intelligence_sdk.spec.cluster_specs import ClusterExecutionSpec


class ClusterSpecSelectorPrompt:
    """Builds chat messages for cluster spec selection."""

    def select_messages(
        self,
        *,
        query: UserQuery,
        intent: Intent,
        uploaded_files: list[UploadedFile],
        clustering_result: DataHubClusteringResult,
        cluster_specs: list["ClusterExecutionSpec"],
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
        previous_spec: ExecutionSpec | None = None,
        user_feedback: str | None = None,
    ) -> list[dict[str, str]]:
        payload: dict[str, Any] = {
            "query": to_jsonable(query),
            "intent": intent,
            "uploaded_files": to_jsonable(uploaded_files),
            "clustering_result": to_jsonable(clustering_result),
            "cluster_specs": to_jsonable(cluster_specs),
            "session_context": to_jsonable(session_context),
            "user_context": to_jsonable(user_context),
            "previous_spec": to_jsonable(previous_spec),
            "user_feedback": user_feedback,
        }
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=True, sort_keys=True),
            },
        ]


_SYSTEM_PROMPT = """You are the Cluster Spec Selector agent.

Select exactly one prepared cluster execution spec. The system now selects at
cluster level, not file level. Do not add or remove individual files.

Selection rules:
- If user_feedback is present, treat it as the latest instruction and use it to
  reselect a cluster from the prepared cluster_specs.
- If query.text is empty or vague, use user_context preferences and history,
  then session_context, to choose the best cluster spec.
- If query.text is specific, choose the cluster whose description, members,
  relationships, or suggested_tasks best match it.
- previous_spec explains what is being revised, but user_feedback is newer.
- Return only JSON.

JSON contract:
{
  "cluster_id": "one cluster_id from cluster_specs",
  "reason": "why this cluster was selected",
  "confidence": 0.0
}
"""


def to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(cast(Any, value))
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    return value
