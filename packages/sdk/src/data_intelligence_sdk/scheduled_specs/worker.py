"""One-cycle orchestration for scheduled report specs."""

from __future__ import annotations

from dataclasses import dataclass

from data_intelligence_sdk.runtime.logger import RuntimeLogger
from data_intelligence_sdk.scheduled_specs.contracts import (
    RecentDocumentSource,
    ScheduledSpecStore,
)


@dataclass(frozen=True, slots=True)
class ScheduledSpecCycleResult:
    loaded: int
    created: int
    skipped: int
    failed: int


class RecentDocumentSpecWorker:
    def __init__(
        self,
        *,
        source: RecentDocumentSource,
        prompt: object,
        store: ScheduledSpecStore,
        organization_id: str,
        limit: int = 3,
        logger: RuntimeLogger | None = None,
    ) -> None:
        if limit <= 0:
            raise ValueError("Scheduled spec limit must be positive.")
        if not organization_id.strip():
            raise ValueError("Organization ID is required.")
        self.source = source
        self.prompt = prompt
        self.store = store
        self.organization_id = organization_id
        self.limit = limit
        self.logger = logger

    def run_cycle(self) -> ScheduledSpecCycleResult:
        self._log(
            "scheduled_spec.cycle.started",
            {"organization_id": self.organization_id, "limit": self.limit},
        )
        documents = self.source.load_recent(
            organization_id=self.organization_id,
            limit=self.limit,
        )
        self._log("scheduled_spec.documents.loaded", {"document_count": len(documents)})

        created = skipped = failed = 0
        for document in sorted(
            documents,
            key=lambda item: (item.ingested_at, item.document_id),
        ):
            if self.store.exists(document.document_id):
                skipped += 1
                self._log(
                    "scheduled_spec.document.skipped",
                    {"document_id": document.document_id, "reason": "spec_exists"},
                )
                continue
            try:
                markdown = self.prompt.render(document)  # type: ignore[attr-defined]
                destination = self.store.create(document.document_id, markdown)
            except FileExistsError:
                skipped += 1
                self._log(
                    "scheduled_spec.document.skipped",
                    {"document_id": document.document_id, "reason": "spec_exists"},
                )
            except Exception as exc:
                failed += 1
                self._log(
                    "scheduled_spec.document.failed",
                    {
                        "document_id": document.document_id,
                        "error_type": type(exc).__name__,
                    },
                )
            else:
                created += 1
                self._log(
                    "scheduled_spec.document.created",
                    {
                        "document_id": document.document_id,
                        "output_path": str(destination),
                    },
                )

        result = ScheduledSpecCycleResult(
            loaded=len(documents),
            created=created,
            skipped=skipped,
            failed=failed,
        )
        self._log(
            "scheduled_spec.cycle.completed",
            {
                "loaded_count": result.loaded,
                "created_count": result.created,
                "skipped_count": result.skipped,
                "failed_count": result.failed,
            },
        )
        return result

    def _log(self, event: str, payload: dict[str, object]) -> None:
        if self.logger is not None:
            self.logger.log(event, payload)
