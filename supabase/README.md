# Supabase schema

Dedicated project: `vorquel-content-brain` (`xnygwzuckijaxzlszfpn`, `sa-east-1`).

The Git repository is the source of truth for schema migrations.

Migration order:
1. `0001_core_entities.sql`
2. `0002_indexes_and_review_integrity.sql`
3. `0003_full_text_search.sql`
4. `0004_security_rls.sql`
5. `0005_alpha_search_and_idempotency.sql`
6. `0006_security_performance_hardening.sql`
7. `0007_alpha_correctness_hardening.sql`

Do not place Supabase server credentials in this directory or in browser code.
