"""Durable state boundary for paused Responses workflows."""

from __future__ import annotations

import copy
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Callable

import psycopg
from psycopg.rows import dict_row

from data_intelligence_api.application.ports.run_repository import RunRepository
from data_intelligence_api.domain.runs import (
    RunConflictError,
    RunExpiredError,
    RunNotFoundError,
    RunStoreError,
    StoredRun,
)

RunStore = RunRepository


def hash_confirmation_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _copy_run(run: StoredRun) -> StoredRun:
    return copy.deepcopy(run)


class InMemoryRunRepository:
    """Deterministic repository used by tests and local callers."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.runs: dict[str, StoredRun] = {}
        self.revisions: dict[str, dict[int, dict]] = {}
        self.decisions: list[dict] = []

    def create_pending(self, **kwargs) -> StoredRun:
        response_id = kwargs["response_id"]
        run = StoredRun(
            response_id=response_id,
            status="awaiting_confirmation",
            current_revision=1,
            token_hash=kwargs["token_hash"],
            request_payload=copy.deepcopy(kwargs["request_payload"]),
            prepared_execution=copy.deepcopy(kwargs["prepared_execution"]),
            intent_payload=copy.deepcopy(kwargs["intent_payload"]),
            spec_payload=copy.deepcopy(kwargs["spec_payload"]),
            user_id=kwargs.get("user_id"),
            session_id=kwargs.get("session_id"),
            expires_at=kwargs["expires_at"],
        )
        self.runs[response_id] = run
        self.revisions[response_id] = {1: copy.deepcopy(run.spec_payload)}
        return _copy_run(run)

    def _authorized(self, response_id: str, token: str) -> StoredRun:
        run = self.runs.get(response_id)
        if run is None or not hmac.compare_digest(
            run.token_hash, hash_confirmation_token(token)
        ):
            raise RunNotFoundError("Response was not found.")
        if run.expires_at <= self.clock() and run.status == "awaiting_confirmation":
            run.status = "expired"
        if run.status == "expired":
            raise RunExpiredError("Response confirmation has expired.")
        return run

    def get_authorized(self, response_id: str, token: str) -> StoredRun:
        return _copy_run(self._authorized(response_id, token))

    def claim(
        self,
        response_id: str,
        token: str,
        *,
        revision: int,
        target_status: Literal["revising", "executing"],
    ) -> StoredRun:
        run = self._authorized(response_id, token)
        if run.status != "awaiting_confirmation" or run.current_revision != revision:
            raise RunConflictError("Response revision is stale or already processing.")
        run.status = target_status
        return _copy_run(run)

    def save_revision(
        self,
        response_id: str,
        *,
        previous_revision: int,
        spec_payload: dict,
        source: str,
        feedback: str | None,
        edited_spec: dict | None,
    ) -> StoredRun:
        run = self.runs[response_id]
        if run.status != "revising" or run.current_revision != previous_revision:
            raise RunConflictError("Response is not ready to save a revision.")
        revision = previous_revision + 1
        self.revisions[response_id][revision] = copy.deepcopy(spec_payload)
        self.decisions.append(
            {
                "response_id": response_id,
                "revision": previous_revision,
                "action": "revise",
                "feedback": feedback,
                "edited_spec": copy.deepcopy(edited_spec),
                "source": source,
            }
        )
        run.current_revision = revision
        run.spec_payload = copy.deepcopy(spec_payload)
        run.status = "awaiting_confirmation"
        return _copy_run(run)

    def record_confirmation(self, response_id: str, revision: int) -> None:
        self.decisions.append(
            {"response_id": response_id, "revision": revision, "action": "confirm"}
        )

    def mark_completed(self, response_id: str) -> None:
        self.runs[response_id].status = "completed"

    def mark_failed(self, response_id: str, code: str, message: str) -> None:
        run = self.runs[response_id]
        run.status = "failed"
        run.error_code = code
        run.error_message = message

    def check_ready(self) -> bool:
        return True


class PostgresRunRepository:
    """Postgres repository used by the API and workers."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def create_pending(self, **kwargs) -> StoredRun:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO response_runs (
                    response_id, status, current_revision,
                    confirmation_token_hash, request_payload,
                    prepared_execution, intent_payload, user_id, session_id,
                    expires_at
                ) VALUES (%s, 'awaiting_confirmation', 1, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    kwargs["response_id"],
                    kwargs["token_hash"],
                    psycopg.types.json.Jsonb(kwargs["request_payload"]),
                    psycopg.types.json.Jsonb(kwargs["prepared_execution"]),
                    psycopg.types.json.Jsonb(kwargs["intent_payload"]),
                    kwargs.get("user_id"),
                    kwargs.get("session_id"),
                    kwargs["expires_at"],
                ),
            )
            cursor.execute(
                """
                INSERT INTO response_spec_revisions
                    (response_id, revision, spec_payload, source)
                VALUES (%s, 1, %s, 'initial')
                """,
                (
                    kwargs["response_id"],
                    psycopg.types.json.Jsonb(kwargs["spec_payload"]),
                ),
            )
        return self.get_authorized_by_hash(kwargs["response_id"], kwargs["token_hash"])

    def _row_to_run(self, row: dict) -> StoredRun:
        return StoredRun(
            response_id=row["response_id"],
            status=row["status"],
            current_revision=row["current_revision"],
            token_hash=row["confirmation_token_hash"],
            request_payload=row["request_payload"],
            prepared_execution=row["prepared_execution"],
            intent_payload=row["intent_payload"],
            spec_payload=row["spec_payload"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            expires_at=row["expires_at"],
            error_code=row["error_code"],
            error_message=row["error_message"],
        )

    def get_authorized_by_hash(self, response_id: str, token_hash: str) -> StoredRun:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.*, s.spec_payload
                FROM response_runs r
                JOIN response_spec_revisions s
                  ON s.response_id = r.response_id
                 AND s.revision = r.current_revision
                WHERE r.response_id = %s
                """,
                (response_id,),
            )
            row = cursor.fetchone()
            if row is None or not hmac.compare_digest(
                row["confirmation_token_hash"], token_hash
            ):
                raise RunNotFoundError("Response was not found.")
            if row["expires_at"] <= datetime.now(timezone.utc) and row["status"] == "awaiting_confirmation":
                cursor.execute(
                    "UPDATE response_runs SET status = 'expired', updated_at = now() WHERE response_id = %s",
                    (response_id,),
                )
                raise RunExpiredError("Response confirmation has expired.")
            if row["status"] == "expired":
                raise RunExpiredError("Response confirmation has expired.")
            return self._row_to_run(row)

    def get_authorized(self, response_id: str, token: str) -> StoredRun:
        return self.get_authorized_by_hash(response_id, hash_confirmation_token(token))

    def claim(self, response_id, token, *, revision, target_status):
        self.get_authorized(response_id, token)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE response_runs
                SET status = %s, updated_at = now(),
                    started_execution_at = CASE WHEN %s = 'executing' THEN now() ELSE started_execution_at END
                WHERE response_id = %s
                  AND status = 'awaiting_confirmation'
                  AND current_revision = %s
                  AND expires_at > now()
                """,
                (target_status, target_status, response_id, revision),
            )
            if cursor.rowcount != 1:
                raise RunConflictError("Response revision is stale or already processing.")
        return self.get_authorized(response_id, token)

    def save_revision(self, response_id: str, **kwargs) -> StoredRun:
        revision = kwargs["previous_revision"] + 1
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO response_decisions
                    (response_id, revision, action, feedback, edited_spec)
                VALUES (%s, %s, 'revise', %s, %s)
                """,
                (
                    response_id,
                    kwargs["previous_revision"],
                    kwargs["feedback"],
                    psycopg.types.json.Jsonb(kwargs["edited_spec"])
                    if kwargs["edited_spec"] is not None
                    else None,
                ),
            )
            cursor.execute(
                """
                INSERT INTO response_spec_revisions
                    (response_id, revision, spec_payload, source)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    response_id,
                    revision,
                    psycopg.types.json.Jsonb(kwargs["spec_payload"]),
                    kwargs["source"],
                ),
            )
            cursor.execute(
                """
                UPDATE response_runs
                SET status = 'awaiting_confirmation', current_revision = %s, updated_at = now()
                WHERE response_id = %s AND status = 'revising' AND current_revision = %s
                """,
                (revision, response_id, kwargs["previous_revision"]),
            )
            if cursor.rowcount != 1:
                raise RunConflictError("Response is not ready to save a revision.")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT confirmation_token_hash FROM response_runs WHERE response_id = %s",
                (response_id,),
            )
            token_hash = cursor.fetchone()["confirmation_token_hash"]
        return self.get_authorized_by_hash(response_id, token_hash)

    def record_confirmation(self, response_id: str, revision: int) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO response_decisions (response_id, revision, action) VALUES (%s, %s, 'confirm')",
                (response_id, revision),
            )

    def mark_completed(self, response_id: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE response_runs SET status = 'completed', completed_at = now(), updated_at = now() WHERE response_id = %s",
                (response_id,),
            )

    def mark_failed(self, response_id: str, code: str, message: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE response_runs
                SET status = 'failed', error_code = %s, error_message = %s, updated_at = now()
                WHERE response_id = %s
                """,
                (code, message, response_id),
            )

    def check_ready(self) -> bool:
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() is not None
        except psycopg.Error:
            return False


# Compatibility aliases for callers that used the original store names.
InMemoryRunStore = InMemoryRunRepository
PostgresRunStore = PostgresRunRepository
