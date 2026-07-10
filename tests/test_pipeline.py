import os

from triage.pipeline import run_triage_pipeline

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _run():
    reg_text = open(os.path.join(FIXTURES, "regression_summary.log")).read()
    cov_text = open(os.path.join(FIXTURES, "coverage_report.txt")).read()
    logs_dir = os.path.join(FIXTURES, "failure_logs")
    failure_logs = {
        fname[:-4]: open(os.path.join(logs_dir, fname)).read()
        for fname in os.listdir(logs_dir)
    }
    return run_triage_pipeline(reg_text, cov_text, failure_logs)


def test_pipeline_produces_one_draft_and_verdict_per_cluster():
    result, _ = _run()
    assert len(result.clusters) == len(result.drafts) == len(result.verdicts) == 4


def test_pipeline_matches_standalone_agent_results():
    """The traced pipeline must produce identical results to calling the
    agents directly (Phase 3) — tracing must be purely observational."""
    import os as _os
    from triage.agents.clusterer import cluster_failures
    from triage.agents.drafter import draft_bug_list
    from triage.agents.critic import critique_bug_list
    from triage.parsing.coverage_parser import parse_coverage_report_file
    from triage.parsing.log_signature import build_failure_signature_file
    from triage.parsing.regression_parser import parse_regression_summary_file

    reg = parse_regression_summary_file(_os.path.join(FIXTURES, "regression_summary.log"))
    cov = parse_coverage_report_file(_os.path.join(FIXTURES, "coverage_report.txt"))
    sigs = [
        build_failure_signature_file(t.test_name, _os.path.join(FIXTURES, "failure_logs", f"{t.test_name}.log"))
        for t in reg.failures
    ]
    direct_clusters = cluster_failures(sigs)
    direct_drafts = draft_bug_list(direct_clusters, cov)
    direct_verdicts = critique_bug_list(direct_drafts, direct_clusters, cov)

    result, _ = _run()

    assert {c.cluster_id for c in result.clusters} == {c.cluster_id for c in direct_clusters}
    assert [d.priority_score for d in result.drafts] == [d.priority_score for d in direct_drafts]
    assert all(v.accepted for v in result.verdicts) == all(v.accepted for v in direct_verdicts)


def test_pipeline_runs_end_to_end_on_real_picorv32_data():
    """Real simulation data (real_data/runs/), not synthetic fixtures.

    Honest expected outcome, not a cluster-purity claim: PicoRV32's console
    output has no UVM_ERROR/msg_id/hierarchy structure, so both real
    failing tests (sub, auipc — genuinely correlated by a real seeded RTL
    bug, see README Phase 4) get empty FailureSignatures and trivially
    merge into one exact_key cluster. This test exists to prove the
    pipeline's plumbing survives real data end-to-end, not to claim
    meaningful clustering happened — that needs a UVM environment.
    """
    real_data_dir = os.path.join(FIXTURES, "..", "..", "real_data", "runs")
    reg_text = open(os.path.join(real_data_dir, "sub_bug_seeded_regression.log")).read()

    logs_dir = os.path.join(real_data_dir, "raw_logs")
    failure_logs = {}
    for fname in os.listdir(logs_dir):
        with open(os.path.join(logs_dir, fname), "rb") as f:
            raw = f.read()
        failure_logs[f"uvm_test_picorv32_{fname[:-4]}"] = raw.decode("utf-8", errors="replace")

    result, tracer = run_triage_pipeline(reg_text, "", failure_logs)

    assert result.parse_rate == 1.0
    assert len(result.clusters) == 1
    assert set(result.clusters[0].test_names) == {"uvm_test_picorv32_sub", "uvm_test_picorv32_auipc"}
    assert result.clusters[0].method == "exact_key"  # trivial merge: both signatures are empty
    assert len(result.accepted_drafts) == 1
    assert len(tracer.spans) > 0


def test_trace_has_expected_span_hierarchy():
    _, tracer = _run()
    names = [s.name for s in tracer.spans]
    for expected in ["triage.pipeline", "parse.regression_summary", "parse.coverage_report",
                      "parse.failure_logs", "agent.clusterer", "agent.drafter", "agent.critic"]:
        assert expected in names

    root = next(s for s in tracer.spans if s.name == "triage.pipeline")
    assert root.parent_span_id is None
    # every other span must trace back to the root eventually
    by_id = {s.span_id: s for s in tracer.spans}
    for s in tracer.spans:
        if s is root:
            continue
        cur = s
        depth = 0
        while cur.parent_span_id is not None and depth < 10:
            cur = by_id[cur.parent_span_id]
            depth += 1
        assert cur is root


def test_cluster_assignment_spans_carry_real_decision_data():
    _, tracer = _run()
    cluster_spans = [s for s in tracer.spans if s.name == "cluster.assignment"]
    assert len(cluster_spans) == 4
    methods = {s.attributes["cluster.method"] for s in cluster_spans}
    assert methods == {"exact_key", "similarity", "singleton"}
    review_flags = [s.attributes["cluster.needs_llm_review"] for s in cluster_spans]
    assert any(review_flags)  # the ambiguous apb_addr_decode case


def test_critic_verdict_spans_carry_real_flags():
    _, tracer = _run()
    verdict_spans = [s for s in tracer.spans if s.name == "critic.verdict"]
    assert len(verdict_spans) == 4
    assert all(s.attributes["critic.accepted"] for s in verdict_spans)
    assert all(s.attributes["critic.flag_count"] == 0 for s in verdict_spans)


def test_agent_critic_span_reports_override_rate():
    _, tracer = _run()
    critic_span = next(s for s in tracer.spans if s.name == "agent.critic")
    assert critic_span.attributes["critic.total"] == 4
    assert critic_span.attributes["critic.accepted"] == 4
    assert critic_span.attributes["critic.override_rate"] == 0.0


def test_all_spans_have_end_time_and_positive_duration():
    _, tracer = _run()
    for s in tracer.spans:
        assert s.end_time_unix_nano > 0
        assert s.duration_ms >= 0


def test_result_helper_properties():
    result, _ = _run()
    assert len(result.accepted_drafts) == 4
    assert len(result.rejected_drafts) == 0
    assert [c.cluster_id for c in result.review_needed_clusters] == \
        [c.cluster_id for c in result.clusters if c.needs_llm_review]
