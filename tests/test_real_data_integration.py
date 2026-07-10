"""Integration test: real simulation data (not synthetic fixtures) through
the Phase 2/3 pipeline, unchanged.

`real_data/runs/*.log` are real console output from an actual Icarus
Verilog simulation of PicoRV32, captured via
`scripts/run_single_picorv32_test.sh` (see README's Phase 4 section for
how they were produced and what limitations apply — notably, there is no
structured UVM_ERROR/msg_id/hierarchy signal in this data, so clustering
here can only separate "passed" from "failed", not distinguish root causes
the way it can on the synthetic UVM-style fixtures).
"""
import os

from triage.parsing.regression_parser import parse_regression_summary_file

REAL_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "real_data", "runs")


def test_parser_handles_real_clean_run_at_full_rate():
    result = parse_regression_summary_file(os.path.join(REAL_DATA_DIR, "clean_rtl_regression.log"))
    assert result.parse_rate == 1.0
    assert result.failures == []
    assert len(result.results) == 9


def test_parser_handles_real_seeded_bug_run():
    """Real SUB-as-ADD RTL bug, real Icarus Verilog simulation: sub and
    auipc both genuinely fail (auipc.S's own self-check uses `sub`
    internally — confirmed real, not a harness artifact); everything
    else in the sample passes."""
    result = parse_regression_summary_file(os.path.join(REAL_DATA_DIR, "sub_bug_seeded_regression.log"))
    assert result.parse_rate == 1.0
    failure_names = {t.test_name for t in result.failures}
    assert failure_names == {"uvm_test_picorv32_sub", "uvm_test_picorv32_auipc"}
    assert len(result.results) == 9


def test_real_seeds_are_honestly_null_not_fabricated():
    """This testbench has no $random seeding; the harness script reports
    SEED: N/A rather than inventing a plausible-looking number, and the
    parser must reflect that as seed=None, not silently coerce it."""
    result = parse_regression_summary_file(os.path.join(REAL_DATA_DIR, "sub_bug_seeded_regression.log"))
    assert all(t.seed is None for t in result.results)
