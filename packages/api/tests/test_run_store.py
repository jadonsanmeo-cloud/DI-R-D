import unittest
from datetime import datetime, timedelta, timezone

from data_intelligence_api.domain.runs import (
    RunConflictError,
    RunExpiredError,
    RunNotFoundError,
)
from data_intelligence_api.infrastructure.persistence.memory.run_repository import (
    InMemoryRunRepository,
)
from data_intelligence_api.infrastructure.persistence.run_store import (
    hash_confirmation_token,
)


class RunStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 14, tzinfo=timezone.utc)
        self.store = InMemoryRunRepository(clock=lambda: self.now)
        self.store.create_pending(
            response_id="resp_1",
            token_hash=hash_confirmation_token("secret"),
            request_payload={"input": "hello"},
            prepared_execution={"query": {"text": "hello"}},
            intent_payload={"value": "reason"},
            spec_payload={"intent": "reason", "objective": "hello"},
            user_id="user-1",
            session_id="session-1",
            expires_at=self.now + timedelta(hours=1),
        )

    def test_token_is_hashed_and_authorized_lookup_returns_current_spec(self) -> None:
        self.assertNotEqual(hash_confirmation_token("secret"), "secret")

        run = self.store.get_authorized("resp_1", "secret")

        self.assertEqual(run.current_revision, 1)
        self.assertEqual(run.spec_payload["objective"], "hello")

    def test_wrong_token_is_not_disclosed(self) -> None:
        with self.assertRaises(RunNotFoundError):
            self.store.get_authorized("resp_1", "wrong")

    def test_claim_rejects_stale_and_concurrent_decisions(self) -> None:
        with self.assertRaises(RunConflictError):
            self.store.claim("resp_1", "secret", revision=2, target_status="revising")

        self.store.claim("resp_1", "secret", revision=1, target_status="revising")
        with self.assertRaises(RunConflictError):
            self.store.claim("resp_1", "secret", revision=1, target_status="executing")

    def test_revision_is_immutable_and_returns_to_awaiting_confirmation(self) -> None:
        self.store.claim("resp_1", "secret", revision=1, target_status="revising")

        run = self.store.save_revision(
            "resp_1",
            previous_revision=1,
            spec_payload={"intent": "reason", "objective": "revised"},
            source="feedback_revision",
            feedback="narrow scope",
            edited_spec=None,
        )

        self.assertEqual(run.current_revision, 2)
        self.assertEqual(run.status, "awaiting_confirmation")
        self.assertEqual(run.spec_payload["objective"], "revised")
        self.assertEqual(self.store.revisions["resp_1"][1]["objective"], "hello")

    def test_expired_run_is_marked_expired(self) -> None:
        self.now += timedelta(hours=2)

        with self.assertRaises(RunExpiredError):
            self.store.get_authorized("resp_1", "secret")

        self.assertEqual(self.store.runs["resp_1"].status, "expired")

    def test_mark_completed_persists_history_output(self) -> None:
        self.store.mark_completed(
            "resp_1",
            output_text="Finished answer",
            evidence={"sources": ["sales.csv"]},
            response_metadata={"engine_name": "general"},
        )

        run = self.store.get_for_session("resp_1", "session-1")

        self.assertEqual(run.status, "completed")
        self.assertEqual(run.output_text, "Finished answer")
        self.assertEqual(run.evidence, {"sources": ["sales.csv"]})
        self.assertEqual(run.response_metadata, {"engine_name": "general"})
        self.assertEqual(run.completed_at, self.now)

    def test_list_for_session_is_scoped_and_newest_first(self) -> None:
        self.now += timedelta(minutes=1)
        self.store.create_pending(
            response_id="resp_2",
            token_hash=hash_confirmation_token("secret-2"),
            request_payload={"input": "newer"},
            prepared_execution={"query": {"text": "newer"}},
            intent_payload={"value": "reason"},
            spec_payload={"intent": "reason", "objective": "newer"},
            user_id="user-1",
            session_id="session-1",
            expires_at=self.now + timedelta(hours=1),
        )
        self.now += timedelta(minutes=1)
        self.store.create_pending(
            response_id="resp_other",
            token_hash=hash_confirmation_token("other"),
            request_payload={"input": "private"},
            prepared_execution={"query": {"text": "private"}},
            intent_payload={"value": "reason"},
            spec_payload={"intent": "reason", "objective": "private"},
            user_id="user-2",
            session_id="session-2",
            expires_at=self.now + timedelta(hours=1),
        )

        runs = self.store.list_for_session("session-1", limit=10)

        self.assertEqual([run.response_id for run in runs], ["resp_2", "resp_1"])

    def test_get_for_session_hides_another_session(self) -> None:
        with self.assertRaises(RunNotFoundError):
            self.store.get_for_session("resp_1", "session-2")

    def test_delete_for_session_removes_matching_run(self) -> None:
        self.store.delete_for_session("resp_1", "session-1")

        with self.assertRaises(RunNotFoundError):
            self.store.get_for_session("resp_1", "session-1")

    def test_delete_for_session_hides_another_session(self) -> None:
        with self.assertRaises(RunNotFoundError):
            self.store.delete_for_session("resp_1", "session-2")

        self.assertEqual(
            self.store.get_for_session("resp_1", "session-1").response_id,
            "resp_1",
        )


if __name__ == "__main__":
    unittest.main()
