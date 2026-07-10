import os

from triage.agents.clusterer import cluster_failures, signature_similarity
from tests._shared_signatures import ALL_FAILING_TESTS, all_signatures as _all_signatures

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "failure_logs")


def _cluster_containing(clusters, test_name):
    return next(c for c in clusters if test_name in c.test_names)


def test_cluster_purity_against_seeded_ground_truth():
    """Objective #2: failures from the same seeded bug should be grouped
    together. Ground truth: 3 seeded root causes across 7 failing tests
    (alu overflow x2, fifo overflow x2, apb reset-family x3)."""
    clusters = cluster_failures(_all_signatures())

    alu_cluster = _cluster_containing(clusters, "uvm_test_alu_overflow")
    assert set(alu_cluster.test_names) == {"uvm_test_alu_overflow", "uvm_test_alu_overflow_neg"}
    assert alu_cluster.method == "exact_key"
    assert not alu_cluster.needs_llm_review

    fifo_cluster = _cluster_containing(clusters, "uvm_test_fifo_full_write")
    assert set(fifo_cluster.test_names) == {"uvm_test_fifo_full_write", "uvm_test_fifo_almost_full"}
    assert fifo_cluster.method == "exact_key"

    # every one of the 7 failing tests must land in exactly one cluster
    covered = {t for c in clusters for t in c.test_names}
    assert covered == set(ALL_FAILING_TESTS)


def test_similarity_merge_for_near_miss_same_root_cause():
    """apb_reset and apb_reset_seed2 don't share an exact feature_key (the
    seed2 run surfaces an extra secondary symptom) but are the same root
    cause — similarity-based merge should catch this without an LLM call."""
    clusters = cluster_failures(_all_signatures())
    reset_cluster = _cluster_containing(clusters, "uvm_test_apb_reset")
    assert "uvm_test_apb_reset_seed2" in reset_cluster.test_names
    assert reset_cluster.method == "similarity"
    assert not reset_cluster.needs_llm_review


def test_ambiguous_case_flagged_for_llm_review_not_merged():
    """apb_addr_decode shares hierarchy with the reset-family tests but a
    completely different msg_id — genuinely ambiguous from structure alone.
    It should NOT be silently merged, and should be flagged for the LLM
    semantic-grouping fallback described in Section 5.2."""
    clusters = cluster_failures(_all_signatures())
    addr_cluster = _cluster_containing(clusters, "uvm_test_apb_addr_decode")

    assert addr_cluster.test_names == ["uvm_test_apb_addr_decode"]
    assert addr_cluster.needs_llm_review is True

    # and it must NOT have been folded into the reset cluster
    reset_cluster = _cluster_containing(clusters, "uvm_test_apb_reset")
    assert "uvm_test_apb_addr_decode" not in reset_cluster.test_names


def test_unrelated_root_causes_never_collide():
    clusters = cluster_failures(_all_signatures())
    alu_cluster = _cluster_containing(clusters, "uvm_test_alu_overflow")
    fifo_cluster = _cluster_containing(clusters, "uvm_test_fifo_full_write")
    assert alu_cluster.cluster_id != fifo_cluster.cluster_id
    assert set(alu_cluster.test_names).isdisjoint(fifo_cluster.test_names)


def test_signature_similarity_symmetric_and_bounded():
    sigs = _all_signatures()
    for a in sigs:
        for b in sigs:
            sim = signature_similarity(a, b)
            assert 0.0 <= sim <= 1.0
            assert sim == signature_similarity(b, a)


def test_identical_signature_has_similarity_one():
    sigs = _all_signatures()
    assert signature_similarity(sigs[0], sigs[0]) == 1.0


def test_empty_input_returns_no_clusters():
    assert cluster_failures([]) == []


def test_single_signature_is_its_own_singleton_cluster():
    sigs = _all_signatures()
    clusters = cluster_failures([sigs[0]])
    assert len(clusters) == 1
    assert clusters[0].method == "singleton"
    assert clusters[0].test_names == [sigs[0].test_name]
