"""Characterize GT holes vs non-holes numerically on image 46.

For each GT location: extract a patch and compute features (intensity profile,
texture, gradient, circularity). For sample of false-positive locations that
the current detector flags but aren't GT: extract the same features.

Goal: find what feature threshold separates GT from FP.
"""
from __future__ import annotations
from pathlib import Path
import math
import cv2
import numpy as np

from cv.blob_detect import to_gray, crop_to_target, calibrate, detect_holes, _local_std
from cv.gt import load_bgr, magenta_centers


def patch_features(crop, cx, cy, r):
    """Features for a candidate at (cx, cy) with radius r."""
    x0, x1 = int(cx - 3 * r), int(cx + 3 * r)
    y0, y1 = int(cy - 3 * r), int(cy + 3 * r)
    h, w = crop.shape
    x0, x1 = max(0, x0), min(w, x1)
    y0, y1 = max(0, y0), min(h, y1)
    patch = crop[y0:y1, x0:x1].astype(np.float32)
    if patch.size == 0:
        return None
    H, W = patch.shape
    pcy, pcx = cy - y0, cx - x0
    yy, xx = np.mgrid[0:H, 0:W]
    dist = np.sqrt((yy - pcy) ** 2 + (xx - pcx) ** 2)
    disk = dist <= r
    annulus = (dist > r) & (dist <= 2 * r)
    outer = (dist > 2 * r) & (dist <= 3 * r)
    if disk.sum() < 4 or annulus.sum() < 4 or outer.sum() < 4:
        return None
    g = crop.astype(np.float32)
    # Intensity inside disk vs surrounding paper (outer)
    i_disk = g[y0:y1, x0:x1][disk].mean()
    i_ann = g[y0:y1, x0:x1][annulus].mean()
    i_out = g[y0:y1, x0:x1][outer].mean()
    # Local-std (texture) inside vs annulus
    lst = _local_std(crop, 15)
    lst_p = lst[y0:y1, x0:x1]
    t_disk = lst_p[disk].mean()
    t_ann = lst_p[annulus].mean()
    t_out = lst_p[outer].mean()
    # Gradient magnitude inside vs annulus
    gx = cv2.Sobel(crop.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(crop.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    gm = np.sqrt(gx * gx + gy * gy)
    gm_p = gm[y0:y1, x0:x1]
    g_disk = gm_p[disk].mean()
    g_ann = gm_p[annulus].mean()
    # Radial profile: mean intensity as a function of distance from centre
    bins = np.minimum((dist / max(r, 1)).astype(int), 5)
    profile = [g[y0:y1, x0:x1][bins == b].mean() if (bins == b).any() else 0 for b in range(6)]
    # Depth of dip at centre: profile[0] should be min for a hole
    dip = profile[2] - profile[0]  # paper minus centre
    return {
        "r": float(r),
        "i_disk": float(i_disk), "i_ann": float(i_ann), "i_out": float(i_out),
        "i_contrast": float(i_out - i_disk),  # paper - hole darkness
        "t_disk": float(t_disk), "t_ann": float(t_ann), "t_out": float(t_out),
        "t_ratio": float(t_disk / max(t_ann, 1)),
        "g_disk": float(g_disk), "g_ann": float(g_ann),
        "g_ratio": float(g_disk / max(g_ann, 1)),
        "dip": float(dip),
        "profile": [round(float(p), 1) for p in profile],
    }


def run():
    img_id = 46
    train = Path("resources/train")
    bgr = load_bgr(train / f"{img_id}.jpg")
    gray = to_gray(bgr)
    crop, bbox = crop_to_target(gray)
    x0, y0, w, h = bbox
    cal = calibrate(crop)
    s = cal["s_px"]

    # GT
    bgr_mark = load_bgr(train / f"{img_id}_marked.jpg")
    gt_full, _ = magenta_centers(bgr_mark)
    gt_crop = [(x - x0, y - y0) for (x, y) in gt_full]

    # Predictions
    pred = detect_holes(crop, cal)

    # Match GT to nearest pred (just for understanding)
    print("=== GT hole features ===")
    print("Using estimated hole radius r=14 px (auto from prior pipeline)")
    r_est = 14.0
    for i, (gx, gy) in enumerate(gt_crop):
        f = patch_features(crop, gx, gy, r_est)
        # Distance to nearest pred
        ds = sorted([(math.hypot(gx - px, gy - py), j) for j, (px, py, _) in enumerate(pred)])
        d_nn, j_nn = ds[0]
        f["d_nn"] = d_nn
        print(f"GT#{i} ({gx:.0f},{gy:.0f}) d_nn={d_nn:.1f}")
        print(f"  i_disk={f['i_disk']:.0f} i_ann={f['i_ann']:.0f} i_out={f['i_out']:.0f} contrast={f['i_contrast']:.0f}")
        print(f"  t_disk={f['t_disk']:.1f} t_ann={f['t_ann']:.1f} t_ratio={f['t_ratio']:.2f}")
        print(f"  g_disk={f['g_disk']:.1f} g_ann={f['g_ann']:.1f} g_ratio={f['g_ratio']:.2f}")
        print(f"  dip={f['dip']:.1f}  profile={f['profile']}")

    print("\n=== Predicted hole features (sorted by distance to nearest GT) ===")
    # For each prediction, distance to nearest GT
    pred_status = []
    for j, (px, py, pr) in enumerate(pred):
        ds = [(math.hypot(px - gx, gy - gy2), i) for i, (gx, gy2) in enumerate(gt_crop)]
        ds.sort()
        d_to_gt = ds[0][0]
        is_match = d_to_gt < 30
        pred_status.append((j, px, py, pr, d_to_gt, is_match))

    print(f"{'idx':>3} {'x':>5} {'y':>5} {'r':>4} {'d_gt':>5} {'match':>5} {'i_d':>4} {'i_out':>5} {'con':>4} {'t_d':>4} {'t_r':>4} {'dip':>4}")
    for j, px, py, pr, d, m in pred_status:
        f = patch_features(crop, px, py, pr)
        if f is None:
            continue
        mark = "*" if m else " "
        print(f"{j:>3} {px:>5.0f} {py:>5.0f} {pr:>4.1f} {d:>5.0f} {mark:>5} "
              f"{f['i_disk']:>4.0f} {f['i_out']:>5.0f} {f['i_contrast']:>4.0f} "
              f"{f['t_disk']:>4.1f} {f['t_ratio']:>4.2f} {f['dip']:>4.0f}")

    print("\n=== Summary ===")
    matches = [p for p in pred_status if p[5]]
    fps = [p for p in pred_status if not p[5]]
    print(f"TP (matched GT): {len(matches)}, FP: {len(fps)}, GT: {len(gt_crop)}")
    # Stats on matched vs FP
    for label, group in [("TP", matches), ("FP", fps)]:
        if not group:
            continue
        feats = [patch_features(crop, p[1], p[2], p[3]) for p in group]
        feats = [f for f in feats if f]
        if not feats:
            continue
        print(f"\n{label} (n={len(feats)}):")
        for key in ["i_disk", "i_contrast", "t_disk", "t_ratio", "g_ratio", "dip"]:
            vals = [f[key] for f in feats]
            print(f"  {key}: mean={np.mean(vals):.1f} median={np.median(vals):.1f} min={min(vals):.1f} max={max(vals):.1f}")


if __name__ == "__main__":
    run()
