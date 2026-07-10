"""Coverage Parser (Section 5.1).

Parses a simplified UCIS-compatible *text* export — the kind most simulators
(VCS urg -format text, Questa vcover report, Verilator --annotate) can be
converted to with a small conversion script — into structured coverage
data: functional covergroups/coverpoints/crosses with bin hit counts, and
per-module code coverage (line/branch/toggle/FSM percentages).

Expected format:

    COVERGROUP alu_cg
      COVERPOINT op_type
        BIN add HITS 120
        BIN sub HITS 0
      CROSS op_type_x_operand_sign
        BIN add_pos_pos HITS 45

    CODE_COVERAGE MODULE alu_unit
      LINE 87.5
      BRANCH 72.1
      TOGGLE 65.0
      FSM 90.0

Indentation is not semantically required (matched by keyword), but the
parser is line-oriented and expects one directive per line.
"""
from __future__ import annotations

import re

from ..models import CodeCoverage, CoverageBin, CoverageReport, Covergroup, Coverpoint

_COVERGROUP_RE = re.compile(r"^COVERGROUP\s+(\S+)", re.IGNORECASE)
_COVERPOINT_RE = re.compile(r"^COVERPOINT\s+(\S+)", re.IGNORECASE)
_CROSS_RE = re.compile(r"^CROSS\s+(\S+)", re.IGNORECASE)
_BIN_RE = re.compile(r"^BIN\s+(\S+)\s+HITS\s+(\d+)", re.IGNORECASE)
_CODE_COV_RE = re.compile(r"^CODE_COVERAGE\s+MODULE\s+(\S+)", re.IGNORECASE)
_METRIC_RE = re.compile(r"^(LINE|BRANCH|TOGGLE|FSM)\s+([\d.]+)", re.IGNORECASE)


def parse_coverage_report(text: str) -> CoverageReport:
    report = CoverageReport()

    current_cg: Covergroup | None = None
    current_cp: Coverpoint | None = None
    current_cc: CodeCoverage | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if m := _COVERGROUP_RE.match(line):
            current_cg = Covergroup(name=m.group(1))
            report.covergroups.append(current_cg)
            current_cp, current_cc = None, None
            continue

        if m := _CODE_COV_RE.match(line):
            current_cc = CodeCoverage(module=m.group(1))
            report.code_coverage.append(current_cc)
            current_cg, current_cp = None, None
            continue

        if current_cc is not None:
            if m := _METRIC_RE.match(line):
                metric, value = m.group(1).upper(), float(m.group(2))
                setattr(current_cc, {
                    "LINE": "line_pct", "BRANCH": "branch_pct",
                    "TOGGLE": "toggle_pct", "FSM": "fsm_pct",
                }[metric], value)
                continue

        if current_cg is not None:
            if m := _COVERPOINT_RE.match(line):
                current_cp = Coverpoint(name=m.group(1))
                current_cg.coverpoints.append(current_cp)
                continue
            if m := _CROSS_RE.match(line):
                current_cp = Coverpoint(name=m.group(1))
                current_cg.crosses.append(current_cp)
                continue
            if current_cp is not None and (m := _BIN_RE.match(line)):
                current_cp.bins.append(CoverageBin(name=m.group(1), hits=int(m.group(2))))
                continue

    return report


def parse_coverage_report_file(path: str) -> CoverageReport:
    with open(path, "r", encoding="utf-8") as f:
        return parse_coverage_report(f.read())
