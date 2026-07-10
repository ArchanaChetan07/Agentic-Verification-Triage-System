"""Tests for the Critic Agent.

The first block checks the critic accepts real, evidence-grounded drafts
cleanly (no false negatives on honest input). The second block — the more
important one per the proposal's risk table ("Critic agent becomes a
rubber stamp") — deliberately injects specific, labeled flaws into
otherwise-valid drafts and checks the critic catches every one of them,
without needing an LLM in the loop for this deterministic evidence-grounding
class of error. `test_critic_effectiveness_on_mutation_set` computes both
false-positive-catch-rate and false-negative-rate the way Objective #4 and
the risk mitigation table require: a critic that flags everything would
score 100% catch rate but fail the false-negative check below.
"""
import copy
import os

from tests._shared_signatures import all_signatures as _all_signatures
from triage.agents.clusterer import cluster_failures
from triage.agents.critic import CriticAgent, critique_bug_list
from triage.agents.drafter import draft_bug_list
from triage.models import EvidenceCitation
from triage.parsing.coverage_parser import parse_coverage_report_file

COVERAGE_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "coverage_report.txt")


def _setup():
    cov = parse_coverage_report_file(COVERAGE_FIXTURE)
    clusters = cluster_failures(_all_signatures())
    drafts = draft_bug_list(clusters, cov)
    return drafts, clusters, cov


# ---- honest-draft acceptance -----------------------------------------------

def test_all_real_drafts_accepted_cleanly():
    drafts, clusters, cov = _setup()
    verdicts = critique_bug_list(drafts, clusters, cov)
    assert all(v.accepted for v in verdicts)
    assert all(v.flags == [] for v in verdicts)
    assert all(v.checked_claims > 0 for v in verdicts)


def test_unknown_cluster_id_rejected():
    drafts, clusters, cov = _setup()
    bad = copy.deepcopy(drafts[0])
    bad.cluster_id = "cluster_does_not_exist"
    verdicts = critique_bug_list([bad], clusters, cov)
    assert verdicts[0].rejected
    assert "unknown cluster_id" in verdicts[0].flags[0]


# ---- mutation-based effectiveness measurement ------------------------------

def _mutate_fabricate_coverage_hole(draft):
    d = copy.deepcopy(draft)
    d.evidence.append(EvidenceCitation(
        kind="coverage_hole", detail="totally_fake_cg.fake_cp: bin 'fake_bin' has 0 hits",
    ))
    return d


def _mutate_fabricate_log_event(draft):
    d = copy.deepcopy(draft)
    d.evidence.append(EvidenceCitation(
        kind="log_event",
        detail=f"{d.affected_tests[0]}: UVM_ERROR [FABRICATED_ID] nowhere.sv(999) — this never happened",
    ))
    return d


def _mutate_claim_unrelated_test(draft):
    d = copy.deepcopy(draft)
    d.affected_tests = d.affected_tests + ["uvm_test_completely_unrelated"]
    return d


def _mutate_omit_a_real_test(draft):
    d = copy.deepcopy(draft)
    if len(d.affected_tests) > 1:
        d.affected_tests = d.affected_tests[:-1]
    else:
        d.affected_tests = []
    return d


def _mutate_inflate_priority_score(draft):
    d = copy.deepcopy(draft)
    d.priority_score = min(1.0, d.priority_score + 0.3)
    return d


def _mutate_hallucinate_root_cause_id(draft):
    d = copy.deepcopy(draft)
    d.probable_root_cause = d.probable_root_cause + " Also linked to UNRELATED_PHANTOM_BUG_ID."
    return d


def _mutate_fabricate_code_coverage_module(draft):
    d = copy.deepcopy(draft)
    d.evidence.append(EvidenceCitation(
        kind="code_coverage", detail="module nonexistent_module: line=99.0% branch=99.0% toggle=99.0% fsm=99.0%",
    ))
    return d


MUTATORS = [
    _mutate_fabricate_coverage_hole,
    _mutate_fabricate_log_event,
    _mutate_claim_unrelated_test,
    _mutate_omit_a_real_test,
    _mutate_inflate_priority_score,
    _mutate_hallucinate_root_cause_id,
    _mutate_fabricate_code_coverage_module,
]


def test_critic_effectiveness_on_mutation_set():
    """Reproducible mini validation set (Section 7 methodology, scaled down):
    for every real draft x every mutation, the critic must catch the
    injected flaw (false-positive-catch-rate == 100% on this labeled set),
    while the untouched real drafts continue to pass clean
    (false-negative-rate == 0%). Both are asserted — a rubber-stamp critic
    fails the first; an overzealous one fails the second."""
    drafts, clusters, cov = _setup()
    critic = CriticAgent()
    by_id = {c.cluster_id: c for c in clusters}

    # false-negative check: honest drafts must still pass
    for d in drafts:
        verdict = critic.critique(d, by_id[d.cluster_id], cov)
        assert verdict.accepted, f"false negative: rejected an honest draft {d.cluster_id}: {verdict.flags}"

    # false-positive-catch check: every mutated draft must be rejected
    caught, total = 0, 0
    results = []
    for d in drafts:
        for mutate in MUTATORS:
            total += 1
            flawed = mutate(d)
            verdict = critic.critique(flawed, by_id[d.cluster_id], cov)
            results.append((d.cluster_id, mutate.__name__, verdict.accepted))
            if verdict.rejected:
                caught += 1

    catch_rate = caught / total
    missed = [(cid, name) for cid, name, accepted in results if accepted]
    assert catch_rate == 1.0, f"critic missed {len(missed)}/{total} injected flaws: {missed}"


def test_omitting_the_only_test_still_flagged():
    """Edge case: a singleton cluster's draft with affected_tests emptied
    out entirely must be flagged (omission of the one real test), not
    silently accepted because there's nothing left to contradict."""
    drafts, clusters, cov = _setup()
    singleton_draft = next(d for d in drafts if len(d.affected_tests) == 1)
    flawed = _mutate_omit_a_real_test(singleton_draft)
    assert flawed.affected_tests == []
    by_id = {c.cluster_id: c for c in clusters}
    verdict = CriticAgent().critique(flawed, by_id[singleton_draft.cluster_id], cov)
    assert verdict.rejected
    assert any("omits tests" in f for f in verdict.flags)


def test_checked_claims_scales_with_evidence_size():
    drafts, clusters, cov = _setup()
    by_id = {c.cluster_id: c for c in clusters}
    critic = CriticAgent()
    for d in drafts:
        v = critic.critique(d, by_id[d.cluster_id], cov)
        # at least: affected_tests check + root_cause check + priority check (3 fixed)
        # plus one per evidence item
        assert v.checked_claims >= 3 + len(d.evidence)
