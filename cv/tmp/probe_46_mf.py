"""Matched-filter hole detector for image 46.

Strategy: a 9x19 hole at s=101 px is ~0.15s = ~15 px radius. Build a synthetic
'Dark disk surrounded by bright annulus' template, slide it across the image,
find local maxima. This is mathematically equivalent to (image * template)
and is more discriminative than black-hat alone because it captures the full
radial pattern, not just 'is there dark structure here'.
"""
from __future__ import annotations
from pathlib import Path
import math
import cv2
import numpy as np

from cv.blob_detect import to_gray, crop_to_target, calibrate, _local_std
from cv.gt import load_bgr, magenta_centers


def make_template(r: int, soft: int = 2) -> np.ndarray:
    """A synthetic hole template: dark disk + bright annulus, normalized to
    zero mean and unit L2 norm (so correlation = dot product)."""
    sz = 3 * r + 2 * soft + 1
    c = sz // 2
    yy, xx = np.mgrid[0:sz, 0:sz]
    d = np.sqrt((yy - c) ** 2 + (xx - c) ** 2)
    # Template: -1 inside r, +1 in annulus [r, 2r], 0 outside
    t = np.zeros((sz, sz), dtype=np.float32)
    t[d <= r] = -1.0
    t[(d > r) & (d <= 2 * r)] = 1.0
    # Soften the edges with a small gaussian
    t = cv2.GaussianBlur(t, (2 * soft + 1, 2 * soft + 1), 0)
    # Zero-mean, unit-norm
    t -= t.mean()
    n = np.linalg.norm(t)
    if n > 1e-6:
        t /= n
    return t


def matched_filter(crop: np.ndarray, r: int) -> np.ndarray:
    """Correlation of crop with the synthetic hole template."""
    t = make_template(r)
    # matchTemplate uses sliding window; we want full convolution-equivalent.
    # Use filter2D for that (same depth, zero-padding).
    resp = cv2.filter2D(crop.astype(np.float32), cv2.CV_32F, t)
    return resp


def detect_holes_mf(crop, cal, radii=None, debug=False):
    s = cal["s_px"]
    cx, cy = cal["cx"], cal["cy"]
    r1 = cal["r_bull_px"] + 9 * s

    if radii is None:
        radii = list(range(max(4, int(0.08 * s)), int(0.25 * s) + 1))

    # For each radius, compute matched-filter response, then aggregate.
    # Pick the radius with the strongest response at the GT locations (cheating
    # in this debug script; we'll calibrate this without GT later).
    responses = {}
    best_r = radii[0]
    if debug:
        print(f"  sweeping radii {radii[0]}..{radii[-1]}")
    responses_arr = []
    for r in radii:
        resp = matched_filter(crop, r)
        responses_arr.append((r, resp))
        responses[r] = resp

    # Pick the radius whose response map has the highest 99.9-percentile
    # (strongest peak — likely the actual hole scale, since ring lines are
    # elongated and don't correlate as well with a circular template).
    best_r, best_resp_obj = max(responses_arr, key=lambda kv: np.percentile(kv[1], 99.9))
    if debug:
        print(f"  best radius: {best_r} ({best_r/s:.2f}s)")
        # Also list 99-percentile per radius
        for r, resp in responses_arr:
            print(f"    r={r}: p99.9={np.percentile(resp, 99.9):.1f} p99={np.percentile(resp, 99):.1f}")

    # Take the response at best_r, find local maxima
    resp = responses[best_r]
    resp_pos = np.maximum(resp, 0)  # holes correlate positively
    # Local maxima via dilation
    kr = max(3, int(1.0 * best_r))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * kr + 1, 2 * kr + 1))
    dil = cv2.dilate(resp_pos, kernel)
    thr = 0.30 * resp_pos.max()
    peaks = (resp_pos == dil) & (resp_pos > thr)
    ys, xs = np.where(peaks)
    candidates = [(float(x), float(y), float(resp[y, x])) for x, y in zip(xs, ys)
                  if math.hypot(x - cx, y - cy) < r1]
    if debug:
        print(f"  candidates: {len(candidates)}")
    return {"best_r": best_r, "resp": resp, "candidates": candidates, "all_resp": responses_arr}


def radial_profile(crop, cx, cy, r):
    h, w = crop.shape
    pad = int(3 * r) + 2
    x0, x1 = max(0, int(cx - pad)), min(w, int(cx + pad))
    y0, y1 = max(0, int(cy - pad)), min(h, int(cy + pad))
    patch = crop[y0:y1, x0:x1].astype(np.float32)
    if patch.size == 0:
        return None
    H, W = patch.shape
    yy, xx = np.mgrid[0:H, 0:W]
    dist = np.sqrt((yy - (cy - y0)) ** 2 + (xx - (cx - x0)) ** 2) / max(r, 1)
    bins = np.minimum(dist.astype(int), 5)
    return [float(patch[bins == b].mean()) if (bins == b).any() else 0.0 for b in range(6)]


def run():
    img_id = 46
    train = Path("resources/train")
    bgr = load_bgr(train / f"{img_id}.jpg")
    gray = to_gray(bgr)
    crop, bbox = crop_to_target(gray)
    x0, y0, w, h = bbox
    cal = calibrate(crop)
    s = cal["s_px"]
    cx, cy = cal["cx"], cal["cy"]
    r1 = cal["r_bull_px"] + 9 * s

    bgr_mark = load_bgr(train / f"{img_id}_marked.jpg")
    gt_full, _ = magenta_centers(bgr_mark)
    gt_crop = [(x - x0, y - y0) for (x, y) in gt_full]

    # -------- Step 1: probe GT responses across radii (calibration cheat) --------
    print("=== GT response across radii ===")
    radii_to_probe = list(range(4, 30))
    responses_at_gt = {r: [] for r in radii_to_probe}
    for r in radii_to_probe:
        resp = matched_filter(crop, r)
        for gx, gy in gt_crop:
            ix, iy = int(gx), int(gy)
            if 0 <= ix < crop.shape[1] and 0 <= iy < crop.shape[0]:
                responses_at_gt[r].append(float(resp[iy, ix]))
    print(f"{'r':>3} {'mean@GT':>8} {'min@GT':>8} {'max@GT':>8}")
    for r in radii_to_probe:
        vals = responses_at_gt[r]
        print(f"{r:>3} {np.mean(vals):>8.1f} {min(vals):>8.1f} {max(vals):>8.1f}")
    # Best radius = max mean response at GT
    best_r_gt = max(radii_to_probe, key=lambda r: np.mean(responses_at_gt[r]))
    print(f"\nBest r per GT mean response: {best_r_gt} ({best_r_gt/s:.2f}s)")

    # -------- Step 2: run detector and see candidates --------
    print("\n=== Detector (calibration cheat: use best_r_gt) ===")
    result = detect_holes_mf(crop, cal, radii=[best_r_gt], debug=True)
    cands = result["candidates"]
    resp_map = result["all_resp"][0][1]

    # Match candidates to GT
    print(f"\n{'#':>3} {'x':>5} {'y':>5} {'resp':>6} {'d_gt':>5} {'match':>5} {'prof':<35}")
    sorted_cands = sorted(cands, key=lambda c: -c[2])
    matched_gt = set()
    for j, (px, py, pv) in enumerate(sorted_cands[:30]):
        ds = sorted([(math.hypot(px - gx, py - gy_), i) for i, (gx, gy_) in enumerate(gt_crop)])
        d_nn, gt_i = ds[0]
        match = "*" if (d_nn < 25 and gt_i not in matched_gt) else " "
        if d_nn < 25:
            matched_gt.add(gt_i)
        prof = radial_profile(crop, px, py, best_r_gt)
        prof_s = ",".join(f"{p:.0f}" for p in prof) if prof else ""
        print(f"{j:>3} {px:>5.0f} {py:>5.0f} {pv:>6.1f} {d_nn:>5.0f} {match:>5} [{prof_s}]")

    # -------- Step 3: visualize --------
    viz = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    for x, y in gt_crop:
        cv2.circle(viz, (int(x), int(y)), int(best_r_gt * 2), (0, 255, 0), 3)
        cv2.circle(viz, (int(x), int(y)), 4, (0, 255, 0), -1)
    for x, y, v in sorted_cands[:30]:
        cv2.circle(viz, (int(x), int(y)), int(best_r_gt), (0, 0, 255), 2)
        cv2.putText(viz, f"{v:.0f}", (int(x) + 12, int(y) - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 2)
    out = Path("resources/train/intermediate_blob/46_mf.png")
    cv2.imwrite(str(out), viz)
    # Response map
    resp_vis = cv2.normalize(resp_map, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    resp_vis = cv2.applyColorMap(resp_vis, cv2.COLORMAP_JET)
    for x, y in gt_crop:
        cv2.circle(resp_vis, (int(x), int(y)), 20, (0, 255, 0), 3)
    cv2.imwrite(str(Path("resources/train/intermediate_blob/46_mf_resp.png")), resp_vis)


if __name__ == "__main__":
    run()
