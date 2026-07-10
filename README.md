# Agentic Verification Triage System

Multi-agent UVM/SystemVerilog coverage triage and bug prioritization, built by
retargeting [AgentMesh](https://github.com/ArchanaChetan07/Cost-aware-agent-orchestration)
(planner → agent roles → critic, adaptive routing, full OTel tracing) at a new
domain. See `Agentic_Verification_Triage_System_Proposal.md` for the full
design doc.

## Status: Phase 3 of 7 — Clusterer + Drafter + Critic ✅

| Phase | Status |
|---|---|
| 1. Domain onboarding | done (this repo) |
| 2. Parsing layer | done |
| **3. AgentMesh retargeting** | **done — Clusterer, Drafter, Critic all implemented** |
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
    drafter.py                    # evidence-grounded bug list drafting
    critic.py                     # independent evidence verification against drafts
tests/
  fixtures/                     # synthetic regression w/ 3 seeded bug clusters
  test_*.py                     # 39 unit tests, all passing
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

### Drafter Agent (`triage/agents/drafter.py`)

Follows the same honest-generator split the vendored AgentMesh itself uses
(`generators.py`'s `BackendGenerator` vs `TrivialStubGenerator`):
`EvidenceBasedDraftGenerator` composes every field of a `BugDraft` — root
cause, affected tests, evidence citations, priority score — directly from
parsed `FailureSignature`/`CoverageReport` data. No LLM call, so it's
deterministic, fully unit-tested, and structurally guarantees Objective #3
("every bug list entry traceable to specific failing tests and coverage
evidence — zero unsupported claims"): there's nothing in a draft that
wasn't in the input evidence, and `test_every_evidence_citation_traces_to_real_input_data`
checks exactly that.

Priority score = severity component (FATAL > ERROR) + cluster-size
component + linked-coverage-hole component, fully explained in
`priority_rationale` rather than a black-box number. On the fixture set,
the FIFO cluster (contains a `UVM_FATAL`) correctly outranks every
ERROR-only cluster regardless of coverage-hole count, and the singleton
ambiguous cluster from the Clusterer ranks lowest and stays flagged
`needs_llm_review=True` rather than being drafted with false confidence.

Coverage/module relevance uses a stated keyword-overlap heuristic
(`related_coverage_holes`/`related_code_coverage`) — e.g. hierarchy path
`...fifo_agent.monitor` matches covergroup `fifo_cg` and module
`fifo_unit` via the shared "fifo" token. This is an explicit heuristic,
not a claim of true testbench-structure understanding; a production
version would consume the testbench's actual module hierarchy map.

A future `LLMDraftGenerator` would wrap `Mesh`/`AdaptiveRouter` (mirroring
`BackendGenerator`) to turn this evidence into more polished prose, or to
resolve `needs_llm_review` clusters the Clusterer couldn't merge on
structure alone — not implemented yet.

### Critic Agent (`triage/agents/critic.py`)

Independently reviews each `BugDraft` against the underlying evidence —
deliberately by **re-deriving ground truth from the cluster/coverage data
itself**, not by re-reading the Drafter's own evidence list. A critic that
only checks internal consistency of what the Drafter already wrote can't
catch a drafter that fabricated its evidence wholesale; this one
re-parses each evidence citation and checks it against the actual
`FailureSignature.events`, `CoverageReport.coverage_holes()`, and
`CoverageReport.code_coverage`, independently recomputes the priority
score, and scans the free-text root-cause line for identifiers that don't
actually appear anywhere in the cluster.

Per the risk table ("Critic agent becomes a rubber stamp"), effectiveness
is measured on **both axes**, not just catch rate:

- `test_all_real_drafts_accepted_cleanly` / the false-negative half of
  `test_critic_effectiveness_on_mutation_set` — honest, evidence-grounded
  drafts must pass clean (0% false-negative rate)
- the false-positive-catch half of the same test — 7 distinct injected
  flaws (fabricated coverage hole, fabricated log event, fabricated code
  coverage module, claiming an unrelated test, omitting a real test,
  inflating the priority score, hallucinating an identifier in the root
  cause) applied to every real draft, all must be caught (100% catch rate
  on this labeled mutation set — a small-scale rehearsal of the Section 7
  methodology's critic-effectiveness measurement, same "measure both
  directions" discipline, smaller N)

On the real (non-mutated) fixture drafts the Critic accepts all 4 cleanly,
since `EvidenceBasedDraftGenerator` is evidence-grounded by construction —
which is itself informative: the interesting test is the mutation set,
not the pass-through case.

## Next: Phase 4 — Bug seeding & real test harness

Phases 1–3 are now complete against synthetic fixtures. The honest next
step per the proposal (Section 7, "no claim will be published without a
reproducible script") is to stop validating against hand-written fixtures
and start validating against a **real** UVM regression:

- Pick one open-source design to start — PicoRV32 (small, easy to seed
  controlled bugs in) rather than OpenTitan initially (larger surface,
  save for later)
- Seed a first batch of real bugs, run real regressions via
  Verilator/Icarus, capture real regression summaries/coverage/logs
- Adapt `regression_parser.py`/`coverage_parser.py`/`log_signature.py` to
  whatever the real tool output actually looks like — this is where the
  "≥95% parse rate" objective gets tested against reality instead of a
  synthetic fixture built to match the parser
- Once real signatures/coverage flow through, re-run the same
  Clusterer → Drafter → Critic pipeline unchanged and see where cluster
  purity actually lands against real seeded-bug ground truth

Separately (can happen in parallel): wire an actual LLM call through
`Mesh`/`AdaptiveRouter` for `needs_llm_review` clusters and add `"triage"`
to `ROLE_SEQUENCES` so every step gets traced the same way
planner/coder/critic are today — deferred because it needs either a real
model endpoint or an API-key-backed generator (the vendored submodule's
simulated backend only produces placeholder text), and is lower-value
than getting real regression data flowing first.
