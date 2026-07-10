"""Drafter Agent (Section 5.2).

For each cluster from the Clusterer Agent, drafts a bug report: probable
root cause, affected tests, supporting coverage/log evidence, and a
priority score derived from coverage-hole severity and cluster size.

Follows the same honest-generator split used by the vendored AgentMesh
(`generators.py`'s `BackendGenerator` vs `TrivialStubGenerator` pattern):

* `EvidenceBasedDraftGenerator` is the real path implemented here — it
  composes the root-cause description and every evidence citation directly
  from parsed `FailureSignature`/`CoverageReport` fields. No LLM call, so
  it's deterministic and 100% testable, and it structurally satisfies
  Objective #3 ("every bug list entry traceable to specific failing tests
  and coverage evidence, zero unsupported claims") — there is nothing in
  a draft that wasn't in the input evidence.
* A future `LLMDraftGenerator` would wrap `Mesh`/`AdaptiveRouter` (the same
  way `BackendGenerator` wraps a `Backend`) to turn this evidence into more
  polished prose, or to handle clusters flagged `needs_llm_review` by the
  Clusterer. That is not implemented yet — this module only produces the
  evidence-grounded draft the LLM path would start from.
"""
from __future__ import annotations

import re

from ..agents.clusterer import FailureCluster
from ..models import BugDraft, CoverageReport, EvidenceCitation

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"uvm", "test", "top", "env", "agent", "monitor", "scoreboard", "unit", "cg", "slave"}


def _keywords(*texts: str) -> set[str]:
    """Lowercase alnum tokens, minus generic UVM/TB structural words, used
    to heuristically relate a cluster's hierarchy paths/test names to
    coverage covergroups/modules by shared domain vocabulary (e.g. "alu",
    "fifo", "apb"). This is a stated heuristic, not a claim of semantic
    understanding — real systems would use the testbench's own module map."""
    toks: set[str] = set()
    for text in texts:
        toks |= set(_TOKEN_RE.findall(text.lower()))
    return toks - _STOPWORDS


def related_coverage_holes(cluster: FailureCluster, report: CoverageReport) -> list[tuple[str, str, str]]:
    cluster_kw = _keywords(*cluster.test_names, *[
        h for sig in cluster.signatures for h in sig.hierarchy_paths
    ])
    related = []
    for cg_name, cp_name, bin_name in report.coverage_holes():
        if _keywords(cg_name) & cluster_kw:
            related.append((cg_name, cp_name, bin_name))
    return related


def related_code_coverage(cluster: FailureCluster, report: CoverageReport):
    cluster_kw = _keywords(*cluster.test_names, *[
        h for sig in cluster.signatures for h in sig.hierarchy_paths
    ])
    return [cc for cc in report.code_coverage if _keywords(cc.module) & cluster_kw]


def _root_cause_text(cluster: FailureCluster) -> str:
    msg_ids = sorted({mid for sig in cluster.signatures for mid in sig.msg_ids})
    hierarchies = sorted({h for sig in cluster.signatures for h in sig.hierarchy_paths})
    n = cluster.size
    id_part = ", ".join(msg_ids) if msg_ids else "an unclassified error"
    where_part = ", ".join(hierarchies) if hierarchies else "an unknown component"
    plural = "1 test" if n == 1 else f"{n} tests"
    return f"Repeated {id_part} raised from {where_part}, observed across {plural}."


def _priority_score(cluster: FailureCluster, holes: list, code_cov: list) -> tuple[float, str]:
    """Priority derived from coverage-hole severity and cluster size
    (Section 5.2). Purely additive and fully explained in `rationale` so
    every score is traceable, not a black-box number."""
    worst = "UVM_FATAL" if any(sig.severity == "UVM_FATAL" for sig in cluster.signatures) else "UVM_ERROR"
    severity_component = 0.5 if worst == "UVM_FATAL" else 0.25
    size_component = min(0.3, 0.1 * cluster.size)
    hole_component = min(0.2, 0.05 * len(holes))
    score = round(min(1.0, severity_component + size_component + hole_component), 3)
    rationale = (
        f"severity={worst} (+{severity_component}), "
        f"cluster_size={cluster.size} (+{size_component}), "
        f"linked_coverage_holes={len(holes)} (+{hole_component})"
    )
    return score, rationale


class EvidenceBasedDraftGenerator:
    """The real Drafter path: composes a BugDraft purely from parsed evidence."""

    def draft(self, cluster: FailureCluster, coverage_report: CoverageReport) -> BugDraft:
        holes = related_coverage_holes(cluster, coverage_report)
        code_cov = related_code_coverage(cluster, coverage_report)

        evidence: list[EvidenceCitation] = []
        for sig in cluster.signatures:
            for ev in sig.events:
                evidence.append(EvidenceCitation(
                    kind="log_event",
                    detail=f"{sig.test_name}: {ev.severity} [{ev.msg_id}] {ev.file}({ev.line}) — {ev.message}",
                ))
        for cg_name, cp_name, bin_name in holes:
            evidence.append(EvidenceCitation(
                kind="coverage_hole",
                detail=f"{cg_name}.{cp_name}: bin '{bin_name}' has 0 hits",
            ))
        for cc in code_cov:
            evidence.append(EvidenceCitation(
                kind="code_coverage",
                detail=(f"module {cc.module}: line={cc.line_pct}% branch={cc.branch_pct}% "
                        f"toggle={cc.toggle_pct}% fsm={cc.fsm_pct}%"),
            ))

        score, rationale = _priority_score(cluster, holes, code_cov)

        return BugDraft(
            cluster_id=cluster.cluster_id,
            probable_root_cause=_root_cause_text(cluster),
            affected_tests=cluster.test_names,
            evidence=evidence,
            priority_score=score,
            priority_rationale=rationale,
            generator="evidence_template",
            needs_llm_review=cluster.needs_llm_review,
        )


def draft_bug_list(clusters: list[FailureCluster], coverage_report: CoverageReport,
                    generator: EvidenceBasedDraftGenerator | None = None) -> list[BugDraft]:
    """Draft one BugDraft per cluster, sorted highest-priority first."""
    gen = generator or EvidenceBasedDraftGenerator()
    drafts = [gen.draft(c, coverage_report) for c in clusters]
    return sorted(drafts, key=lambda d: d.priority_score, reverse=True)
