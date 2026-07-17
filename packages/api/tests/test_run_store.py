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


if __name__ == "__main__":
    unittest.main()
