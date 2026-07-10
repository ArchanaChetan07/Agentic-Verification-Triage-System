"""Structured records produced by the parsing layer.

These are the interchange types between Section 5.1 (Parsing Layer) and
Section 5.2 (Verification AgentMesh) of the proposal. Every downstream
agent (Clusterer, Drafter, Critic) consumes these, never raw text — so
the parsers are the only place that needs to know about UVM/UCIS syntax.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TestResult:
    """One row from a regression summary."""
    test_name: str
    seed: Optional[int]
    status: str          # "PASSED" | "FAILED" | "ERROR" (tool/env failure)
    runtime_s: float
    raw_line: str = ""

    @property
    def is_failure(self) -> bool:
        return self.status in ("FAILED", "ERROR")


@dataclass
class CoverageBin:
    name: str
    hits: int

    @property
    def is_hole(self) -> bool:
        return self.hits == 0


@dataclass
class Coverpoint:
    name: str
    bins: list[CoverageBin] = field(default_factory=list)

    @property
    def holes(self) -> list[CoverageBin]:
        return [b for b in self.bins if b.is_hole]


@dataclass
class Covergroup:
    name: str
    coverpoints: list[Coverpoint] = field(default_factory=list)
    crosses: list[Coverpoint] = field(default_factory=list)


@dataclass
class CodeCoverage:
    module: str
    line_pct: Optional[float] = None
    branch_pct: Optional[float] = None
    toggle_pct: Optional[float] = None
    fsm_pct: Optional[float] = None


@dataclass
class CoverageReport:
    covergroups: list[Covergroup] = field(default_factory=list)
    code_coverage: list[CodeCoverage] = field(default_factory=list)

    def coverage_holes(self) -> list[tuple[str, str, str]]:
        """(covergroup, coverpoint, bin) triples with zero hits."""
        holes = []
        for cg in self.covergroups:
            for cp in cg.coverpoints + cg.crosses:
                for b in cp.holes:
                    holes.append((cg.name, cp.name, b.name))
        return holes


@dataclass
class FailureEvent:
    """One UVM_ERROR/UVM_FATAL line within a failing test's log."""
    severity: str        # "UVM_ERROR" | "UVM_FATAL"
    file: str
    line: int
    sim_time: Optional[int]
    hierarchy: str        # e.g. uvm_test_top.env.scoreboard
    msg_id: str            # e.g. SCOREBOARD_MISMATCH
    message: str


@dataclass
class EvidenceCitation:
    """One piece of evidence backing a drafted bug entry — always traceable
    back to a specific parsed artifact, never a free-text LLM claim on its
    own (Objective #3: zero unsupported claims)."""
    kind: str            # "log_event" | "coverage_hole" | "code_coverage"
    detail: str          # human-readable, but built from parsed fields only


@dataclass
class BugDraft:
    """One entry in the Drafter Agent's prioritized bug list (Section 5.2)."""
    cluster_id: str
    probable_root_cause: str
    affected_tests: list[str]
    evidence: list[EvidenceCitation] = field(default_factory=list)
    priority_score: float = 0.0
    priority_rationale: str = ""
    generator: str = "evidence_template"   # "evidence_template" | "llm"
    needs_llm_review: bool = False


@dataclass
class FailureSignature:
    """Compact feature representation of a failing test, built from its log.

    This is the unit the Clusterer Agent groups on (Section 5.2). It
    deliberately does NOT include free-text messages verbatim as the
    clustering key — only IDs/paths/structure — so clustering is driven
    by structured similarity first, with LLM semantic grouping as a
    fallback for cases these features don't cleanly separate (Section 5.2).
    """
    test_name: str
    msg_ids: tuple[str, ...] = ()             # sorted, deduplicated
    hierarchy_paths: tuple[str, ...] = ()      # sorted, deduplicated module paths
    first_error_time: Optional[int] = None
    severity: str = "UVM_ERROR"                # worst severity seen
    events: list[FailureEvent] = field(default_factory=list)

    def feature_key(self) -> tuple:
        """Hashable structured-similarity key used before any LLM fallback."""
        return (self.msg_ids, self.hierarchy_paths)
