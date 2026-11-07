# Document Generation Roadmap and Phase Register

| Phase | Scope | Status | Notes |
|---|---|---|---|
| Phase 1 | Document-generation foundation, company profile, numbering, parties and lifecycle | Completed before this task | Existing models, migrations and tests inspected and reused. |
| Phase 2 | Universal document generation with safe placeholders and Word/PDF output | Completed before this task | Existing resolver, preview workflow, renderers and private document storage reused; regression tests passed. |
| Phase 3 | Standard document categories/types, versioned template and clause library, administration UI, approval workflow, generation/storage integration | Implemented and verified | See [Phase 3 implementation report](document_generation_phase_3.md). Dedicated 53-test app suite passed. Full suite: 618/619 passed; the one failure is a pre-existing customer-row Delete-icon expectation mismatch. |
| Phase 4 | Future extensions | Pending; not started | Requires a separate task and user approval. No Phase 4 implementation is included in this change. |

Phase 3 keeps all seeded starter content under review or inactive status. A staff user must approve it before it can be selected for normal generation.
