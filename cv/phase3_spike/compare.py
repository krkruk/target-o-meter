"""Score-multiset comparison between LLM output and metadata.yml ground truth.

Per the Step-1 interview (Q4/Q6): we compare by SCORE (counts of each score
value 0..10), not by hole position — the user will manually review the images
vs GT afterward. We also flag misalignments so the user can re-check
metadata.yml if their own counts were off.

Positional metrics (F1/precision/recall vs magenta GT) are intentionally NOT
computed here — they're a separate axis and not what the user asked for in
Step 1.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable


def score_multiset(scores: Iterable[int]) -> Counter:
    """Multiset (Counter) of ISSF scores 0..10. X already mapped to 10 upstream."""
    return Counter(int(s) for s in scores)


def score_jaccard(llm: Counter, gt: Counter) -> float:
    """Multiset Jaccard over score values 0..10.

    J = |intersection| / |union| where for multisets
        intersection count = sum over v of min(llm[v], gt[v])
        union count        = sum over v of max(llm[v], gt[v])
    Returns 1.0 when both are empty (convention: no holes == match).
    """
    keys = set(llm) | set(gt)
    if not keys:
        return 1.0
    inter = sum(min(llm[k], gt[k]) for k in keys)
    union = sum(max(llm[k], gt[k]) for k in keys)
    return inter / union if union else 1.0


def exact_count_match(llm: Counter, gt: Counter) -> bool:
    """True if LLM found exactly the same number of holes as GT."""
    return sum(llm.values()) == sum(gt.values())


def per_score_breakdown(llm: Counter, gt: Counter) -> dict:
    """Per-score (0..10) LLM vs GT counts + delta. For the per-image table."""
    rows = {}
    for v in range(0, 11):
        rows[v] = {
            "llm": int(llm.get(v, 0)),
            "gt": int(gt.get(v, 0)),
            "delta": int(llm.get(v, 0)) - int(gt.get(v, 0)),
        }
    return rows


def misalignment_flags(llm: Counter, gt: Counter) -> list[str]:
    """Human-readable flags the user should review against metadata.yml.

    These are NOT LLM errors a priori — per Q6 the user can mis-count in
    metadata.yml, so we surface every discrepancy neutrally.
    """
    flags: list[str] = []
    n_llm = sum(llm.values())
    n_gt = sum(gt.values())
    if n_llm != n_gt:
        flags.append(f"hole count differs: llm={n_llm} gt={n_gt} (delta {n_llm - n_gt:+d})")
    for v in range(0, 11):
        d = llm.get(v, 0) - gt.get(v, 0)
        if d != 0:
            flags.append(f"score {v:>2}: llm={llm.get(v,0)} gt={gt.get(v,0)} (delta {d:+d})")
    return flags
