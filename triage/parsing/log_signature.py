"""Log Signature Extractor (Section 5.1).

Extracts UVM_ERROR/UVM_FATAL lines from a failing test's simulation log
and reduces them to a compact FailureSignature: message IDs + module
hierarchy paths, sorted and deduplicated. This is deliberately a
*structured* feature representation, not the raw text — the Clusterer
Agent (Section 5.2) clusters on this first, falling back to LLM semantic
grouping only where these features don't cleanly separate failures.

Expected UVM log line format (standard UVM report format):

    UVM_ERROR path/to/file.sv(142) @ 1230500: uvm_test_top.env.scoreboard \
        [SCOREBOARD_MISMATCH] Expected 0x10 but got 0x00

Lines that don't match this shape are ignored (they're typically banner
lines, coverage dumps, or other non-error simulator output).
"""
from __future__ import annotations

import re

from ..models import FailureEvent, FailureSignature

_UVM_LINE_RE = re.compile(
    r"^(?P<sev>UVM_ERROR|UVM_FATAL)\s+"
    r"(?P<file>\S+)\((?P<line>\d+)\)\s+"
    r"@\s*(?P<time>\d+)\s*:\s*"
    r"(?P<hier>\S+)\s+"
    r"\[(?P<msgid>[^\]]+)\]\s*"
    r"(?P<msg>.*)$"
)

_SEVERITY_RANK = {"UVM_ERROR": 1, "UVM_FATAL": 2}


def extract_failure_events(log_text: str) -> list[FailureEvent]:
    events = []
    for raw in log_text.splitlines():
        line = raw.strip()
        m = _UVM_LINE_RE.match(line)
        if not m:
            continue
        events.append(FailureEvent(
            severity=m.group("sev"),
            file=m.group("file"),
            line=int(m.group("line")),
            sim_time=int(m.group("time")),
            hierarchy=m.group("hier"),
            msg_id=m.group("msgid"),
            message=m.group("msg").strip(),
        ))
    return events


def build_failure_signature(test_name: str, log_text: str) -> FailureSignature:
    events = extract_failure_events(log_text)

    msg_ids = tuple(sorted({e.msg_id for e in events}))
    hierarchy_paths = tuple(sorted({e.hierarchy for e in events}))
    first_error_time = min((e.sim_time for e in events if e.sim_time is not None), default=None)
    worst_severity = "UVM_ERROR"
    if events:
        worst_severity = max(events, key=lambda e: _SEVERITY_RANK.get(e.severity, 0)).severity

    return FailureSignature(
        test_name=test_name,
        msg_ids=msg_ids,
        hierarchy_paths=hierarchy_paths,
        first_error_time=first_error_time,
        severity=worst_severity,
        events=events,
    )


def build_failure_signature_file(test_name: str, path: str) -> FailureSignature:
    with open(path, "r", encoding="utf-8") as f:
        return build_failure_signature(test_name, f.read())
