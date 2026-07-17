"""File-backed workflow for method proposals."""

from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from data_intelligence_sdk.core.errors import DataIntelligenceError

__all__ = [
    "MethodProposal",
    "MethodProposalError",
    "create_proposal",
    "list_proposals",
    "load_proposal",
    "move_proposal",
]

_VALID_STATUSES = ("pending", "accepted", "rejected")
_PROPOSAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class MethodProposalError(DataIntelligenceError):
    """Raised when a proposal cannot be created, loaded, or moved."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized not in _VALID_STATUSES:
        raise MethodProposalError(
            f"Invalid proposal status {status!r}. Expected one of {_VALID_STATUSES!r}."
        )
    return normalized


def _sanitize_proposal_id(proposal_id: str) -> str:
    normalized = proposal_id.strip()
    if not normalized or not _PROPOSAL_ID_PATTERN.match(normalized):
        raise MethodProposalError(
            "Proposal id must contain only letters, digits, dot, underscore, or dash."
        )
    return normalized


def _ensure_json_serializable(
    payload: Mapping[str, Any], source: str
) -> dict[str, Any]:
    try:
        normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    except TypeError as exc:
        raise MethodProposalError(
            f"{source}: proposal payload must be JSON serializable."
        ) from exc
    if not isinstance(normalized, dict):
        raise MethodProposalError(f"{source}: proposal payload must be a JSON object.")
    return normalized


def _root_dir(base_dir: str | Path) -> Path:
    return Path(base_dir).resolve()


def _status_dir(base_dir: str | Path, status: str) -> Path:
    return _root_dir(base_dir) / "proposals" / status


def _proposal_file(base_dir: str | Path, status: str, proposal_id: str) -> Path:
    return _status_dir(base_dir, status) / f"{proposal_id}.json"


def _iter_statuses(status: str | None = None) -> tuple[str, ...]:
    if status is None:
        return _VALID_STATUSES
    return (_normalize_status(status),)


@dataclass(slots=True)
class MethodProposal:
    """A file-backed proposal record."""

    proposal_id: str
    status: str = "pending"
    payload: dict[str, Any] = field(default_factory=dict)
    trust_level: str = "generated_unvalidated"
    title: str = ""
    summary: str = ""
    source: str | None = None
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "method-proposal-v1",
            "proposal_id": self.proposal_id,
            "status": self.status,
            "trust_level": self.trust_level,
            "title": self.title,
            "summary": self.summary,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "payload": deepcopy(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MethodProposal":
        if data.get("schema") != "method-proposal-v1":
            raise MethodProposalError("Unsupported proposal schema.")
        payload = data.get("payload", {})
        if not isinstance(payload, Mapping):
            raise MethodProposalError("Proposal payload must be a JSON object.")
        return cls(
            proposal_id=str(data["proposal_id"]),
            status=_normalize_status(str(data.get("status", "pending"))),
            trust_level=str(data.get("trust_level", "generated_unvalidated")),
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            source=(str(data["source"]) if data.get("source") is not None else None),
            created_at=str(data.get("created_at", _utc_now())),
            updated_at=str(data.get("updated_at", _utc_now())),
            payload=deepcopy(dict(payload)),
        )


def create_proposal(
    base_dir: str | Path,
    payload: Mapping[str, Any],
    *,
    proposal_id: str | None = None,
    title: str = "",
    summary: str = "",
    source: str | None = None,
) -> MethodProposal:
    """Create a pending proposal file."""

    proposal_payload = _ensure_json_serializable(payload, "create_proposal")
    trust_level = str(proposal_payload.get("trust_level", "generated_unvalidated"))
    if trust_level != "generated_unvalidated":
        raise MethodProposalError("Proposal trust_level must be generated_unvalidated.")
    proposal_payload["trust_level"] = "generated_unvalidated"
    proposal_id = _sanitize_proposal_id(proposal_id or uuid.uuid4().hex)
    proposal = MethodProposal(
        proposal_id=proposal_id,
        status="pending",
        payload=proposal_payload,
        trust_level="generated_unvalidated",
        title=title,
        summary=summary,
        source=source,
    )
    path = _proposal_file(base_dir, proposal.status, proposal.proposal_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise MethodProposalError(f"{path}: proposal already exists.")
    path.write_text(
        json.dumps(proposal.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return proposal


def load_proposal(
    base_dir: str | Path,
    proposal_id: str,
    *,
    status: str | None = None,
) -> MethodProposal:
    """Load a proposal by id from one of the workflow status directories."""

    sanitized_id = _sanitize_proposal_id(proposal_id)
    for current_status in _iter_statuses(status):
        path = _proposal_file(base_dir, current_status, sanitized_id)
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise MethodProposalError(
                f"{path}: proposal file must contain a JSON object."
            )
        return MethodProposal.from_dict(payload)
    raise MethodProposalError(f"{sanitized_id}: proposal not found.")


def list_proposals(
    base_dir: str | Path,
    *,
    status: str | None = None,
) -> list[MethodProposal]:
    """List proposals in deterministic order."""

    statuses = _iter_statuses(status)
    proposals: list[MethodProposal] = []
    for current_status in statuses:
        directory = _status_dir(base_dir, current_status)
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise MethodProposalError(
                    f"{path}: proposal file must contain a JSON object."
                )
            proposals.append(MethodProposal.from_dict(payload))
    return proposals


def move_proposal(
    base_dir: str | Path,
    proposal_id: str,
    target_status: str,
) -> MethodProposal:
    """Move a proposal between workflow status directories."""

    sanitized_id = _sanitize_proposal_id(proposal_id)
    target_status = _normalize_status(target_status)
    source_path: Path | None = None
    source_proposal: MethodProposal | None = None
    for current_status in _VALID_STATUSES:
        candidate = _proposal_file(base_dir, current_status, sanitized_id)
        if not candidate.exists():
            continue
        source_path = candidate
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise MethodProposalError(
                f"{candidate}: proposal file must contain a JSON object."
            )
        source_proposal = MethodProposal.from_dict(payload)
        break

    if source_path is None or source_proposal is None:
        raise MethodProposalError(f"{sanitized_id}: proposal not found.")

    target_path = _proposal_file(base_dir, target_status, sanitized_id)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and target_path != source_path:
        raise MethodProposalError(f"{target_path}: proposal already exists.")

    source_proposal.status = target_status
    source_proposal.updated_at = _utc_now()
    target_path.write_text(
        json.dumps(source_proposal.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if source_path != target_path:
        source_path.unlink()
    return source_proposal
