# Analyze-to-Candidate Draft V1 Acceptance

Status: Accepted  
Executed: 2026-09-22  
Branch: `feat/10-analyze-learn-brain`

## Real source

- Source: `src_45b118461b35497d9acabefebd618597`
- State: `READY`
- Job: `SUCCEEDED`
- Duration: 1,629,994 ms
- Local media integrity: verified
- Analyze window: 0-120,000 ms

## Measured flow

The read-only run selected 24 transcript segments and 8 existing
VisualReviewPacks. After evidence deduplication it exposed 25 structured
evidence items and 1,600 text characters to the deterministic local analyzer.
It produced five bounded drafts in 24.544 seconds on the first run and 5.266
seconds on the acceptance/proposal run.

Analyze did not change persistence: the source had zero PENDING candidates
before Analyze and zero immediately after it.

After explicit review, one anchored draft was submitted through the existing
KnowledgeWriter boundary:

- Draft: `draft_92dc98c1a2d15fdafa28acd8`
- Candidate: `knd_1d7c243624d84514b2e1ad5120c68d6c`
- Status: `PENDING`
- Type/status: `CLAIM` / `DECLARADO`
- Evidence window: 14,280-17,040 ms
- Transcript: `trn_20e17c4dd7c8eda5cdb175808eb2ff8d`
- Segment: `seg_2fb9af3469dbd4112ced52ad56670c1c`
- Proposal wall time: 0.157 seconds

The candidate remained `UNTRUSTED_DERIVED` with
`instruction_authority = NONE`. The Analyze V1 acceptance stopped at PENDING;
the later composed Watch V1 closure explicitly approved this same candidate.

## Composed Watch V1 closure

On 2026-09-22, an explicit human approval of the PENDING candidate created
`knw_50ca5a1b9bd94afebaa172b225bc52a3`. The item was measured as `ACTIVE`, with
`valid_from` exactly equal to `approved_at` (`2026-09-22T22:12:55.287496+00:00`).
Search for `grandes empresas` in domain `sales` returned that item, and
extractive synthesis used it with the original 14,280-17,040 ms transcript
provenance. The synthesis retained `instruction_authority = NONE` and marked
the context as containing untrusted content.

Measured wall times for this final persisted portion were 5,258 ms for
approval, 4,944 ms for search and 4,919 ms for synthesis.

## Regression

- Analyze-specific tests: 13/13 passed.
- Knowledge tests: 12/12 passed.
- UI tests: 7/7 passed.
- MCP contract tests: 3/3 passed.
- Visual Context tests: 23/23 passed.
- Full suite: 28 modules; 266 passed and 1 expected skip.
- Compileall, pip check and repository security checks passed.
- MCP remained at exactly 24 tools; no Analyze tool was added.
- `get_frame` remained unexposed.

This acceptance does not authorize automatic persistence, automatic approval,
a paid provider, RAG, embeddings, scopes, external sources or a new MCP tool.
