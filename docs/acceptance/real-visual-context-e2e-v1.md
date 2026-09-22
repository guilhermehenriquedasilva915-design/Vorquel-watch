# Real Visual Context E2E Acceptance V1

Status: Accepted
Acceptance executed: 2026-09-20
Recorded: 2026-09-21
Branch: `feat/10-analyze-learn-brain`

This acceptance exercises the MCP V1.3 visual-context surface against an
already processed real local video. It does not authorize a new capability or
change the contracts, budgets or trust boundaries established by ADR 0008.

## MEDIDO

### Real source and persisted evidence

| Field | Value |
|---|---|
| `source_id` | `src_45b118461b35497d9acabefebd618597` |
| Type | `LOCAL_VIDEO` |
| Duration | 1,629,994 ms |
| Source state | `READY` |
| Job | `job_064725b9bd804d8bb1b4af1937489a7b` |
| Job status | `SUCCEEDED` |
| Transcript | `trn_20e17c4dd7c8eda5cdb175808eb2ff8d` |
| Screen observations/OCR | Confirmed |

### `get_visual_context_at`

| Requested point | Result | Evidence returned | Wall time |
|---|---|---|---:|
| 30 s | Success | OCR naturally absent; 2 transcript segments | 5,275.020 ms |
| 10 min | Success | 161 OCR characters; 24 blocks; 3 transcript segments | 2,403.400 ms |
| 20 min | Success | 81 OCR characters; 11 blocks; 4 transcript segments | 2,061.645 ms |

### `get_visual_context_range`

| Requested range | Result | Packs and coverage | Wall time |
|---|---|---|---:|
| 110-130 s | Success | 10 ordered packs; 110,000-130,000 ms | 24,228.671 ms |
| 14-16 min | Success | 20 ordered packs; 840,000-960,000 ms | 135,317.406 ms |

The principal MCP session took 172,819.649 ms in total.

### Determinism

The 10-minute point call was repeated with identical arguments. The repeated
call took 3,364.918 ms. Its canonical `data + security` projection was
identical to the first result, with SHA-256:

`ab596f3df9a3bf7d0314856a6b760202a0e90bc49529ad097e537c81f97e3758`

### Response audit

| Check | Occurrences |
|---|---:|
| Absolute paths | 0 |
| Configured data directory | 0 |
| Configured secret | 0 |
| URLs | 0 |
| Binary values | 0 |
| Large base64-like blobs | 0 |
| Forbidden keys | 0 |

Every returned pack retained:

```text
data_trust_class = UNTRUSTED_DERIVED
instruction_authority = NONE
```

### Final regression

- 27 isolated test modules passed: 250 tests passed and 1 expected test was
  skipped.
- Visual Context MCP tests passed 23/23.
- `pip check`, bytecode compilation, complete runtime imports and repository
  security policy checks passed.

### Repository state

- Final branch: `feat/10-analyze-learn-brain`.
- Final `git status --short`: empty before this documentation-only closure.
- The acceptance execution modified no files and created no commit.
- No push was performed.

## OBSERVADO

- The 30-second point was a naturally occurring case without OCR; real
  transcript evidence was available for the same point.
- No suitable naturally occurring episode without transcript was found.
- The trust class and absence of instruction authority were preserved in all
  results.
- Both ranges were bounded, ordered and covered their requested endpoints.
- The two-minute range completed successfully.
- The acceptance ended on the expected branch with a clean working tree.

## Performance risk / follow-up

**MEDIDO:** the 14-16 minute range, with a logical duration of two minutes,
took 135,317.406 ms on this machine.

**Classification:** PERFORMANCE RISK / FOLLOW-UP.

This measurement is not automatically classified as a bug. No optimization,
threshold or budget change is authorized by this acceptance. Any remediation
must follow future profiling rather than an assumed solution.

## INFERIDO

- The functional acceptance criteria were met.
- Determinism was demonstrated for an identical repeated request.
- The `UNTRUSTED_DERIVED` / `NONE` trust boundary was maintained.
- The audited MCP projections did not leak paths, secrets or binary payloads.
- Scene-aware processing cost on larger ranges merits operational follow-up.
- This acceptance does not imply that Vorquel Watch is production-ready.
- It does not authorize Vision, `get_frame`, RAG, visual knowledge, live
  streaming or any other deferred capability.

VEREDITO DO ACCEPTANCE:
PASS
