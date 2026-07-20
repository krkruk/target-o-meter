"""Collect features for all TP and FP candidates across the train set.

For each image, run detection and classify each candidate as TP (matches GT
within 25px) or FP. Record (blob, dip, dip_ratio, prof0, prof2, resp, ratio_r)
so we can find global thresholds that separate TP from FP.
"""
from __future__ import annotations
import math
import json
from pathlib import Path
import cv2
import numpy as np

from cv.blob_detect import to_gray, crop_to_target, calibrate, _matched_filter, _hessian_blobness, _radial_profile
from cv.gt import load_bgr, magenta_centers

IDS = [1, 4, 6, 10, 12, 19, 21, 29, 31, 46]


def gather_features(img_id):
    train = Path("resources/train")
    bgr = load_bgr(train / f"{img_id}.jpg")
    gray = to_gray(bgr)
    crop, bbox = crop_to_target(gray)
    x0, y0, _, _ = bbox
    cal = calibrate(crop)
    s = cal["s_px"]
    cx, cy = cal["cx"], cal["cy"]
    r_target = cal["r_bull_px"] + 9 * s
    r_bw = cal["r_bw_px"]

    bgr_mark = load_bgr(train / f"{img_id}_marked.jpg")
    gt_full, _ = magenta_centers(bgr_mark)
    gt_crop = [(x - x0, y - y0) for (x, y) in gt_full]

    # Compute responses at all radii
    ratios = [0.05, 0.08, 0.11, 0.14, 0.18, 0.22, 0.28, 0.36]
    radii = sorted({max(3, int(round(f * s))) for f in ratios})
    stack = np.zeros((len(radii),) + crop.shape, dtype=np.float32)
    for i, r in enumerate(radii):
        stack[i] = _matched_filter(crop, r)

    # Target mask
    H, W = crop.shape
    yy, xx = np.mgrid[0:H, 0:W]
    a = max(cal.get("semi_a", r_bw), 1.0)
    b = max(cal.get("semi_b", a), 1.0)
    major = cal.get("major_dir", np.array([1.0, 0.0]))
    dx = xx - cx
    dy = yy - cy
    proj_maj = dx * major[0] + dy * major[1]
    proj_min = dx * (-major[1]) + dy * major[0]
    dist_metric = np.sqrt((proj_maj / a) ** 2 + (proj_min / b) ** 2) * r_bw
    target_mask = (dist_metric <= r_target).astype(np.uint8)

    # Generate pooled candidates (per-radius NMS, threshold 0.45*max_in)
    pooled = []
    for i, r in enumerate(radii):
        resp = stack[i]
        resp_pos = np.maximum(resp, 0)
        in_target = resp[target_mask > 0]
        max_in = float(in_target.max()) if in_target.size else 0.0
        if max_in < 1.0:
            continue
        thr = 0.45 * max_in
        kr = max(5, int(1.0 * r))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * kr + 1, 2 * kr + 1))
        dil = cv2.dilate(resp_pos, kernel)
        peaks = (resp_pos == dil) & (resp_pos > thr) & (target_mask > 0)
        ys, xs = np.where(peaks)
        for x, y in zip(xs, ys):
            pooled.append((float(x), float(y), float(r), float(resp[y, x])))

    # Dedup
    pooled.sort(key=lambda c: -c[3])
    clusters = []
    for x, y, r, v in pooled:
        if any(math.hypot(x - cx_, y - cy_) < 0.6 * s for cx_, cy_, _, _ in clusters):
            continue
        clusters.append((x, y, r, v))

    # Compute features per cluster
    crop_in_target = crop[target_mask > 0]
    prof0_ceil = float(np.percentile(crop_in_target, 50))

    rows = []
    matched_gt = set()
    # Match each candidate to GT (greedy by response descending — already sorted)
    for x, y, r, v in clusters:
        i_r = radii.index(int(round(r))) if int(round(r)) in radii else 0
        resp_map = stack[i_r]
        blob = _hessian_blobness(resp_map, x, y)
        prof = _radial_profile(crop, x, y, r)
        if prof is None:
            continue
        prof0, prof2 = prof[0], prof[2]
        dip = prof2 - prof0
        dip_ratio = dip / max(prof2, 1.0)
        # Distance to nearest unmatched GT
        ds = sorted([(math.hypot(x - gx, y - gy), i) for i, (gx, gy) in enumerate(gt_crop)])
        d_nn, gt_i = ds[0] if ds else (999, -1)
        is_tp = (d_nn < 25 and gt_i not in matched_gt)
        if is_tp:
            matched_gt.add(gt_i)
        rows.append({
            "img": img_id, "x": x, "y": y, "r": r, "r_resp_v": v,
            "blob": blob, "dip": dip, "dip_ratio": dip_ratio,
            "prof0": prof0, "prof2": prof2,
            "d_nn_gt": d_nn, "is_tp": is_tp,
        })
    return rows


def main():
    all_rows = []
    for img_id in IDS:
        try:
            rows = gather_features(img_id)
            n_tp = sum(1 for r in rows if r["is_tp"])
            print(f"img {img_id}: {len(rows)} candidates, {n_tp} TP")
            all_rows.extend(rows)
        except Exception as e:
            print(f"img {img_id}: ERROR {e}")
    print(f"\nTotal: {len(all_rows)} candidates, {sum(1 for r in all_rows if r['is_tp'])} TP")

    # Stats
    tps = [r for r in all_rows if r["is_tp"]]
    fps = [r for r in all_rows if not r["is_tp"]]
    print(f"\nFeature stats:")
    print(f"{'feature':>12} | {'TP med':>8} {'TP min':>8} {'TP max':>8} | {'FP med':>8} {'FP min':>8} {'FP max':>8}")
    for key in ["blob", "dip", "dip_ratio", "prof0", "prof2", "r_resp_v"]:
        tvals = [r[key] for r in tps]
        fvals = [r[key] for r in fps]
        if tvals and fvals:
            print(f"{key:>12} | {np.median(tvals):>8.2f} {min(tvals):>8.2f} {max(tvals):>8.2f} | "
                  f"{np.median(fvals):>8.2f} {min(fvals):>8.2f} {max(fvals):>8.2f}")

    # Try thresholds
    print("\nTrying combinations of thresholds:")
    print(f"{'blob':>6} {'dip_r':>6} {'TP':>4} {'FP':>5} {'F1':>5}")
    for blob_t in [0.30, 0.40, 0.50, 0.60]:
        for dip_r_t in [0.10, 0.15, 0.20, 0.25]:
            kept_tp = sum(1 for r in tps if r["blob"] >= blob_t and r["dip_ratio"] >= dip_r_t)
            kept_fp = sum(1 for r in fps if r["blob"] >= blob_t and r["dip_ratio"] >= dip_r_t)
            prec = kept_tp / (kept_tp + kept_fp) if (kept_tp + kept_fp) else 0
            rec = kept_tp / len(tps) if tps else 0
            f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
            print(f"{blob_t:>6.2f} {dip_r_t:>6.2f} {kept_tp:>4} {kept_fp:>5} {f1:>5.2f} (P={prec:.2f} R={rec:.2f})")

    # Save to JSON for further analysis
    Path("cv/tmp/features.json").write_text(json.dumps(all_rows, indent=2, default=float))


if __name__ == "__main__":
    main()
