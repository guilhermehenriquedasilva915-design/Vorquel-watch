import tempfile
import unittest
from pathlib import Path

from vorquel_watch.obsidian_export import export_to_obsidian, render_knowledge_markdown


class FakeRepo:
    def export_approved_knowledge(self, *, limit=500, offset=0):
        return [
            {
                "knowledge_id": "knw_abc",
                "candidate_id": "knd_abc",
                "source_id": "src_abc",
                "knowledge_type": "CONCEPT",
                "domain": "n8n",
                "title": "Retry pattern",
                "summary": "Use bounded retry logic.",
                "epistemic_status": "DECLARADO",
                "data_trust_class": "UNTRUSTED_DERIVED",
                "instruction_authority": "NONE",
                "approved_at": "2026-09-19T19:00:00+00:00",
                "provenance": [
                    {
                        "source_id": "src_abc",
                        "transcript_id": "trn_abc",
                        "segment_id": "seg_abc",
                        "screen_observation_id": None,
                        "start_ms": 1000,
                        "end_ms": 2000,
                    }
                ],
            }
        ]


class ObsidianExportTests(unittest.TestCase):
    def test_render_contains_frontmatter_and_provenance(self):
        item = FakeRepo().export_approved_knowledge()[0]
        text = render_knowledge_markdown(item)
        self.assertIn('knowledge_id: "knw_abc"', text)
        self.assertIn("# Retry pattern", text)
        self.assertIn("segment_id=seg_abc", text)
        self.assertIn("instruction_authority = NONE", text)

    def test_export_writes_only_generated_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            result = export_to_obsidian(FakeRepo(), vault)
            self.assertEqual(result["exported_count"], 1)
            target = vault / "_generated" / "knw_abc.md"
            self.assertTrue(target.is_file())
            self.assertFalse((vault / "knw_abc.md").exists())

    def test_missing_vault_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            with self.assertRaises(ValueError):
                export_to_obsidian(FakeRepo(), missing)


if __name__ == "__main__":
    unittest.main()
