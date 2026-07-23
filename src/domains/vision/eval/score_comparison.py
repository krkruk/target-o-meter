"""Score-multiset comparison between LLM output and metadata.yml ground truth.

Ported verbatim from ``cv/phase3_spike/compare.py`` (71 LOC at commit 76f6fc4).

Per the Step-1 interview (Q4/Q6): comparison is by SCORE (counts of each
score value 0..10), not by hole position. Positional metrics (F1/precision/
recall vs magenta GT) are intentionally NOT computed here — separate axis,
not what the user asked for in Step 1.

These are pure functions (acceptable under the one-class-per-file rule per
``lessons.md`` — the rule explicitly permits ``ports.py`` / ``dtos.py`` as
contract collections; eval helpers follow the same exception because they are
the eval module's typed contract).
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable


def score_multiset(scores: Iterable[int]) -> Counter:
    """Multiset (Counter) of ISSF scores 0..10. X already mapped to 10 upstream.

    Ported from cv/phase3_spike/compare.py:18-20.
    """
    return Counter(int(s) for s in scores)


def score_jaccard(llm: Counter, gt: Counter) -> float:
    """Multiset Jaccard over score values 0..10.

    J = |intersection| / |union| where for multisets
        intersection count = sum over v of min(llm[v], gt[v])
        union count        = sum over v of max(llm[v], gt[v])

    Returns 1.0 when both are empty (convention: no holes == match).

    Ported from cv/phase3_spike/compare.py:23-36.
    """
    keys = set(llm) | set(gt)
    if not keys:
        return 1.0
    inter = sum(min(llm[k], gt[k]) for k in keys)
    union = sum(max(llm[k], gt[k]) for k in keys)
    return inter / union if union else 1.0


def exact_count_match(llm: Counter, gt: Counter) -> bool:
    """True if LLM found exactly the same number of holes as GT.
    Ported from cv/phase3_spike/compare.py:39-41.
    """
    return sum(llm.values()) == sum(gt.values())


def per_score_breakdown(llm: Counter, gt: Counter) -> dict:
    """Per-score (0..10) LLM vs GT counts + delta. For the per-image table.
    Ported from cv/phase3_spike/compare.py:44-53.
    """
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
    metadata.yml, so every discrepancy is surfaced neutrally.

    Ported from cv/phase3_spike/compare.py:56-70.
    """
    flags: list[str] = []
    n_llm = sum(llm.values())
    n_gt = sum(gt.values())
    if n_llm != n_gt:
        flags.append(f"hole count differs: llm={n_llm} gt={n_gt} (delta {n_llm - n_gt:+d})")
    for v in range(0, 11):
        d = llm.get(v, 0) - gt.get(v, 0)
        if d != 0:
            flags.append(f"score {v:>2}: llm={llm.get(v, 0)} gt={gt.get(v, 0)} (delta {d:+d})")
    return flags
