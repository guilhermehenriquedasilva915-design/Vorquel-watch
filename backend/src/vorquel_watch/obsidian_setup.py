from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any


PLUGIN_ID = "new-3d-graph"
PRESET_VERSION = 1

VORQUEL_GROUPS = [
    {"query": "path:_generated/_hubs", "color": "#8b5cf6"},
    {"query": "tag:#domain/discovery-vendas", "color": "#22c55e"},
    {"query": "tag:#domain/vendas-b2b", "color": "#3b82f6"},
    {"query": "tag:#domain/vendas-enterprise", "color": "#06b6d4"},
    {"query": "tag:#domain/posicionamento", "color": "#f59e0b"},
    {"query": "tag:#type/procedure", "color": "#eab308"},
    {"query": "tag:#type/anti_pattern", "color": "#ef4444"},
    {"query": "tag:#type/pattern", "color": "#a855f7"},
    {"query": "path:Prospects", "color": "#ec4899"},
    {"query": "path:Decisions", "color": "#f8fafc"},
    {"query": "path:Evidence", "color": "#fb923c"},
    {"query": "path:Sources", "color": "#94a3b8"},
    {"query": "path:Knowledge/Imobiliarias", "color": "#60a5fa"},
    {"query": "path:Knowledge/IA", "color": "#c084fc"},
    {"query": "path:Knowledge/n8n", "color": "#4ade80"},
]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON file: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in: {path.name}")
    return value


def setup_obsidian_graph(vault_path: str | Path) -> dict[str, Any]:
    vault = Path(vault_path).expanduser().resolve()
    if not vault.exists() or not vault.is_dir():
        raise ValueError("vault path does not exist or is not a directory")

    obsidian_dir = (vault / ".obsidian").resolve()
    if not obsidian_dir.is_dir():
        raise ValueError("vault has no .obsidian directory")

    plugin_dir = (obsidian_dir / "plugins" / PLUGIN_ID).resolve()
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("New 3D Graph plugin is not installed in this vault")

    manifest = _read_json(manifest_path)
    if manifest.get("id") != PLUGIN_ID:
        raise ValueError("unexpected New 3D Graph plugin id")

    data_path = plugin_dir / "data.json"
    current = _read_json(data_path)

    backup_path = plugin_dir / "data.json.vorquel-backup"
    backup_created = False
    if data_path.is_file() and not backup_path.exists():
        shutil.copy2(data_path, backup_path)
        backup_created = True

    # Preserve unknown plugin fields and only set the fields Vorquel owns.
    current.update(
        {
            "searchQuery": "",
            "showNeighboringNodes": True,
            "filters": [],
            "showAttachments": False,
            "hideOrphans": True,
            "showTags": False,
            "groups": VORQUEL_GROUPS,
            "useThemeColors": False,
            "colorNode": "#cbd5e1",
            "colorTag": "#a78bfa",
            "colorAttachment": "#64748b",
            "colorLink": "#4c4f69",
            "colorHighlight": "#f8fafc",
            "backgroundColor": "#0b0714",
            "nodeSize": 1.35,
            "tagNodeSize": 0.9,
            "attachmentNodeSize": 0.9,
            "linkThickness": 0.7,
            "nodeShape": "Sphere",
            "tagShape": "Tetrahedron",
            "attachmentShape": "Cube",
            "showNodeLabels": True,
            "showLabelsOnHoverOnly": True,
            "labelDistance": 180,
            "labelFadeThreshold": 0.7,
            "labelTextSize": 2.5,
            "labelTextColorDark": "#f8fafc",
            "labelTextColorLight": "#111827",
            "labelBackgroundColor": "#0b0714",
            "labelBackgroundOpacity": 0.45,
            "labelOcclusion": True,
            "useKeyboardControls": True,
            "keyboardMoveSpeed": 2.0,
            "zoomOnClick": True,
            "rotateSpeed": 0.8,
            "panSpeed": 0.8,
            "zoomSpeed": 1.0,
            "performanceMode": False,
            "centerForce": 0.08,
            "repelForce": 12,
            "linkForce": 0.012,
            "useWasmPhysics": True,
            "vorquelPresetVersion": PRESET_VERSION,
        }
    )

    data_path.write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    return {
        "vault": str(vault),
        "plugin_id": PLUGIN_ID,
        "plugin_version": manifest.get("version"),
        "data_path": str(data_path),
        "backup_path": str(backup_path) if backup_path.exists() else None,
        "backup_created": backup_created,
        "groups_configured": len(VORQUEL_GROUPS),
        "hide_orphans": True,
        "show_tags": False,
        "preset_version": PRESET_VERSION,
        "restart_obsidian_required": True,
    }
