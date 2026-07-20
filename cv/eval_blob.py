"""Evaluation harness for the blob-detection pipeline.

Ground truth = magenta-dot centres (cv.gt) for position, and the score
multisets in resources/paper_targets/metadata.yml for scoring. Predictions
come from cv.blob_detect. Matching is greedy bipartite by centre distance,
gated by a scale-relative tolerance derived from the self-calibrated ring
spacing s_px (so no target-type / px-per-mm assumption is needed).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import yaml


def load_metadata_scores() -> dict[int, list[int]]:
    meta = yaml.safe_load(Path("resources/paper_targets/metadata.yml").read_text())
    return {int(k.rstrip(".jpg")): list(v["hits"]) for k, v in meta.items()}


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def match_centers(
    pred: list[tuple[float, float, float]],   # (x, y, radius_px)
    gt: list[tuple[float, float]],
    tol_px: float,
) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    """Greedy bipartite matching by distance, one-to-one, gated by tol_px.

    Returns (matches, fp_idx, fn_idx) where matches are (pred_i, gt_j, dist).
    """
    pairs = []
    for i, p in enumerate(pred):
        for j, g in enumerate(gt):
            d = _dist((p[0], p[1]), g)
            if d <= tol_px:
                pairs.append((d, i, j))
    pairs.sort()
    used_p, used_g = set(), set()
    matches: list[tuple[int, int, float]] = []
    for d, i, j in pairs:
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        matches.append((i, j, d))
    fp = [i for i in range(len(pred)) if i not in used_p]
    fn = [j for j in range(len(gt)) if j not in used_g]
    return matches, fp, fn


def score_jaccard(pred_scores: list[int], gt_scores: list[int]) -> float:
    """Multiset Jaccard over score values (proxy for the ≥90% PRD bar)."""
    from collections import Counter
    a, b = Counter(pred_scores), Counter(gt_scores)
    inter = sum((a & b).values())
    union = sum((a | b).values())
    return inter / union if union else 0.0


def tolerance_px(s_px: float, pred_radius_px: float | None) -> float:
    """Match tolerance: 1.5× the predicted hole radius, floored at 0.3×s_px."""
    floor = 0.30 * s_px
    if pred_radius_px is None:
        return floor
    return max(floor, 1.5 * pred_radius_px)


def evaluate_image(
    pred_centers_radius: list[tuple[float, float, float]],
    gt_centers: list[tuple[float, float]],
    pred_scores: list[int],
    gt_scores: list[int],
    s_px: float,
) -> dict:
    # Per-prediction tolerance uses each prediction's own radius.
    tols = [tolerance_px(s_px, r) for (_, _, r) in pred_centers_radius]
    # Use the max tolerance for the matching gate (then distance sorts it out).
    gate = max(tols) if tols else 0.30 * s_px
    matches, fp, fn = match_centers(pred_centers_radius, gt_centers, gate)

    tp = len(matches)
    fp_n = len(fp)
    fn_n = len(fn)
    prec = tp / (tp + fp_n) if (tp + fp_n) else 0.0
    rec = tp / (tp + fn_n) if (tp + fn_n) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    mean_err_px = float(np.mean([d for _, _, d in matches])) if matches else float("nan")
    jac = score_jaccard(pred_scores, gt_scores)
    return {
        "n_pred": len(pred_centers_radius),
        "n_gt": len(gt_centers),
        "tp": tp, "fp": fp_n, "fn": fn_n,
        "precision": prec, "recall": rec, "f1": f1,
        "count_err": abs(len(pred_centers_radius) - len(gt_centers)),
        "mean_center_err_px": mean_err_px,
        "score_jaccard": jac,
    }


def aggregate(per_image: dict[int, dict]) -> dict:
    keys = ["precision", "recall", "f1", "score_jaccard"]
    agg = {k: float(np.mean([v[k] for v in per_image.values()])) for k in keys}
    agg["mean_count_err"] = float(np.mean([v["count_err"] for v in per_image.values()]))
    errs = [v["mean_center_err_px"] for v in per_image.values() if v["tp"] > 0]
    agg["mean_center_err_px"] = float(np.mean(errs)) if errs else float("nan")
    agg["n_images"] = len(per_image)
    return agg


def main() -> None:
    # Self-test: print metadata score counts for the train subset.
    scores = load_metadata_scores()
    for img_id in [1, 4, 6, 10, 12, 19, 21, 29, 31, 46]:
        s = scores.get(img_id, [])
        print(f"{img_id:>3}: n={len(s)} scores={s}")


if __name__ == "__main__":
    main()
