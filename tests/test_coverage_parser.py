import os

from triage.parsing.coverage_parser import parse_coverage_report, parse_coverage_report_file

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "coverage_report.txt")


def test_parses_covergroups_and_coverpoints():
    report = parse_coverage_report_file(FIXTURE)
    names = {cg.name for cg in report.covergroups}
    assert names == {"alu_cg", "fifo_cg", "apb_cg"}

    alu_cg = next(cg for cg in report.covergroups if cg.name == "alu_cg")
    assert len(alu_cg.coverpoints) == 1
    assert alu_cg.coverpoints[0].name == "op_type"
    assert len(alu_cg.coverpoints[0].bins) == 4
    assert len(alu_cg.crosses) == 1
    assert alu_cg.crosses[0].name == "op_type_x_operand_sign"


def test_parses_code_coverage():
    report = parse_coverage_report_file(FIXTURE)
    modules = {cc.module: cc for cc in report.code_coverage}
    assert modules["alu_unit"].line_pct == 87.5
    assert modules["alu_unit"].branch_pct == 72.1
    assert modules["alu_unit"].toggle_pct == 65.0
    assert modules["alu_unit"].fsm_pct == 90.0
    assert modules["apb_slave"].line_pct == 76.3


def test_coverage_holes_detected():
    report = parse_coverage_report_file(FIXTURE)
    holes = report.coverage_holes()
    hole_names = {(cg, cp, b) for cg, cp, b in holes}
    assert ("alu_cg", "op_type", "overflow_add") in hole_names
    assert ("alu_cg", "op_type", "overflow_sub") in hole_names
    assert ("alu_cg", "op_type_x_operand_sign", "add_neg_neg") in hole_names
    assert ("fifo_cg", "fill_level", "full") in hole_names
    assert ("apb_cg", "reset_state", "reset_during_write") in hole_names
    # a hit bin should never show up as a hole
    assert not any(name == "add" for _, _, name in holes)


def test_bin_hit_counts_preserved():
    report = parse_coverage_report(
        "COVERGROUP g\n  COVERPOINT cp\n    BIN a HITS 5\n    BIN b HITS 0\n"
    )
    cp = report.covergroups[0].coverpoints[0]
    hits = {b.name: b.hits for b in cp.bins}
    assert hits == {"a": 5, "b": 0}
    assert [b.name for b in cp.holes] == ["b"]
