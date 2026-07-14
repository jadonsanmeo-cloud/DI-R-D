from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from data_intelligence_sdk.runtime.method_proposals import (
    MethodProposalError,
    create_proposal,
    list_proposals,
    load_proposal,
    move_proposal,
)


class MethodProposalTests(unittest.TestCase):
    def test_create_list_load_and_move_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            first = create_proposal(
                base_dir,
                {"name": "scan_csv", "capability_names": ["scan_csv"]},
                proposal_id="proposal-a",
                title="Scan CSV",
                summary="Expose scan_csv as a draft capability.",
            )
            second = create_proposal(
                base_dir,
                {"name": "filter_csv", "capability_names": ["filter_csv"]},
                proposal_id="proposal-b",
                title="Filter CSV",
                summary="Expose filter_csv as a draft capability.",
            )

            listed = list_proposals(base_dir)
            loaded = load_proposal(base_dir, "proposal-a")
            moved = move_proposal(base_dir, "proposal-a", "accepted")
            accepted = list_proposals(base_dir, status="accepted")

        self.assertEqual(first.status, "pending")
        self.assertEqual(second.status, "pending")
        self.assertEqual([proposal.proposal_id for proposal in listed], ["proposal-a", "proposal-b"])
        self.assertEqual(loaded.payload["name"], "scan_csv")
        self.assertEqual(moved.status, "accepted")
        self.assertEqual([proposal.proposal_id for proposal in accepted], ["proposal-a"])

    def test_create_proposal_rejects_invalid_payloads_and_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            with self.assertRaises(MethodProposalError):
                create_proposal(
                    base_dir,
                    {"name": "bad", "trust_level": "builtin"},
                    proposal_id="proposal-invalid-trust",
                )
            with self.assertRaises(MethodProposalError):
                create_proposal(
                    base_dir,
                    {"name": "bad", "capability_names": ["bad"]},
                    proposal_id="../hack",
                )


if __name__ == "__main__":
    unittest.main()
