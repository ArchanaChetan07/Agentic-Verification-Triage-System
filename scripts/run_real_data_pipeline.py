#!/usr/bin/env python3
"""Runs the traced triage pipeline against REAL PicoRV32 simulation data
(real_data/runs/) instead of the synthetic fixtures, and writes a
dashboard.

Honest framing, read before trusting the output: this is an infra/plumbing
smoke test, not a cluster-purity result. PicoRV32's console output has no
UVM_ERROR/msg_id/hierarchy structure (see README's Phase 4 section), so
log_signature.py extracts empty FailureSignatures for every failing test
here. Two failing tests with identical (empty) signatures will trivially
get merged into one exact_key cluster — that's a real cluster in the
technical sense, but it says nothing about clustering *quality*, since
there's no structured signal to have gotten right or wrong. There's also
no real coverage report for this run (the Makefile target used doesn't
collect coverage), so an empty CoverageReport is passed rather than
fabricating one.

    python3 scripts/run_real_data_pipeline.py [output_html_path]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from triage.dashboard import build_html
from triage.pipeline import run_triage_pipeline

REAL_DATA = os.path.join(os.path.dirname(__file__), "..", "real_data")


def main(out_path: str) -> None:
    reg_text = open(os.path.join(REAL_DATA, "runs", "sub_bug_seeded_regression.log")).read()
    coverage_text = ""  # honest: no real coverage collected for this run, not fabricated

    logs_dir = os.path.join(REAL_DATA, "runs", "raw_logs")
    # Real regression test names are "uvm_test_picorv32_<name>"; raw logs are
    # keyed by "<name>" (see run_single_picorv32_test.sh) — map explicitly
    # rather than assuming naming conventions stay aligned.
    failure_logs = {}
    for fname in os.listdir(logs_dir):
        short_name = fname[:-4]
        with open(os.path.join(logs_dir, fname), "rb") as f:
            raw = f.read()
        # Real simulator output occasionally includes stray non-UTF8 bytes
        # from register-dump formatting on error paths; decode leniently
        # rather than crash or silently drop the file.
        failure_logs[f"uvm_test_picorv32_{short_name}"] = raw.decode("utf-8", errors="replace")

    result, tracer = run_triage_pipeline(reg_text, coverage_text, failure_logs)
    build_html(result, tracer, out_path)

    print(f"trace_id: {result.trace_id}")
    print(f"parse_rate: {result.parse_rate}")
    print(f"clusters={len(result.clusters)} drafts={len(result.drafts)} "
          f"accepted={len(result.accepted_drafts)} rejected={len(result.rejected_drafts)}")
    for c in result.clusters:
        print(f"  {c.cluster_id} [{c.method}] tests={c.test_names}")
    print()
    print("NOTE: this is an infra smoke test on real simulation data, not a cluster-purity")
    print("result — PicoRV32's console output has no structured UVM_ERROR/msg_id/hierarchy")
    print("signal for log_signature.py to extract (see README Phase 4). Real cluster-purity")
    print("validation needs a UVM environment (OpenTitan) where that structure exists.")
    print(f"dashboard written to {out_path}")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "real_data_dashboard.html"
    main(out)
