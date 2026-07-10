import os

from tests.test_clusterer import _all_signatures
from triage.agents.clusterer import cluster_failures
from triage.agents.drafter import EvidenceBasedDraftGenerator, draft_bug_list
from triage.parsing.coverage_parser import parse_coverage_report_file

COVERAGE_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "coverage_report.txt")


def _drafts():
    cov = parse_coverage_report_file(COVERAGE_FIXTURE)
    clusters = cluster_failures(_all_signatures())
    return draft_bug_list(clusters, cov), clusters, cov


def _draft_for(drafts, *test_name_substr):
    for d in drafts:
        if any(t in d.affected_tests for t in test_name_substr):
            return d
    raise AssertionError(f"no draft found containing {test_name_substr}")


def test_one_draft_per_cluster():
    drafts, clusters, _ = _drafts()
    assert len(drafts) == len(clusters)
    assert {d.cluster_id for d in drafts} == {c.cluster_id for c in clusters}


def test_drafts_sorted_by_priority_descending():
    drafts, _, _ = _drafts()
    scores = [d.priority_score for d in drafts]
    assert scores == sorted(scores, reverse=True)


def test_fatal_cluster_outranks_error_only_clusters():
    """The FIFO cluster contains a UVM_FATAL; it should rank at or above
    every UVM_ERROR-only cluster regardless of coverage-hole count."""
    drafts, _, _ = _drafts()
    fifo_draft = _draft_for(drafts, "uvm_test_fifo_full_write")
    others = [d for d in drafts if d.cluster_id != fifo_draft.cluster_id]
    assert all(fifo_draft.priority_score >= o.priority_score for o in others)


def test_singleton_ambiguous_cluster_flagged_and_ranks_lowest():
    drafts, _, _ = _drafts()
    addr_draft = _draft_for(drafts, "uvm_test_apb_addr_decode")
    assert addr_draft.needs_llm_review is True
    assert addr_draft.priority_score == min(d.priority_score for d in drafts)


def test_every_evidence_citation_traces_to_real_input_data():
    """Objective #3: zero unsupported claims. Every evidence citation's
    detail string must be built from data that's actually present in the
    underlying signatures/coverage report, not invented."""
    drafts, clusters, cov = _drafts()
    all_msg_ids = {mid for c in clusters for sig in c.signatures for mid in sig.msg_ids}
    all_hole_bins = {b for _, _, b in cov.coverage_holes()}
    all_modules = {cc.module for cc in cov.code_coverage}

    for d in drafts:
        for e in d.evidence:
            if e.kind == "log_event":
                assert any(mid in e.detail for mid in all_msg_ids)
            elif e.kind == "coverage_hole":
                assert any(b in e.detail for b in all_hole_bins)
            elif e.kind == "code_coverage":
                assert any(m in e.detail for m in all_modules)
            else:
                raise AssertionError(f"unexpected evidence kind: {e.kind}")


def test_affected_tests_match_cluster_membership_exactly():
    drafts, clusters, _ = _drafts()
    by_id = {c.cluster_id: c for c in clusters}
    for d in drafts:
        assert set(d.affected_tests) == set(by_id[d.cluster_id].test_names)


def test_root_cause_text_mentions_msg_id_and_hierarchy():
    drafts, _, _ = _drafts()
    fifo_draft = _draft_for(drafts, "uvm_test_fifo_full_write")
    assert "FIFO_OVERFLOW_UNPROTECTED" in fifo_draft.probable_root_cause
    assert "fifo_agent.monitor" in fifo_draft.probable_root_cause


def test_singular_test_count_grammar():
    drafts, _, _ = _drafts()
    addr_draft = _draft_for(drafts, "uvm_test_apb_addr_decode")
    assert "1 test." in addr_draft.probable_root_cause
    assert "1 tests" not in addr_draft.probable_root_cause


def test_generator_is_labeled_not_llm():
    """Guards against silently claiming LLM-authored content later without
    updating the label."""
    drafts, _, _ = _drafts()
    assert all(d.generator == "evidence_template" for d in drafts)


def test_unrelated_coverage_not_falsely_attributed():
    """A cluster with no keyword overlap to any covergroup/module should
    cite zero coverage evidence rather than grabbing an unrelated hole."""
    from triage.agents.clusterer import FailureCluster
    from triage.models import FailureSignature
    from triage.parsing.coverage_parser import parse_coverage_report

    cov = parse_coverage_report("COVERGROUP totally_unrelated_cg\n  COVERPOINT x\n    BIN y HITS 0\n")
    sig = FailureSignature(test_name="uvm_test_something_else", msg_ids=("SOME_ERROR",),
                            hierarchy_paths=("uvm_test_top.env.widget",))
    cluster = FailureCluster(cluster_id="c", signatures=[sig], method="singleton")

    gen = EvidenceBasedDraftGenerator()
    draft = gen.draft(cluster, cov)
    assert not any(e.kind == "coverage_hole" for e in draft.evidence)
