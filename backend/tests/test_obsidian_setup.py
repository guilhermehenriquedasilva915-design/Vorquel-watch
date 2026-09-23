import json
import tempfile
import unittest
from pathlib import Path

from vorquel_watch.obsidian_setup import setup_obsidian_graph


class ObsidianSetupTests(unittest.TestCase):
    def _make_vault(self, tmp: str) -> Path:
        vault = Path(tmp) / "vault"
        plugin = vault / ".obsidian" / "plugins" / "new-3d-graph"
        plugin.mkdir(parents=True)
        (plugin / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "new-3d-graph",
                    "name": "New 3D Graph",
                    "version": "2.8.1",
                }
            ),
            encoding="utf-8",
        )
        return vault

    def test_setup_creates_backup_and_vorquel_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._make_vault(tmp)
            data = vault / ".obsidian" / "plugins" / "new-3d-graph" / "data.json"
            data.write_text(
                json.dumps({"customField": "keep-me", "hideOrphans": False}),
                encoding="utf-8",
            )

            result = setup_obsidian_graph(vault)

            self.assertTrue(result["backup_created"])
            self.assertEqual(result["plugin_version"], "2.8.1")
            configured = json.loads(data.read_text(encoding="utf-8"))
            self.assertEqual(configured["customField"], "keep-me")
            self.assertTrue(configured["hideOrphans"])
            self.assertFalse(configured["showTags"])
            self.assertGreaterEqual(len(configured["groups"]), 10)
            self.assertEqual(configured["vorquelPresetVersion"], 1)
            self.assertTrue(
                (data.parent / "data.json.vorquel-backup").is_file()
            )

    def test_second_setup_does_not_replace_original_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._make_vault(tmp)
            data = vault / ".obsidian" / "plugins" / "new-3d-graph" / "data.json"
            data.write_text(json.dumps({"nodeSize": 1.1}), encoding="utf-8")
            setup_obsidian_graph(vault)
            backup = data.parent / "data.json.vorquel-backup"
            first = backup.read_text(encoding="utf-8")

            setup_obsidian_graph(vault)

            self.assertEqual(backup.read_text(encoding="utf-8"), first)

    def test_missing_plugin_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / ".obsidian").mkdir(parents=True)
            with self.assertRaises(ValueError):
                setup_obsidian_graph(vault)


if __name__ == "__main__":
    unittest.main()
