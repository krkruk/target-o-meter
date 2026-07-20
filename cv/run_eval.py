"""End-to-end eval: blob_detect -> eval_blob across the 10-image train set.

Run: uv run python -m cv.run_eval
"""
from __future__ import annotations
import json
import math
from pathlib import Path

import numpy as np

from cv.blob_detect import to_gray, crop_to_target, calibrate, detect_holes, score_holes, run_one
from cv.gt import load_bgr, magenta_centers
from cv.eval_blob import evaluate_image, tolerance_px, match_centers


IDS = [1, 4, 6, 10, 12, 19, 21, 29, 31, 46]


def evaluate(img_id: int) -> dict:
    train = Path("resources/train")
    bgr = load_bgr(train / f"{img_id}.jpg")
    gray = to_gray(bgr)
    crop, bbox = crop_to_target(gray)
    x0, y0, _, _ = bbox
    cal = calibrate(crop)
    holes = detect_holes(crop, cal)
    scores = score_holes(holes, cal)

    bgr_mark = load_bgr(train / f"{img_id}_marked.jpg")
    gt_full, _ = magenta_centers(bgr_mark)
    gt_crop = [(x - x0, y - y0) for (x, y) in gt_full]

    # GT scores from metadata
    from cv.eval_blob import load_metadata_scores
    meta = load_metadata_scores()
    gt_scores = meta.get(img_id, [])

    s = cal["s_px"]
    per_img = evaluate_image(holes, gt_crop, scores, gt_scores, s)
    return {"img_id": img_id, "n_pred": len(holes), "n_gt": len(gt_crop), **per_img}


def main():
    results = {}
    print(f"{'img':>4} {'n_pred':>6} {'n_gt':>4} {'tp':>2} {'fp':>2} {'fn':>2} "
          f"{'P':>5} {'R':>5} {'F1':>5} {'cntE':>4} {'sJac':>5}")
    for img_id in IDS:
        # Make sure intermediates are fresh too
        try:
            r = evaluate(img_id)
            results[img_id] = r
            print(f"{img_id:>4} {r['n_pred']:>6} {r['n_gt']:>4} {r['tp']:>2} {r['fp']:>2} {r['fn']:>2} "
                  f"{r['precision']:>5.2f} {r['recall']:>5.2f} {r['f1']:>5.2f} "
                  f"{r['count_err']:>4} {r['score_jaccard']:>5.2f}")
        except Exception as e:
            print(f"{img_id:>4} ERROR: {e}")
    print()
    # Aggregate
    keys = ["precision", "recall", "f1", "score_jaccard"]
    agg = {k: float(np.mean([results[i][k] for i in results])) for k in keys}
    agg["mean_count_err"] = float(np.mean([results[i]["count_err"] for i in results]))
    print(f"mean: P={agg['precision']:.2f} R={agg['recall']:.2f} F1={agg['f1']:.2f} "
          f"scoreJac={agg['score_jaccard']:.2f} countErr={agg['mean_count_err']:.1f}")
    print("\nPrior (iter 9, SimpleBlobDetector): P=0.13 R=0.29 F1=0.16 scoreJac=0.22")
    print("Prior (iter 7/8, DoG best): scoreJac=0.255")


if __name__ == "__main__":
    main()
