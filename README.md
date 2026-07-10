# Agentic Verification Triage System

Multi-agent UVM/SystemVerilog coverage triage and bug prioritization, built by
retargeting [AgentMesh](https://github.com/ArchanaChetan07/Cost-aware-agent-orchestration)
(planner → agent roles → critic, adaptive routing, full OTel tracing) at a new
domain. See `Agentic_Verification_Triage_System_Proposal.md` for the full
design doc.

## Status: Phase 2 of 7 — Parsing Layer ✅

| Phase | Status |
|---|---|
| 1. Domain onboarding | done (this repo) |
| **2. Parsing layer** | **done — see below** |
| 3. AgentMesh retargeting (Clusterer/Drafter/Critic agents) | not started |
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
tests/
  fixtures/                     # synthetic regression w/ 3 seeded bug clusters
  test_*.py                     # 16 unit tests, all passing
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

The test fixtures encode 3 synthetic root causes across 6 failing tests
(ALU overflow, FIFO full-write, APB reset glitch) specifically so
`test_log_signature.py` can assert same-root-cause tests share a feature key
and different root causes don't collide — a small-scale rehearsal of the
Objective #2 cluster-purity methodology described in the proposal (Section 7).

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

## Next (Phase 3)

- Add `"triage"` to `ROLE_SEQUENCES` in the AgentMesh orchestrator config
  (via subclassing/config, not editing the submodule)
- Implement Clusterer Agent: structured `feature_key()` similarity first,
  LLM semantic grouping fallback for near-misses
- Implement Drafter Agent: evidence-cited bug list per cluster
- Implement Critic Agent: flags drafted entries unsupported by evidence
