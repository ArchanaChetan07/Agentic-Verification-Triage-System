"""Regression Parser (Section 5.1).

Ingests a regression summary — one line per test, `key: value` pairs
separated by whitespace — into structured TestResult records.

Expected line format (matches common UVM regression-runner output, e.g.
from Makefile-based flows around VCS/Questa/Xcelium or Verilator+cocotb
wrappers):

    TEST: uvm_test_alu_overflow  SEED: 88213  STATUS: FAILED  TIME: 6.8s

Blank lines and lines starting with '#' are ignored. Malformed lines are
collected in `errors` on the returned RegressionParseResult rather than
raising, so one bad line in a multi-thousand-line file doesn't kill the
whole parse.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import TestResult

_LINE_RE = re.compile(
    r"TEST:\s*(?P<test>\S+)\s+"
    r"SEED:\s*(?P<seed>\S+)\s+"
    r"STATUS:\s*(?P<status>PASSED|FAILED|ERROR)\s+"
    r"TIME:\s*(?P<time>[\d.]+)s?",
    re.IGNORECASE,
)

VALID_STATUSES = {"PASSED", "FAILED", "ERROR"}


@dataclass
class RegressionParseResult:
    results: list[TestResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results) + len(self.errors)

    @property
    def parse_rate(self) -> float:
        """Fraction of lines parsed without manual intervention (Objective #1)."""
        if self.total == 0:
            return 1.0
        return len(self.results) / self.total

    @property
    def failures(self) -> list[TestResult]:
        return [r for r in self.results if r.is_failure]


def parse_regression_summary(text: str) -> RegressionParseResult:
    out = RegressionParseResult()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.search(line)
        if not m:
            out.errors.append(f"line {lineno}: unparseable: {line!r}")
            continue
        seed_str = m.group("seed")
        seed = int(seed_str) if seed_str.isdigit() else None
        status = m.group("status").upper()
        if status not in VALID_STATUSES:
            out.errors.append(f"line {lineno}: unknown status {status!r}")
            continue
        out.results.append(TestResult(
            test_name=m.group("test"),
            seed=seed,
            status=status,
            runtime_s=float(m.group("time")),
            raw_line=raw,
        ))
    return out


def parse_regression_summary_file(path: str) -> RegressionParseResult:
    with open(path, "r", encoding="utf-8") as f:
        return parse_regression_summary(f.read())
