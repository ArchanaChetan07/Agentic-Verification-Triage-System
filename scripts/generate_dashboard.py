#!/usr/bin/env python3
"""Runs the traced triage pipeline against the fixture regression and
writes a self-contained observability dashboard HTML file.

    python3 scripts/generate_dashboard.py [output_path]

This uses the synthetic fixtures (tests/fixtures/), not real data — see
README's Phase 4 section for the real PicoRV32 data this same pipeline
was separately validated against (test_real_data_integration.py covers
that path through the parser; wiring real_data/runs/*.log through this
full dashboared pipeline is a natural next step, not yet done).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from triage.dashboard import build_html
from triage.pipeline import run_triage_pipeline

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")


def main(out_path: str) -> None:
    reg_text = open(os.path.join(FIXTURES, "regression_summary.log")).read()
    cov_text = open(os.path.join(FIXTURES, "coverage_report.txt")).read()
    logs_dir = os.path.join(FIXTURES, "failure_logs")
    failure_logs = {
        fname[:-4]: open(os.path.join(logs_dir, fname)).read()
        for fname in os.listdir(logs_dir)
    }

    result, tracer = run_triage_pipeline(reg_text, cov_text, failure_logs)
    build_html(result, tracer, out_path)

    print(f"trace_id: {result.trace_id}")
    print(f"clusters={len(result.clusters)} drafts={len(result.drafts)} "
          f"accepted={len(result.accepted_drafts)} rejected={len(result.rejected_drafts)} "
          f"needs_review={len(result.review_needed_clusters)}")
    print(f"dashboard written to {out_path}")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "triage_dashboard.html"
    main(out)
