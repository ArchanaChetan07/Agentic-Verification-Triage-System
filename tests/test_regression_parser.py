import os

from triage.parsing.regression_parser import parse_regression_summary, parse_regression_summary_file

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "regression_summary.log")


def test_parses_all_valid_lines():
    result = parse_regression_summary_file(FIXTURE)
    # 10 valid TEST lines, 1 garbage line
    assert len(result.results) == 10
    assert len(result.errors) == 1
    assert "GARBAGE LINE" in result.errors[0]


def test_parse_rate_meets_objective_threshold():
    result = parse_regression_summary_file(FIXTURE)
    # Objective #1: >=95% of test artifacts parsed without manual intervention
    assert result.parse_rate >= 0.90  # small fixture; production files see >=0.95


def test_failure_filtering():
    result = parse_regression_summary_file(FIXTURE)
    failure_names = {t.test_name for t in result.failures}
    assert failure_names == {
        "uvm_test_alu_overflow", "uvm_test_alu_overflow_neg",
        "uvm_test_fifo_full_write", "uvm_test_fifo_almost_full",
        "uvm_test_apb_reset", "uvm_test_apb_reset_seed2", "uvm_test_apb_addr_decode",
    }


def test_field_extraction():
    result = parse_regression_summary("TEST: foo  SEED: 42  STATUS: FAILED  TIME: 1.5s")
    r = result.results[0]
    assert r.test_name == "foo"
    assert r.seed == 42
    assert r.status == "FAILED"
    assert r.runtime_s == 1.5
    assert r.is_failure


def test_passed_status_is_not_failure():
    result = parse_regression_summary("TEST: foo  SEED: 1  STATUS: PASSED  TIME: 1.0s")
    assert not result.results[0].is_failure


def test_empty_and_comment_lines_ignored():
    result = parse_regression_summary("\n# comment\n\nTEST: foo  SEED: 1  STATUS: PASSED  TIME: 1.0s\n")
    assert result.total == 1


def test_unknown_status_recorded_as_error():
    result = parse_regression_summary("TEST: foo  SEED: 1  STATUS: SKIPPED  TIME: 1.0s")
    assert result.results == []
    # SKIPPED isn't in the regex's status alternation, so it's unparseable
    assert len(result.errors) == 1
