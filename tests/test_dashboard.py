import json
import os
import re

from triage.dashboard import build_html, build_model
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


def test_model_totals_match_pipeline_result():
    result, tracer = _run()
    model = build_model(result, tracer)
    assert model["totals"]["clusters"] == len(result.clusters)
    assert model["totals"]["drafts"] == len(result.drafts)
    assert model["totals"]["accepted"] == len(result.accepted_drafts)
    assert model["totals"]["rejected"] == len(result.rejected_drafts)
    assert model["totals"]["needsReview"] == len(result.review_needed_clusters)


def test_model_method_counts_sum_to_cluster_count():
    result, tracer = _run()
    model = build_model(result, tracer)
    assert sum(model["methodCounts"].values()) == len(result.clusters)


def test_model_priority_distribution_sorted_descending():
    result, tracer = _run()
    model = build_model(result, tracer)
    scores = [d["score"] for d in model["priorityDistribution"]]
    assert scores == sorted(scores, reverse=True)


def test_model_clusters_view_has_one_entry_per_cluster_sorted_by_priority():
    result, tracer = _run()
    model = build_model(result, tracer)
    assert len(model["clusters"]) == len(result.clusters)
    scores = [c["priorityScore"] for c in model["clusters"]]
    assert scores == sorted(scores, reverse=True)


def test_model_json_serializable():
    result, tracer = _run()
    model = build_model(result, tracer)
    json.dumps(model)  # must not raise


def test_build_html_embeds_valid_json_and_no_placeholder_left(tmp_path):
    result, tracer = _run()
    out = tmp_path / "dashboard.html"
    build_html(result, tracer, str(out))
    html = out.read_text()
    assert "/*__DATA__*/null" not in html
    m = re.search(r"const DATA = (.*?);\n", html)
    assert m is not None
    data = json.loads(m.group(1))
    assert data["traceId"] == result.trace_id
