"""End-to-end triage pipeline with full decision tracing (Section 5.2/5.3).

Wires the Parsing Layer (Phase 2) and the Clusterer/Drafter/Critic agents
(Phase 3) together, wrapping every cluster assignment, draft, and critic
verdict in an OTel-shaped span via AgentMesh's `Tracer` — reused unmodified,
exactly as the proposal specifies ("Tracer: reused unmodified — every
planner decision, cluster assignment, draft, and critic override becomes
an OTel-shaped span, reusing AgentMesh's tracer").

This module doesn't use `Mesh`/`AdaptiveRouter` because none of these three
agents make an LLM call yet (see `drafter.py`'s docstring on the
evidence-template vs LLM generator split) — there's no model to route to.
When an `LLMDraftGenerator` is added for `needs_llm_review` clusters, that
step would go through `Mesh` and get routing spans the same way
`orchestrator.py`'s planner/coder/critic steps do; everything here would be
unchanged.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# Vendored AgentMesh submodule — import path setup for standalone use.
_VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "agentmesh"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from agentmesh.telemetry import Tracer  # noqa: E402

from .agents.clusterer import FailureCluster, cluster_failures
from .agents.critic import CriticAgent, critique_bug_list
from .agents.drafter import draft_bug_list
from .models import BugDraft, CoverageReport, CriticVerdict, FailureSignature
from .parsing.coverage_parser import parse_coverage_report
from .parsing.log_signature import build_failure_signature
from .parsing.regression_parser import parse_regression_summary


@dataclass
class TriageRunResult:
    trace_id: str
    clusters: list[FailureCluster] = field(default_factory=list)
    drafts: list[BugDraft] = field(default_factory=list)
    verdicts: list[CriticVerdict] = field(default_factory=list)
    parse_rate: float = 1.0

    @property
    def accepted_drafts(self) -> list[BugDraft]:
        by_id = {v.cluster_id: v for v in self.verdicts}
        return [d for d in self.drafts if by_id.get(d.cluster_id) and by_id[d.cluster_id].accepted]

    @property
    def rejected_drafts(self) -> list[BugDraft]:
        by_id = {v.cluster_id: v for v in self.verdicts}
        return [d for d in self.drafts if by_id.get(d.cluster_id) and by_id[d.cluster_id].rejected]

    @property
    def review_needed_clusters(self) -> list[FailureCluster]:
        return [c for c in self.clusters if c.needs_llm_review]


def run_triage_pipeline(
    regression_summary_text: str,
    coverage_report_text: str,
    failure_logs: dict[str, str],
    tracer: Tracer | None = None,
) -> tuple[TriageRunResult, Tracer]:
    """Runs Parsing -> Clusterer -> Drafter -> Critic on raw artifact text,
    tracing every step. `failure_logs` maps test_name -> raw log text for
    every FAILED test named in the regression summary."""
    tracer = tracer or Tracer()
    trace_id = tracer.new_trace_id()

    with tracer.span("triage.pipeline", trace_id) as root:
        with tracer.span("parse.regression_summary", trace_id, root) as s:
            reg = parse_regression_summary(regression_summary_text)
            s.attributes.update({
                "parse.total": reg.total, "parse.errors": len(reg.errors),
                "parse.rate": round(reg.parse_rate, 4),
                "parse.failure_count": len(reg.failures),
            })

        with tracer.span("parse.coverage_report", trace_id, root) as s:
            coverage: CoverageReport = parse_coverage_report(coverage_report_text)
            holes = coverage.coverage_holes()
            s.attributes.update({
                "coverage.covergroups": len(coverage.covergroups),
                "coverage.holes": len(holes),
            })

        signatures: list[FailureSignature] = []
        with tracer.span("parse.failure_logs", trace_id, root) as s:
            for t in reg.failures:
                log_text = failure_logs.get(t.test_name, "")
                sig = build_failure_signature(t.test_name, log_text)
                signatures.append(sig)
            s.attributes["signatures.count"] = len(signatures)

        with tracer.span("agent.clusterer", trace_id, root) as s:
            clusters = cluster_failures(signatures)
            for c in clusters:
                with tracer.span("cluster.assignment", trace_id, s,
                                  **{"cluster.id": c.cluster_id, "cluster.method": c.method,
                                     "cluster.size": c.size, "cluster.needs_llm_review": c.needs_llm_review,
                                     "cluster.tests": c.test_names,
                                     "cluster.min_pairwise_similarity": c.min_pairwise_similarity}):
                    pass
            s.attributes.update({
                "clusterer.cluster_count": len(clusters),
                "clusterer.needs_review_count": sum(1 for c in clusters if c.needs_llm_review),
            })

        with tracer.span("agent.drafter", trace_id, root) as s:
            drafts = draft_bug_list(clusters, coverage)
            for d in drafts:
                with tracer.span("draft.bug_entry", trace_id, s,
                                  **{"draft.cluster_id": d.cluster_id, "draft.priority_score": d.priority_score,
                                     "draft.priority_rationale": d.priority_rationale,
                                     "draft.generator": d.generator, "draft.evidence_count": len(d.evidence),
                                     "draft.needs_llm_review": d.needs_llm_review}):
                    pass
            s.attributes["drafter.draft_count"] = len(drafts)

        with tracer.span("agent.critic", trace_id, root) as s:
            critic = CriticAgent()
            verdicts = critique_bug_list(drafts, clusters, coverage, critic)
            for v in verdicts:
                with tracer.span("critic.verdict", trace_id, s,
                                  **{"critic.cluster_id": v.cluster_id, "critic.accepted": v.accepted,
                                     "critic.flag_count": len(v.flags), "critic.flags": v.flags,
                                     "critic.checked_claims": v.checked_claims}):
                    pass
            accepted = sum(1 for v in verdicts if v.accepted)
            s.attributes.update({
                "critic.total": len(verdicts), "critic.accepted": accepted,
                "critic.rejected": len(verdicts) - accepted,
                "critic.override_rate": round((len(verdicts) - accepted) / len(verdicts), 4) if verdicts else 0.0,
            })

        root.attributes.update({
            "pipeline.cluster_count": len(clusters),
            "pipeline.draft_count": len(drafts),
            "pipeline.accepted_count": sum(1 for v in verdicts if v.accepted),
        })

    result = TriageRunResult(
        trace_id=trace_id, clusters=clusters, drafts=drafts,
        verdicts=verdicts, parse_rate=reg.parse_rate,
    )
    return result, tracer
