"""Clusterer Agent (Section 5.2).

Groups failing tests by root-cause signature using a *hybrid* approach:

  1. Exact structured match — tests whose FailureSignature.feature_key()
     (msg_ids, hierarchy_paths) is identical are merged with full confidence.
     No LLM call needed; this is pure code.
  2. Fuzzy structured similarity — for signatures that don't share an exact
     key (e.g. one test surfaces an extra secondary symptom), a weighted
     Jaccard similarity over msg_ids and hierarchy_paths decides whether to
     auto-merge, flag for LLM semantic review, or leave separate.
  3. LLM semantic grouping fallback — signatures in the "ambiguous" band
     (ROUTE_TO_LLM_BAND below) are the ones the proposal's Section 5.2
     describes routing to an LLM call via the Mesh/AdaptiveRouter. That
     call is NOT implemented here — this module only decides *which*
     signatures need it and packages the evidence for that call.

This module is deliberately LLM-free and fully deterministic/testable: it's
the part of the Clusterer that can be cluster-purity-tested (Objective #2)
without any model in the loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import FailureSignature

# Weighted similarity: hierarchy match matters slightly less than shared
# message IDs, since two unrelated bugs in the same module/testbench
# component are plausible, but two unrelated bugs raising the exact same
# message ID are not.
MSG_ID_WEIGHT = 0.6
HIERARCHY_WEIGHT = 0.4

# similarity >= AUTO_MERGE_THRESHOLD -> merge automatically, no LLM needed
AUTO_MERGE_THRESHOLD = 0.6
# REVIEW_THRESHOLD <= similarity < AUTO_MERGE_THRESHOLD -> flag for LLM
# semantic grouping fallback (Section 5.2); below REVIEW_THRESHOLD, treat
# as genuinely separate root causes.
REVIEW_THRESHOLD = 0.25


def _jaccard(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    if not union:
        return 1.0
    return len(sa & sb) / len(union)


def signature_similarity(a: FailureSignature, b: FailureSignature) -> float:
    """Weighted similarity in [0, 1] between two failure signatures."""
    msg_sim = _jaccard(a.msg_ids, b.msg_ids)
    hier_sim = _jaccard(a.hierarchy_paths, b.hierarchy_paths)
    return round(MSG_ID_WEIGHT * msg_sim + HIERARCHY_WEIGHT * hier_sim, 4)


@dataclass
class FailureCluster:
    cluster_id: str
    signatures: list[FailureSignature] = field(default_factory=list)
    method: str = "exact_key"          # "exact_key" | "similarity" | "singleton"
    needs_llm_review: bool = False      # True if any pairwise link was in the review band
    min_pairwise_similarity: float = 1.0

    @property
    def test_names(self) -> list[str]:
        return [s.test_name for s in self.signatures]

    @property
    def size(self) -> int:
        return len(self.signatures)


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[ry] = rx


def cluster_failures(signatures: list[FailureSignature]) -> list[FailureCluster]:
    """Cluster failing tests by root-cause signature (Objective #2).

    Returns clusters covering every input signature. Clusters formed purely
    from exact key matches are `method="exact_key"`; clusters formed (or
    extended) via similarity above AUTO_MERGE_THRESHOLD are
    `method="similarity"`; any signature whose best link to another falls in
    the REVIEW_THRESHOLD..AUTO_MERGE_THRESHOLD band is left in its own
    cluster with `needs_llm_review=True`, signalling the LLM semantic
    grouping fallback described in Section 5.2 should run on it.
    """
    n = len(signatures)
    if n == 0:
        return []

    uf = _UnionFind(n)
    pair_sim: dict[tuple[int, int], float] = {}

    for i in range(n):
        for j in range(i + 1, n):
            sim = signature_similarity(signatures[i], signatures[j])
            pair_sim[(i, j)] = sim
            exact = signatures[i].feature_key() == signatures[j].feature_key()
            if exact or sim >= AUTO_MERGE_THRESHOLD:
                uf.union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    clusters: list[FailureCluster] = []
    for k, (_, indices) in enumerate(sorted(groups.items())):
        sigs = [signatures[i] for i in indices]
        min_sim = 1.0
        any_exact = True
        for a in range(len(indices)):
            for b in range(a + 1, len(indices)):
                i, j = sorted((indices[a], indices[b]))
                sim = pair_sim[(i, j)]
                min_sim = min(min_sim, sim)
                if signatures[i].feature_key() != signatures[j].feature_key():
                    any_exact = False

        method = "singleton" if len(indices) == 1 else ("exact_key" if any_exact else "similarity")
        needs_review = False
        if len(indices) == 1:
            # Check whether this singleton's best link to ANY other signature
            # (even one it didn't merge with) falls in the review band —
            # that's the "structured features don't cleanly separate" case.
            idx = indices[0]
            best = max(
                (pair_sim[tuple(sorted((idx, other)))] for other in range(n) if other != idx),
                default=0.0,
            )
            needs_review = REVIEW_THRESHOLD <= best < AUTO_MERGE_THRESHOLD

        clusters.append(FailureCluster(
            cluster_id=f"cluster_{k:03d}",
            signatures=sigs,
            method=method,
            needs_llm_review=needs_review,
            min_pairwise_similarity=min_sim,
        ))

    return clusters
