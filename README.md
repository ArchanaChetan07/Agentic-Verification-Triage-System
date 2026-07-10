# Agentic Verification Triage System

Multi-agent UVM/SystemVerilog coverage triage and bug prioritization, built by
retargeting [AgentMesh](https://github.com/ArchanaChetan07/Cost-aware-agent-orchestration)
(planner → agent roles → critic, adaptive routing, full OTel tracing) at a new
domain. See `Agentic_Verification_Triage_System_Proposal.md` for the full
design doc.

## Status: Phase 3 of 7 — Clusterer Agent ✅ (Drafter/Critic in progress)

| Phase | Status |
|---|---|
| 1. Domain onboarding | done (this repo) |
| 2. Parsing layer | done |
| **3. AgentMesh retargeting** | **Clusterer Agent done; Drafter/Critic next** |
| 4. Bug seeding & test harness (real OpenTitan/PicoRV32 regressions) | not started |
| 5. Observability integration | not started |
| 6. Evaluation & validation report | not started |
| 7. Documentation & demo | not started |

## What's here

```
triage/
  models.py                     # TestResult, CoverageReport, FailureSignature, etc.
  parsing/
    regression_parser.py        # regression summary -> TestResult records
    coverage_parser.py          # UCIS-style text export -> CoverageReport
    log_signature.py            # UVM_ERROR/UVM_FATAL log -> FailureSignature
  agents/
    clusterer.py                 # structured-similarity clustering + LLM-review flagging
tests/
  fixtures/                     # synthetic regression w/ 3 seeded bug clusters
  test_*.py                     # 24 unit tests, all passing
vendor/agentmesh/                # git submodule: the real AgentMesh core (reused, not forked)
```

### Parsing layer

- **Regression Parser** — line-oriented `TEST: ... SEED: ... STATUS: ... TIME: ...`
  format (adapt the regex to your actual regression-runner output). Bad lines
  are collected as errors rather than aborting the parse; `parse_rate` tracks
  Objective #1 (≥95% parsed without manual intervention).
- **Coverage Parser** — simplified UCIS-style text export: covergroups,
  coverpoints, crosses, bin hit counts, and per-module code coverage
  (line/branch/toggle/FSM). `CoverageReport.coverage_holes()` returns every
  zero-hit bin.
- **Log Signature Extractor** — pulls `UVM_ERROR`/`UVM_FATAL` lines into a
  `FailureSignature` per test: sorted, deduplicated message IDs + hierarchy
  paths. This structured key is what the Clusterer Agent (Phase 3) groups on
  before falling back to LLM semantic grouping — deliberately *not* raw
  message text, so clustering is auditable.

The test fixtures encode 3 synthetic root causes across 7 failing tests
(ALU overflow, FIFO full-write, APB reset glitch, plus one deliberately
ambiguous 4th failure) specifically so `test_log_signature.py` can assert
same-root-cause tests share a feature key and different root causes don't
collide — a small-scale rehearsal of the Objective #2 cluster-purity
methodology described in the proposal (Section 7).

### Clusterer Agent (`triage/agents/clusterer.py`)

Pure code, no LLM calls — the part of Section 5.2's Clusterer that's fully
deterministic and cluster-purity-testable on its own:

1. **Exact match**: identical `feature_key()` (msg_ids + hierarchy_paths) →
   merge with full confidence, `method="exact_key"`.
2. **Fuzzy similarity**: weighted Jaccard over msg_ids/hierarchy_paths for
   signatures that don't share an exact key (e.g. a run surfacing one extra
   secondary symptom) → auto-merge above `AUTO_MERGE_THRESHOLD`,
   `method="similarity"`.
3. **LLM review band**: similarity in `REVIEW_THRESHOLD..AUTO_MERGE_THRESHOLD`
   is genuinely ambiguous from structure alone — left as its own cluster with
   `needs_llm_review=True` rather than guessed at. This is exactly the
   "structured features don't cleanly separate" case Section 5.2 says should
   fall back to LLM semantic grouping (that LLM call itself isn't implemented
   yet — this module only decides which clusters need it).

On the fixture set, this produces 3 correct clusters plus 1 case correctly
flagged for review instead of silently merged or split:

```
cluster_000 [exact_key]  -> alu_overflow, alu_overflow_neg
cluster_001 [exact_key]  -> fifo_full_write, fifo_almost_full
cluster_002 [similarity] -> apb_reset, apb_reset_seed2
cluster_003 [singleton, needs_llm_review=True] -> apb_addr_decode
```

## Why AgentMesh is a submodule, not a copy

`vendor/agentmesh` pins a specific commit of the real, public
`Cost-aware-agent-orchestration` repo. This keeps the reuse honest and
verifiable — anyone can diff against upstream — rather than silently forking
and drifting. `Mesh`, `Task`, `AdaptiveRouter`, and `Tracer` are imported
unmodified; Phase 3 will add a `"triage"` entry to `ROLE_SEQUENCES` and new
`clusterer`/`drafter`/`critic` role prompts, not fork the orchestration core.

## Running the tests

```bash
pip install -e ".[dev]"
pip install -e vendor/agentmesh
git submodule update --init
pytest -q
```

## Next (rest of Phase 3)

- Add `"triage"` to `ROLE_SEQUENCES` in a config layer on top of the
  AgentMesh submodule (not editing it directly)
- Wire the `needs_llm_review` clusters from the Clusterer above into an
  actual LLM call via `Mesh`/`AdaptiveRouter` for semantic grouping
- Implement Drafter Agent: evidence-cited bug list per cluster (root cause,
  affected tests, cited coverage holes + log evidence, priority score)
- Implement Critic Agent: flags drafted entries unsupported by the
  underlying evidence; measure both false positives caught and new false
  negatives introduced (per the proposal's risk mitigation)
