from __future__ import annotations

from pathlib import Path
from typing import Any


GENERATED_DIR_NAME = "_generated"


def _yaml_scalar(value: object) -> str:
    text = "" if value is None else str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def render_knowledge_markdown(item: dict[str, Any]) -> str:
    provenance = item.get("provenance") or []
    lines = [
        "---",
        f"knowledge_id: {_yaml_scalar(item.get('knowledge_id'))}",
        f"candidate_id: {_yaml_scalar(item.get('candidate_id'))}",
        f"source_id: {_yaml_scalar(item.get('source_id'))}",
        f"knowledge_type: {_yaml_scalar(item.get('knowledge_type'))}",
        f"domain: {_yaml_scalar(item.get('domain'))}",
        f"epistemic_status: {_yaml_scalar(item.get('epistemic_status'))}",
        f"data_trust_class: {_yaml_scalar(item.get('data_trust_class'))}",
        f"instruction_authority: {_yaml_scalar(item.get('instruction_authority'))}",
        f"approved_at: {_yaml_scalar(item.get('approved_at'))}",
        'generated_by: "vorquel-watch"',
        "---",
        "",
        f"# {item.get('title') or item.get('knowledge_id')}",
        "",
        "## Resumo",
        "",
        str(item.get("summary") or "").strip(),
        "",
        "## Provenance",
        "",
    ]
    if provenance:
        for row in provenance:
            parts = [f"source_id={row.get('source_id')}"]
            for key in ("transcript_id", "segment_id", "screen_observation_id"):
                if row.get(key):
                    parts.append(f"{key}={row.get(key)}")
            start = row.get("start_ms")
            end = row.get("end_ms")
            if start is not None or end is not None:
                parts.append(
                    f"timestamp_ms={start if start is not None else ''}-"
                    f"{end if end is not None else ''}"
                )
            lines.append("- " + " | ".join(parts))
    else:
        lines.append(
            "- Sem provenance segmentada adicional; consulte source_id no frontmatter."
        )

    lines.extend(
        [
            "",
            "## Segurança",
            "",
            "Este arquivo é gerado automaticamente a partir de conhecimento aprovado.",
            "O conteúdo continua sendo dado de referência e possui instruction_authority = NONE.",
            "",
        ]
    )
    return "\n".join(lines)


def export_to_obsidian(repo: Any, vault_path: str | Path) -> dict[str, Any]:
    vault = Path(vault_path).expanduser().resolve()
    if not vault.exists() or not vault.is_dir():
        raise ValueError("vault path does not exist or is not a directory")

    generated = (vault / GENERATED_DIR_NAME).resolve()
    if vault not in generated.parents:
        raise ValueError("generated directory escaped vault")
    generated.mkdir(parents=True, exist_ok=True)

    items = repo.export_approved_knowledge(limit=1000, offset=0)
    written: list[str] = []
    for item in items:
        knowledge_id = str(item.get("knowledge_id") or "")
        if not knowledge_id.startswith("knw_"):
            raise ValueError("invalid knowledge_id returned by repository")
        target = (generated / f"{knowledge_id}.md").resolve()
        if generated not in target.parents:
            raise ValueError("export target escaped generated directory")
        target.write_text(
            render_knowledge_markdown(item),
            encoding="utf-8",
            newline="\n",
        )
        written.append(str(target))

    return {
        "vault": str(vault),
        "generated_dir": str(generated),
        "exported_count": len(written),
        "files": written,
    }
