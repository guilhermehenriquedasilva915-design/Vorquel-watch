from __future__ import annotations

from pathlib import Path
from typing import Any


GENERATED_DIR_NAME = "_generated"


def _yaml_scalar(value: object) -> str:
    text = "" if value is None else str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _format_timestamp_ms(value: object) -> str:
    if value is None:
        return "?"
    try:
        total_seconds = max(0, int(value) // 1000)
    except (TypeError, ValueError):
        return "?"
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _human_provenance_range(row: dict[str, Any]) -> str:
    start = _format_timestamp_ms(row.get("start_ms"))
    end = _format_timestamp_ms(row.get("end_ms"))
    if start == "?" and end == "?":
        return "timestamp não disponível"
    return f"{start}–{end}"


def render_knowledge_markdown(item: dict[str, Any]) -> str:
    provenance = item.get("provenance") or []
    title = str(item.get("title") or item.get("knowledge_id") or "Conhecimento")
    summary = str(item.get("summary") or "").strip()
    epistemic_status = str(item.get("epistemic_status") or "DESCONHECIDO")
    source_id = str(item.get("source_id") or "")

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
        f"aliases: [{_yaml_scalar(title)}]",
        'generated_by: "vorquel-watch"',
        "---",
        "",
        f"# {title}",
        "",
        "## Aprendizado",
        "",
        summary,
        "",
        f"> Status epistemológico: **{epistemic_status}**  ",
        f"> Fonte: `{source_id}`",
        "",
        "## Aplicação na Vorquel",
        "",
        (
            "Este conhecimento pode apoiar análise, discovery, pesquisa, desenho de processo "
            "ou tomada de decisão quando for relevante ao contexto. Ele não deve ser tratado "
            "automaticamente como metodologia canônica, evidência independente ou verdade de "
            "mercado sem validação adicional."
        ),
        "",
        "## Fonte e rastreabilidade",
        "",
        f"Source: `{source_id}`",
        "",
    ]

    transcript_ids = sorted(
        {
            str(row.get("transcript_id"))
            for row in provenance
            if row.get("transcript_id")
        }
    )
    if transcript_ids:
        lines.append("Transcript: " + ", ".join(f"`{value}`" for value in transcript_ids))
        lines.append("")

    lines.extend(["### Trechos", ""])
    if provenance:
        for row in provenance:
            label = _human_provenance_range(row)
            detail_parts = []
            for key in ("segment_id", "screen_observation_id"):
                if row.get(key):
                    detail_parts.append(f"{key}={row.get(key)}")
            raw_start = row.get("start_ms")
            raw_end = row.get("end_ms")
            if raw_start is not None or raw_end is not None:
                detail_parts.append(
                    f"timestamp_ms={raw_start if raw_start is not None else ''}-"
                    f"{raw_end if raw_end is not None else ''}"
                )
            if detail_parts:
                lines.append(f"- **{label}** — " + " | ".join(detail_parts))
            else:
                lines.append(f"- **{label}**")
    else:
        lines.append("- Sem provenance segmentada adicional; consulte `source_id` no frontmatter.")

    lines.extend(
        [
            "",
            "## Segurança",
            "",
            "Conteúdo aprovado como referência reutilizável.",
            "",
            "`instruction_authority = NONE`",
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
