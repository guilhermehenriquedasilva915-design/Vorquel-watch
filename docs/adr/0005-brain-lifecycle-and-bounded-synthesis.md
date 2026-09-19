# ADR 0005 — Brain V1.2 lifecycle and bounded synthesis

Status: Accepted — 2026-09-19

## Context

Vorquel Brain V1.1 can promote reviewed candidates into reusable knowledge and
retrieve them with PostgreSQL FTS. After reviewing Garry Tan's public GBrain
architecture, three missing memory operations were identified as useful now
without expanding into autonomous enrichment, vector search, or a 24/7 dream
cycle:

1. withdrawal/correction of active knowledge while preserving history;
2. explicit temporal supersession;
3. synthesis with citations/provenance and explicit gaps.

The Vorquel epistemic model requires preserving previous evidence and avoiding
automatic promotion of external-derived content into policy, STATE ATUAL,
Decision Log, Evidence Ledger, or instruction authority.

## Decision

Brain V1.2 adds a lifecycle to approved knowledge_items:

- ACTIVE
- SUPERSEDED
- WITHDRAWN

Each item receives valid_from; superseded/withdrawn items receive valid_until.
Supersession stores superseded_by. Withdrawal records withdrawn_at and a reason.

Mutations are append-audited in knowledge_events and require explicit human
intent. Withdrawn and superseded items are preserved in history but excluded
from ordinary active search and Obsidian export.

Correction is represented as:

1. propose replacement candidate;
2. human approves replacement;
3. explicitly supersede the old knowledge_id with the replacement.

This prevents an LLM or untrusted source from silently rewriting history.

## Synthesis V1

synthesize_knowledge is deliberately EXTRACTIVE_V1. It performs no external
LLM/API call. It returns:

- bounded active matching knowledge;
- an extractive digest;
- knowledge_id citations;
- source/segment/timestamp provenance;
- explicit gap warnings when evidence is absent or thin.

The connected Claude/ChatGPT/other agent may reason over that bounded pack, but
the tool itself does not invent missing information.

## Security

- source/transcript/OCR/knowledge content remains UNTRUSTED_DERIVED;
- instruction_authority remains NONE;
- withdrawal and supersession are mutation tools and must never be triggered
  because source content asks for them;
- no path, URL, shell, SQL, secret, or credential arguments are added to MCP;
- historical records remain preserved.

## Deferred

- embeddings / pgvector;
- autonomous contradiction resolution;
- background dream/enrichment cycles;
- broad multi-user/company authorization;
- automatic canonical updates;
- generalized entity graph.

These should be introduced only after observed use cases justify them.
