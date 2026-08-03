"""Read durable artifact events for response-history replay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from data_intelligence_sdk.runtime.event_payload import runtime_event_payload
from data_intelligence_sdk.sandbox.artifacts import normalize_artifact_run_id

_STAGE_EVENT_TYPES = {
    "run.created": "pipeline.start",
    "intent.analyzed": "pipeline.intent_analyzed",
    "spec.built": "pipeline.spec_built",
    "spec.revised": "pipeline.spec_revised",
    "spec.confirmed": "pipeline.spec_confirmed",
    "engine.selected": "pipeline.engine_selected",
}
_SKIPPED_EVENT_TYPES = {
    "corpus.registered",
    "query.received",
    "response.completed",
    "run.completed",
}
_MAX_CODE_STRING = 65_536


class ArtifactHistoryReader:
    """Convert a run's JSONL audit trail into replayable pipeline events."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def read_pipeline_events(
        self,
        artifact_ref: object,
        *,
        evidence_present: bool,
    ) -> list[dict[str, Any]]:
        events = self._read_events(artifact_ref)
        code_artifacts = self._read_code_artifacts(artifact_ref)
        code_artifacts_by_ref = {
            str(code.get("artifact_ref")): code
            for code in code_artifacts
            if code.get("artifact_ref")
        }
        timeline: list[dict[str, Any]] = []
        for event in events:
            event_type = str(event.get("event_type") or "")
            stage_type = _STAGE_EVENT_TYPES.get(event_type)
            if stage_type is not None:
                stage_event = {
                    "type": stage_type,
                    "event_id": event.get("event_id"),
                    "sequence": event.get("sequence"),
                    "status": event.get("status", "completed"),
                }
                payload = event.get("payload")
                if isinstance(payload, dict):
                    if stage_type == "pipeline.intent_analyzed":
                        for key in (
                            "intent",
                            "catalog_intent",
                            "confidence",
                            "score",
                            "source",
                        ):
                            if payload.get(key) is not None:
                                stage_event[key] = payload[key]
                    elif stage_type == "pipeline.engine_selected":
                        stage_event["engine_name"] = payload.get("engine_name")
                if stage_type == "pipeline.start":
                    stage_event["artifact_ref"] = str(artifact_ref)
                timeline.append(stage_event)
                continue
            if event_type in _SKIPPED_EVENT_TYPES:
                continue
            timeline.append(
                {
                    "type": "pipeline.runtime_event",
                    **runtime_event_payload(event),
                }
            )

        for event in timeline:
            if event.get("type") != "pipeline.runtime_event" or event.get("code"):
                continue
            details = event.get("details")
            if not isinstance(details, dict):
                continue
            inputs = details.get("inputs")
            if not isinstance(inputs, dict):
                continue
            code_ref = inputs.get("code_artifact_ref")
            code = code_artifacts_by_ref.get(str(code_ref))
            if code is not None:
                event["code"] = code

        missing_code_indexes = [
            index
            for index, event in enumerate(timeline)
            if event.get("type") == "pipeline.runtime_event"
            and event.get("name") == "code_agent"
            and event.get("code") is None
        ]
        for index, code in zip(
            reversed(missing_code_indexes),
            reversed(code_artifacts),
        ):
            timeline[index]["code"] = code

        if events:
            timeline.append(
                {
                    "type": "pipeline.engine_completed",
                    "status": "completed",
                }
            )
            if evidence_present:
                timeline.append(
                    {
                        "type": "pipeline.evidence_collected",
                        "status": "completed",
                    }
                )
            timeline.append(
                {
                    "type": "pipeline.completed",
                    "status": "completed",
                }
            )
        return timeline

    def _read_events(self, artifact_ref: object) -> list[dict[str, Any]]:
        run_id = self._run_id(artifact_ref)
        if run_id is None:
            return []
        events_path = (self.root / run_id / "events.jsonl").resolve()
        if events_path.parent.parent != self.root or not events_path.is_file():
            return []

        events: list[dict[str, Any]] = []
        try:
            with events_path.open("r", encoding="utf-8") as event_file:
                for line in event_file:
                    if len(events) >= 1_000:
                        break
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and str(event.get("run_id")) == run_id:
                        events.append(event)
        except OSError:
            return []
        return events

    def _read_code_artifacts(self, artifact_ref: object) -> list[dict[str, Any]]:
        run_id = self._run_id(artifact_ref)
        if run_id is None:
            return []
        run_root = (self.root / run_id).resolve()
        manifest_path = run_root / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        attempts = manifest.get("attempts")
        if not isinstance(attempts, list):
            return []

        code_artifacts: list[dict[str, Any]] = []
        for item in attempts:
            if not isinstance(item, dict):
                continue
            code_ref = item.get("code_artifact_ref")
            parsed = urlparse(str(code_ref or ""))
            if parsed.scheme != "artifact" or parsed.netloc != run_id:
                continue
            relative_path = parsed.path.lstrip("/")
            if not relative_path.startswith("code/"):
                continue
            code_path = (run_root / relative_path).resolve()
            if code_path.parent != run_root / "code" or not code_path.is_file():
                continue
            try:
                source = code_path.read_text(encoding="utf-8")
            except OSError:
                continue
            content = source[:_MAX_CODE_STRING]
            code_artifacts.append(
                {
                    "name": code_path.name,
                    "language": "python",
                    "content": content,
                    "truncated": len(content) < len(source),
                    "artifact_ref": str(code_ref),
                }
            )
        return code_artifacts

    @staticmethod
    def _run_id(artifact_ref: object) -> str | None:
        parsed = urlparse(str(artifact_ref or ""))
        if parsed.scheme != "artifact" or parsed.path not in {"", "/"}:
            return None
        try:
            return normalize_artifact_run_id(parsed.netloc)
        except (TypeError, ValueError, AttributeError):
            return None
