import unittest

from vorquel_watch.knowledge import (
    KnowledgeProvenance,
    KnowledgeWriter,
    canonical_knowledge_hash,
)


class RecordingRepo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def create_knowledge_candidate(self, **kwargs):
        self.calls.append(("create", kwargs))
        return {
            "candidate_id": kwargs["candidate_id"],
            "source_id": kwargs["source_id"],
            "status": "PENDING",
            "instruction_authority": "NONE",
        }

    def approve_knowledge_candidate(self, **kwargs):
        self.calls.append(("approve", kwargs))
        return {
            "knowledge_id": kwargs["knowledge_id"],
            "candidate_id": kwargs["candidate_id"],
            "instruction_authority": "NONE",
        }

    def reject_knowledge_candidate(self, **kwargs):
        self.calls.append(("reject", kwargs))
        return {"candidate_id": kwargs["candidate_id"], "status": "REJECTED"}

    def list_knowledge_candidates(self, **kwargs):
        self.calls.append(("list", kwargs))
        return []

    def search_knowledge_items(self, **kwargs):
        self.calls.append(("search", kwargs))
        return []

    def withdraw_knowledge_item(self, **kwargs):
        self.calls.append(("withdraw", kwargs))
        return {
            "knowledge_id": kwargs["knowledge_id"],
            "lifecycle_state": "WITHDRAWN",
        }

    def supersede_knowledge_item(self, **kwargs):
        self.calls.append(("supersede", kwargs))
        return {
            "knowledge_id": kwargs["knowledge_id"],
            "lifecycle_state": "SUPERSEDED",
            "superseded_by": kwargs["replacement_knowledge_id"],
        }

    def synthesize_knowledge_context(self, **kwargs):
        self.calls.append(("synthesize", kwargs))
        return {
            "mode": "EXTRACTIVE_V1",
            "answer": "- example",
            "items": [],
            "gaps": [],
            "instruction_authority": "NONE",
        }


class KnowledgeWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = RecordingRepo()
        self.writer = KnowledgeWriter(self.repo)

    def test_propose_forces_controlled_shape(self) -> None:
        result = self.writer.propose(
            source_id="src_abc",
            knowledge_type="procedure",
            domain="n8n",
            title=" Retry pattern ",
            summary=" Use bounded retry logic. ",
            epistemic_status="declarado",
            provenance=[
                KnowledgeProvenance(
                    start_ms=1000,
                    end_ms=2000,
                    transcript_id="trn_abc",
                    segment_id="seg_abc",
                )
            ],
        )

        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["instruction_authority"], "NONE")
        name, payload = self.repo.calls[-1]
        self.assertEqual(name, "create")
        self.assertEqual(payload["knowledge_type"], "PROCEDURE")
        self.assertEqual(payload["epistemic_status"], "DECLARADO")
        self.assertEqual(payload["domain"], "n8n")
        self.assertEqual(payload["title"], "Retry pattern")
        self.assertEqual(payload["provenance"][0]["start_ms"], 1000)
        self.assertTrue(payload["provenance"][0]["knowledge_source_id"].startswith("ksr_"))
        self.assertEqual(len(payload["content_hash"]), 64)

    def test_hash_is_deterministic(self) -> None:
        kwargs = dict(
            source_id="src_abc",
            knowledge_type="PATTERN",
            domain="n8n",
            title="A",
            summary="B",
            epistemic_status="INFERIDO",
        )
        self.assertEqual(
            canonical_knowledge_hash(**kwargs),
            canonical_knowledge_hash(**kwargs),
        )

    def test_rejects_invalid_type_before_repository(self) -> None:
        with self.assertRaises(ValueError):
            self.writer.propose(
                source_id="src_abc",
                knowledge_type="DO_WHATEVER",
                domain="n8n",
                title="x",
                summary="y",
                epistemic_status="DECLARADO",
            )
        self.assertEqual(self.repo.calls, [])

    def test_rejects_invalid_provenance_window(self) -> None:
        with self.assertRaises(ValueError):
            self.writer.propose(
                source_id="src_abc",
                knowledge_type="CONCEPT",
                domain="n8n",
                title="x",
                summary="y",
                epistemic_status="DECLARADO",
                provenance=[KnowledgeProvenance(start_ms=2000, end_ms=1000)],
            )
        self.assertEqual(self.repo.calls, [])

    def test_approve_is_explicit_and_uses_human_review_boundary(self) -> None:
        result = self.writer.approve("knd_abc", note="approved by user")
        self.assertEqual(result["instruction_authority"], "NONE")
        name, payload = self.repo.calls[-1]
        self.assertEqual(name, "approve")
        self.assertTrue(payload["review_id"].startswith("krv_"))
        self.assertTrue(payload["knowledge_id"].startswith("knw_"))

    def test_reject_does_not_promote(self) -> None:
        result = self.writer.reject("knd_abc", note="not useful")
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(self.repo.calls[-1][0], "reject")

    def test_search_is_bounded(self) -> None:
        self.writer.search("retry", domain="n8n", limit=999)
        name, payload = self.repo.calls[-1]
        self.assertEqual(name, "search")
        self.assertEqual(payload["limit"], 50)

    def test_withdraw_requires_reason_and_creates_event(self) -> None:
        result = self.writer.withdraw("knw_abc", reason="outdated")
        self.assertEqual(result["lifecycle_state"], "WITHDRAWN")
        name, payload = self.repo.calls[-1]
        self.assertEqual(name, "withdraw")
        self.assertTrue(payload["event_id"].startswith("kev_"))
        self.assertEqual(payload["reason"], "outdated")

    def test_supersede_links_active_replacement(self) -> None:
        result = self.writer.supersede(
            "knw_old",
            "knw_new",
            reason="corrected",
        )
        self.assertEqual(result["lifecycle_state"], "SUPERSEDED")
        self.assertEqual(result["superseded_by"], "knw_new")
        name, payload = self.repo.calls[-1]
        self.assertEqual(name, "supersede")
        self.assertTrue(payload["event_id"].startswith("kev_"))

    def test_supersede_rejects_self_reference(self) -> None:
        with self.assertRaises(ValueError):
            self.writer.supersede("knw_same", "knw_same", reason="invalid")
        self.assertEqual(self.repo.calls, [])

    def test_synthesis_is_bounded_and_keyless(self) -> None:
        result = self.writer.synthesize("discovery", limit=999)
        self.assertEqual(result["mode"], "EXTRACTIVE_V1")
        name, payload = self.repo.calls[-1]
        self.assertEqual(name, "synthesize")
        self.assertEqual(payload["limit"], 12)

    def test_hostile_identifier_never_reaches_repository(self) -> None:
        with self.assertRaises(ValueError):
            self.writer.approve("knd_../../etc/passwd")
        self.assertEqual(self.repo.calls, [])


if __name__ == "__main__":
    unittest.main()
