# Agentic Verification Triage System

Multi-agent UVM/SystemVerilog coverage triage and bug prioritization, built by
retargeting [AgentMesh](https://github.com/ArchanaChetan07/Cost-aware-agent-orchestration)
(planner → agent roles → critic, adaptive routing, full OTel tracing) at a new
domain. See `Agentic_Verification_Triage_System_Proposal.md` for the full
design doc.

## Status: Phase 5 of 7 — Observability ✅ (against synthetic fixtures)

| Phase | Status |
|---|---|
| 1. Domain onboarding | done (this repo) |
| 2. Parsing layer | done — validated against real sim output too |
| 3. AgentMesh retargeting | done — Clusterer, Drafter, Critic all implemented |
| 4. Bug seeding & test harness | isolated per-test harness working; 1 real bug reproduced; UVM gap identified (needs OpenTitan for full validation) |
| **5. Observability integration** | **done — traced pipeline + dashboard, against fixtures; wiring real Phase 4 data through it is next** |
| 6. Evaluation & validation report | not started |
| 7. Documentation & demo | not started |

### Traced pipeline (`triage/pipeline.py`) + dashboard (`triage/dashboard.py`)

Section 5.2 requires "every planner decision, cluster assignment, draft,
and critic override becomes an OTel-shaped span, reusing AgentMesh's
tracer" — `pipeline.py` wires Parsing → Clusterer → Drafter → Critic
together and wraps every stage and every individual decision (each
cluster assignment, each drafted bug entry, each critic verdict) in a
span from the vendored AgentMesh's `Tracer`, **reused unmodified**, not
reimplemented. `Mesh`/`AdaptiveRouter` aren't used here because none of
these three agents call an LLM yet (see `drafter.py`'s docstring on the
evidence-template vs. future LLM-generator split) — there's no model to
route to, so there's nothing for the router to do. When an LLM-backed
generator is added for `needs_llm_review` clusters, that step would go
through `Mesh` and pick up routing spans the same way `orchestrator.py`'s
planner/coder/critic steps do; nothing here would need to change.

`test_pipeline_matches_standalone_agent_results` explicitly checks that
running through the traced pipeline produces identical clusters/drafts/
verdicts to calling the Phase 3 agents directly — tracing is purely
observational, it doesn't change behavior.

`dashboard.py` builds a self-contained HTML file (same "opens anywhere,
no server" approach as the vendored submodule's own dashboard) from the
real span data: pipeline stage timeline, Clusterer method breakdown
(exact_key/similarity/singleton counts — directly showing how much
clustering happened without any LLM call), Critic accept/reject counts
and override rate, priority score distribution, and a full cluster table
with drafted root cause, priority, and critic verdict per row. Generate
it yourself:

```bash
python3 scripts/generate_dashboard.py my_dashboard.html
```

A real Prometheus/Grafana/OTel-Collector wiring (rather than this
single-file HTML view) is the natural next step once real regressions are
flowing at volume — the vendored submodule's existing
`observability/prometheus.yml`/`otel-collector-config.yaml` should work
unmodified, since span shape is already OTLP-compatible; not done yet
because the fixture-scale data here doesn't yet justify running that
stack.

### Honest scope note

This phase is validated against the **synthetic fixtures**, not the real
PicoRV32 data from Phase 4 — `real_data/runs/*.log` was validated through
`regression_parser.py` directly (see `test_real_data_integration.py`), but
hasn't been run through the full traced Clusterer→Drafter→Critic→dashboard
pipeline yet (that data has no structured log signal for the Clusterer to
use, as Phase 4's README section explains, so it would exercise the
pipeline's plumbing but not produce a meaningful cluster-purity result).

### Next

- Phase 6: evaluation methodology — this requires real seeded-bug ground
  truth at the proposal's target scale (15–25 bugs), which in turn needs
  the OpenTitan UVM environment identified as the real gap in Phase 4
- Wire `real_data/runs/*.log` through the full pipeline+dashboard as a
  smoke test of the plumbing, clearly labeled as "infra proof, not a
  cluster-purity result" given the missing structured log signal

### Phase 4: real infra, real bug, real result

Building on the harness-design finding from the previous session (stock
PicoRV32 firmware links all 45 tests into one linear image that halts
permanently on the first failure), this session fixed it:

- `real_data/picorv32_patches/start_single.S` — a minimal patch to
  PicoRV32's `firmware/start.S` (wrap the `TEST(...)` chain in
  `#ifdef SINGLE_TEST_ONLY` / `TEST_INDIRECT(SINGLE_TEST_NAME)`) that lets
  exactly one instruction test run as its own isolated simulation, so
  failures no longer prevent other tests from running
- `scripts/run_single_picorv32_test.sh` — builds and simulates one test in
  isolation (real `riscv64-unknown-elf-gcc`, real Icarus Verilog) and
  prints one line in our existing `TEST: ... SEED: ... STATUS: ... TIME:`
  format. `testbench.vvp` (the compiled RTL) only needs rebuilding when the
  RTL itself changes — per-test reruns just swap `firmware.hex`
- Hit and fixed a real classic C-preprocessor bug along the way: `##`
  token-pasting doesn't macro-expand its operand first, so
  `TEST(SINGLE_TEST_NAME)` pasted the literal macro name instead of the
  test name until routed through an indirection macro
  (`TEST_INDIRECT(n) → TEST(n)`)
- **Honest field, not fabricated**: this testbench has no `$random`
  seeding, so `SEED` is reported as `N/A` (our parser already handles a
  non-numeric seed → `seed=None`) rather than inventing a plausible-looking
  number

**Confirming the fix actually fixed the problem, not just moved it**: with
only the `alu_add_sub` (SUB-as-ADD) bug seeded, isolated runs show `sub`
genuinely FAILED and — importantly — `auipc` **also** genuinely FAILED, on
its own, with no other test run before it. Checking `tests/auipc.S`
confirms this is real, not an artifact: the test's own self-check code
literally executes `sub a0, a0, a1` to validate the address `auipc`
computed. So `sub` and `auipc` share a real root cause for a real reason —
a genuine 2-test correlated failure from one real RTL bug, captured in
`real_data/runs/sub_bug_seeded_regression.log` (clean-RTL control run in
`real_data/runs/clean_rtl_regression.log`), everything else in the sample
passing. **`regression_parser.py` — written in Phase 2 against synthetic
fixtures — parses this real log at 100%, zero changes needed.**

### Real limitation found: no structured error taxonomy without UVM

PicoRV32's testbench prints a bare `testname..OK` / `testname..ERROR` —
there is no `UVM_ERROR`/message-ID/hierarchy-path structure the way a real
UVM regression log has. `log_signature.py`'s `FailureSignature` (msg_ids +
hierarchy_paths) is exactly the feature the Clusterer clusters on, and
there's nothing here for it to extract. Ground truth for *this* pair
(`sub`+`auipc` share a bug) is only knowable because we seeded it and read
the RTL diff — not something the pipeline could discover from this log
alone.

This confirms rather than works around the proposal's original design
choice: PicoRV32 is genuinely useful as **infra-proof and harness
validation** (real toolchain, real simulator, real bug, real correlated
failure, real parser compatibility — all now demonstrated), but Section
7's actual cluster-purity methodology needs a **UVM environment** (e.g.
OpenTitan) where `UVM_ERROR`/hierarchy/message-ID structure genuinely
exists in the logs for `log_signature.py` and the Clusterer to work with.

### Next (from Phase 4, still open)
  Clusterer validation — this is the step that actually exercises
  Sections 5.1/5.2 against real UVM-structured logs
  the same isolated-per-test lesson from this session likely applies
  there too (check for similar linear-regression-halts-on-failure
  patterns before assuming continuation works)
- Scale from today's 1 seeded bug to the proposal's 15–25 once a UVM
  environment is in place
- Wire `real_data/runs/sub_bug_seeded_regression.log` through the full
  Parsing → Clusterer → Drafter → Critic pipeline as an integration test
  (clustering will trivially put `sub`/`auipc` in one cluster today only
  because there are just 2 failures with no distinguishing signature to
  split on — worth having as a regression test, but not a substitute for
  real cluster-purity validation against UVM data)

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
  pipeline.py                    # traced end-to-end orchestration (reuses AgentMesh's Tracer)
  dashboard.py                    # observability dashboard model + HTML builder
  dashboard_template.html        # self-contained single-file dashboard UI
tests/
  fixtures/                     # synthetic regression w/ 3 seeded bug clusters
  test_*.py                     # 56 unit/integration tests, all passing
real_data/
  picorv32_patches/start_single.S  # per-test isolation patch for PicoRV32's harness
  runs/*.log                    # REAL Icarus Verilog simulation output (see Phase 4)
scripts/
  run_single_picorv32_test.sh   # builds + simulates one PicoRV32 test in isolation
  generate_dashboard.py          # produces a dashboard HTML from the fixture data
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
