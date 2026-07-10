"""Builds the self-contained triage observability dashboard (single HTML
file), mirroring AgentMesh's own `dashboard.py` pattern: read pipeline
artifacts, compact into a JSON model, embed directly in a template so the
file opens anywhere with no server.

Section 5.3 of the proposal: "Prometheus + Grafana + OTel Collector,
tracking agent-level latency, clustering confidence distributions, critic
override rate, and end-to-end triage throughput." This module produces the
same underlying span/decision data (real spans from `pipeline.py`, not
placeholders) as a portable single-file dashboard — a real Prometheus/
Grafana wiring is the natural next step once this is running against real
regressions at volume (see README), reusing the vendored submodule's
existing `observability/prometheus.yml`/`otel-collector-config.yaml`
unmodified, since the span shape is already OTLP-compatible.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "agentmesh"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from agentmesh.telemetry import Tracer  # noqa: E402

from .pipeline import TriageRunResult


def build_model(result: TriageRunResult, tracer: Tracer) -> dict:
    spans = [s.to_dict() for s in tracer.spans]

    stage_spans = [s for s in spans if s["name"].startswith(("triage.", "parse.", "agent."))]
    stage_timeline = [
        {"name": s["name"], "durationMs": s["durationMs"], "attributes": s["attributes"]}
        for s in sorted(stage_spans, key=lambda s: s["startTimeUnixNano"])
    ]

    cluster_spans = [s for s in spans if s["name"] == "cluster.assignment"]
    method_counts: dict[str, int] = {}
    for s in cluster_spans:
        m = s["attributes"]["cluster.method"]
        method_counts[m] = method_counts.get(m, 0) + 1

    critic_span = next((s for s in spans if s["name"] == "agent.critic"), None)
    verdict_spans = [s for s in spans if s["name"] == "critic.verdict"]

    draft_spans = [s for s in spans if s["name"] == "draft.bug_entry"]
    priority_dist = sorted(
        [{"clusterId": s["attributes"]["draft.cluster_id"],
          "score": s["attributes"]["draft.priority_score"],
          "rationale": s["attributes"]["draft.priority_rationale"]} for s in draft_spans],
        key=lambda d: d["score"], reverse=True,
    )

    clusters_view = []
    by_cluster_id_draft = {s["attributes"]["draft.cluster_id"]: s for s in draft_spans}
    by_cluster_id_verdict = {s["attributes"]["critic.cluster_id"]: s for s in verdict_spans}
    for s in cluster_spans:
        cid = s["attributes"]["cluster.id"]
        draft = by_cluster_id_draft.get(cid, {}).get("attributes", {})
        verdict = by_cluster_id_verdict.get(cid, {}).get("attributes", {})
        clusters_view.append({
            "clusterId": cid,
            "method": s["attributes"]["cluster.method"],
            "size": s["attributes"]["cluster.size"],
            "needsLlmReview": s["attributes"]["cluster.needs_llm_review"],
            "tests": s["attributes"]["cluster.tests"],
            "priorityScore": draft.get("draft.priority_score"),
            "rootCauseGenerator": draft.get("draft.generator"),
            "criticAccepted": verdict.get("critic.accepted"),
            "criticFlags": verdict.get("critic.flags", []),
        })
    clusters_view.sort(key=lambda c: c["priorityScore"] or 0, reverse=True)

    return {
        "traceId": result.trace_id,
        "parseRate": result.parse_rate,
        "stageTimeline": stage_timeline,
        "methodCounts": method_counts,
        "criticSummary": (critic_span or {}).get("attributes", {}),
        "priorityDistribution": priority_dist,
        "clusters": clusters_view,
        "totals": {
            "clusters": len(result.clusters),
            "drafts": len(result.drafts),
            "accepted": len(result.accepted_drafts),
            "rejected": len(result.rejected_drafts),
            "needsReview": len(result.review_needed_clusters),
        },
    }


def build_html(result: TriageRunResult, tracer: Tracer, out_path: str) -> None:
    model = build_model(result, tracer)
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "dashboard_template.html")) as f:
        template = f.read()
    html = template.replace("/*__DATA__*/null", json.dumps(model))
    with open(out_path, "w") as f:
        f.write(html)
