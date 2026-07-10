"""Critic Agent (Section 5.2).

Independently reviews each drafted bug entry against the underlying
evidence, flagging entries where a claim doesn't actually hold up. This is
deliberately implemented as independent re-derivation from source data
(cluster signatures + coverage report), not a re-read of the Drafter's own
evidence list — a critic that only checks "does the draft's evidence list
look internally consistent" can't catch a drafter that fabricated the
evidence list itself. Every check here recomputes its own ground truth.

Per the proposal's risk table ("Critic agent becomes a rubber stamp"), the
Critic's effectiveness must be measured on BOTH axes:
  - false positives caught: flawed drafts it correctly rejects
  - false negatives introduced: correct drafts it incorrectly rejects
`triage/eval/critic_eval.py` (see tests) computes both so a lazy or
overzealous critic can't inflate its own score either direction.
"""
from __future__ import annotations

import re

from ..agents.clusterer import FailureCluster
from ..agents.drafter import priority_score, related_code_coverage, related_coverage_holes
from ..models import BugDraft, CoverageReport, CriticVerdict

_LOG_EVIDENCE_RE = re.compile(
    r"^(?P<test>\S+):\s+(?P<sev>UVM_ERROR|UVM_FATAL)\s+\[(?P<msgid>[^\]]+)\]\s+"
    r"(?P<file>\S+)\((?P<line>\d+)\)\s+—\s+(?P<msg>.*)$"
)
_HOLE_EVIDENCE_RE = re.compile(
    r"^(?P<cg>\S+)\.(?P<cp>\S+):\s+bin\s+'(?P<bin>[^']+)'\s+has\s+0\s+hits$"
)
_CODE_COV_EVIDENCE_RE = re.compile(r"^module\s+(?P<module>\S+):")

PRIORITY_SCORE_TOLERANCE = 1e-6


def _check_affected_tests(draft: BugDraft, cluster: FailureCluster, flags: list[str]) -> int:
    actual = set(cluster.test_names)
    claimed = set(draft.affected_tests)
    if claimed != actual:
        extra = claimed - actual
        missing = actual - claimed
        if extra:
            flags.append(f"claims tests not in cluster: {sorted(extra)}")
        if missing:
            flags.append(f"omits tests that ARE in cluster: {sorted(missing)}")
    return 1


def _check_log_evidence(draft: BugDraft, cluster: FailureCluster, flags: list[str]) -> int:
    real_events_by_test = {sig.test_name: sig.events for sig in cluster.signatures}
    checked = 0
    for e in draft.evidence:
        if e.kind != "log_event":
            continue
        checked += 1
        m = _LOG_EVIDENCE_RE.match(e.detail)
        if not m:
            flags.append(f"log_event evidence doesn't match expected format: {e.detail!r}")
            continue
        test = m.group("test")
        if test not in real_events_by_test:
            flags.append(f"log_event cites test {test!r} not in this cluster")
            continue
        matched = any(
            ev.msg_id == m.group("msgid") and ev.file == m.group("file")
            and str(ev.line) == m.group("line") and ev.severity == m.group("sev")
            for ev in real_events_by_test[test]
        )
        if not matched:
            flags.append(f"log_event evidence not found in {test}'s actual events: {e.detail!r}")
    return checked


def _check_coverage_hole_evidence(draft: BugDraft, coverage_report: CoverageReport, flags: list[str]) -> int:
    real_holes = set(coverage_report.coverage_holes())
    checked = 0
    for e in draft.evidence:
        if e.kind != "coverage_hole":
            continue
        checked += 1
        m = _HOLE_EVIDENCE_RE.match(e.detail)
        if not m:
            flags.append(f"coverage_hole evidence doesn't match expected format: {e.detail!r}")
            continue
        triple = (m.group("cg"), m.group("cp"), m.group("bin"))
        if triple not in real_holes:
            flags.append(f"coverage_hole evidence cites a bin that isn't actually a hole: {e.detail!r}")
    return checked


def _check_code_coverage_evidence(draft: BugDraft, coverage_report: CoverageReport, flags: list[str]) -> int:
    real_modules = {cc.module for cc in coverage_report.code_coverage}
    checked = 0
    for e in draft.evidence:
        if e.kind != "code_coverage":
            continue
        checked += 1
        m = _CODE_COV_EVIDENCE_RE.match(e.detail)
        if not m or m.group("module") not in real_modules:
            flags.append(f"code_coverage evidence cites an unknown module: {e.detail!r}")
    return checked


def _check_root_cause_consistency(draft: BugDraft, cluster: FailureCluster, flags: list[str]) -> int:
    """The root-cause text should only mention msg_ids that actually occur
    somewhere in the cluster's signatures — a cheap but real hallucination
    check on the free-text portion of the draft."""
    real_msg_ids = {mid for sig in cluster.signatures for mid in sig.msg_ids}
    # message IDs are SCREAMING_SNAKE_CASE tokens; extract candidates that look like one
    candidates = set(re.findall(r"\b[A-Z][A-Z0-9_]{3,}\b", draft.probable_root_cause))
    bogus = candidates - real_msg_ids
    if bogus:
        flags.append(f"root cause mentions unrecognized identifiers: {sorted(bogus)}")
    return 1


def _check_priority_score(draft: BugDraft, cluster: FailureCluster,
                           coverage_report: CoverageReport, flags: list[str]) -> int:
    holes = related_coverage_holes(cluster, coverage_report)
    code_cov = related_code_coverage(cluster, coverage_report)
    expected_score, expected_rationale = priority_score(cluster, holes, code_cov)
    if abs(draft.priority_score - expected_score) > PRIORITY_SCORE_TOLERANCE:
        flags.append(
            f"priority_score {draft.priority_score} doesn't match recomputed {expected_score} "
            f"(expected rationale: {expected_rationale})"
        )
    return 1


class CriticAgent:
    """Independently verifies a BugDraft's claims against source evidence."""

    def critique(self, draft: BugDraft, cluster: FailureCluster,
                 coverage_report: CoverageReport) -> CriticVerdict:
        flags: list[str] = []
        checked = 0
        checked += _check_affected_tests(draft, cluster, flags)
        checked += _check_log_evidence(draft, cluster, flags)
        checked += _check_coverage_hole_evidence(draft, coverage_report, flags)
        checked += _check_code_coverage_evidence(draft, coverage_report, flags)
        checked += _check_root_cause_consistency(draft, cluster, flags)
        checked += _check_priority_score(draft, cluster, coverage_report, flags)

        return CriticVerdict(
            cluster_id=draft.cluster_id,
            accepted=len(flags) == 0,
            flags=flags,
            checked_claims=checked,
        )


def critique_bug_list(drafts: list[BugDraft], clusters: list[FailureCluster],
                       coverage_report: CoverageReport,
                       critic: CriticAgent | None = None) -> list[CriticVerdict]:
    agent = critic or CriticAgent()
    by_id = {c.cluster_id: c for c in clusters}
    verdicts = []
    for d in drafts:
        cluster = by_id.get(d.cluster_id)
        if cluster is None:
            verdicts.append(CriticVerdict(
                cluster_id=d.cluster_id, accepted=False,
                flags=[f"draft references unknown cluster_id {d.cluster_id!r}"],
                checked_claims=1,
            ))
            continue
        verdicts.append(agent.critique(d, cluster, coverage_report))
    return verdicts
