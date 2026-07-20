"""Multi-scale matched-filter + blobness verification.

Two extra discriminators added on top of the single-scale matched filter:

1. **Hessian blobness** at each response peak: real bullet holes give a peak in
   BOTH spatial directions (large |λ_min| of Hessian); ring lines give a ridge
   (one large, one near-zero eigenvalue). Score = min(|λ1|, |λ2|) / max(|λ1|, |λ2|).

2. **Multi-scale consistency**: real hole has a clear scale-space peak (one
   radius where response is maximum); elongated structures respond similarly
   across scales. Score = response_at_peak / response_at_other_radii.
"""
from __future__ import annotations
from pathlib import Path
import math
import cv2
import numpy as np

from cv.blob_detect import to_gray, crop_to_target, calibrate
from cv.gt import load_bgr, magenta_centers
from cv.tmp.probe_46_mf import matched_filter, make_template, radial_profile


def hessian_eigenvalues(resp_map, x, y):
    """Eigenvalues of the Hessian of the response map at (x, y).

    For a peak (true hole): both eigenvalues are large-negative (response drops
    in all directions). For a ridge (line): one is large-negative, one is near
    zero (flat along the line direction).
    """
    ix, iy = int(round(x)), int(round(y))
    if iy < 1 or ix < 1 or iy >= resp_map.shape[0] - 1 or ix >= resp_map.shape[1] - 1:
        return 0.0, 0.0
    dxx = resp_map[iy - 1, ix] - 2 * resp_map[iy, ix] + resp_map[iy + 1, ix]
    dyy = resp_map[iy, ix - 1] - 2 * resp_map[iy, ix] + resp_map[iy, ix + 1]
    # Off-diagonals via diagonal differences
    dxy = (resp_map[iy + 1, ix + 1] - resp_map[iy + 1, ix - 1]
           - resp_map[iy - 1, ix + 1] + resp_map[iy - 1, ix - 1]) / 4.0
    H = np.array([[dxx, dxy], [dxy, dyy]])
    ev = np.linalg.eigvalsh(H)
    return float(ev[0]), float(ev[1])  # both negative for a peak


def multiscale_responses(crop, x, y, radii):
    """Response at (x, y) for each radius."""
    out = []
    for r in radii:
        resp = matched_filter(crop, r)
        ix, iy = int(round(x)), int(round(y))
        if 0 <= ix < crop.shape[1] and 0 <= iy < crop.shape[0]:
            out.append(float(resp[iy, ix]))
        else:
            out.append(0.0)
    return out


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

    # Generate candidates at r=18 (close to best from prior probe)
    primary_r = 18
    resp = matched_filter(crop, primary_r)
    resp_pos = np.maximum(resp, 0)
    kr = max(3, int(1.0 * primary_r))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * kr + 1, 2 * kr + 1))
    dil = cv2.dilate(resp_pos, kernel)
    thr = 0.30 * resp_pos.max()
    peaks = (resp_pos == dil) & (resp_pos > thr)
    ys, xs = np.where(peaks)
    candidates = [(float(x), float(y), float(resp[y, x])) for x, y in zip(xs, ys)
                  if math.hypot(x - cx, y - cy) < r1]
    candidates.sort(key=lambda c: -c[2])
    print(f"Raw candidates at r={primary_r}: {len(candidates)}")

    # Verify each: Hessian + multi-scale
    radii = [12, 15, 18, 21, 24]
    print(f"\n{'idx':>3} {'x':>5} {'y':>5} {'resp':>6} {'d_gt':>5} {'m':>2} "
          f"{'l1':>7} {'l2':>7} {'blob':>5} {'msc':>5} {'prof0':>5} {'dip':>4}")
    matched_gt = set()
    rows = []
    for j, (px, py, pv) in enumerate(candidates):
        ds = sorted([(math.hypot(px - gx, py - gy_), i) for i, (gx, gy_) in enumerate(gt_crop)])
        d_nn, gt_i = ds[0]
        match = "*" if (d_nn < 25 and gt_i not in matched_gt) else " "
        if d_nn < 25:
            matched_gt.add(gt_i)
        # Hessian blobness
        l1, l2 = hessian_eigenvalues(resp, px, py)
        absmax = max(abs(l1), abs(l2))
        blob = min(abs(l1), abs(l2)) / absmax if absmax > 1e-6 else 0.0
        # Multi-scale: compute response at all radii
        ms = multiscale_responses(crop, px, py, radii)
        msc = max(ms) / (sum(ms) / len(ms)) if sum(ms) > 0 else 0.0  # peak / mean
        # Profile
        prof = radial_profile(crop, px, py, primary_r)
        dip = prof[2] - prof[0] if prof else 0
        rows.append((j, px, py, pv, d_nn, match, l1, l2, blob, msc, prof[0], dip))
        print(f"{j:>3} {px:>5.0f} {py:>5.0f} {pv:>6.0f} {d_nn:>5.0f} {match:>2} "
              f"{l1:>7.0f} {l2:>7.0f} {blob:>5.2f} {msc:>5.2f} {prof[0]:>5.0f} {dip:>4.0f}")

    # Summary: GT vs FP for each discriminator
    print("\n=== Discriminator stats: GT-matched vs FP ===")
    tps = [r for r in rows if r[5] == "*"]
    fps = [r for r in rows if r[5] != "*"]
    for label, group in [("TP", tps), ("FP", fps)]:
        if not group:
            print(f"{label}: none")
            continue
        blobs = [r[8] for r in group]
        mscs = [r[9] for r in group]
        dips = [r[11] for r in group]
        resps = [r[3] for r in group]
        prof0 = [r[10] for r in group]
        print(f"{label} (n={len(group)}):")
        print(f"  resp:    median={np.median(resps):.0f}  min={min(resps):.0f}  max={max(resps):.0f}")
        print(f"  blob:    median={np.median(blobs):.2f}  min={min(blobs):.2f}  max={max(blobs):.2f}")
        print(f"  msc:     median={np.median(mscs):.2f}  min={min(mscs):.2f}  max={max(mscs):.2f}")
        print(f"  dip:     median={np.median(dips):.0f}  min={min(dips):.0f}  max={max(dips):.0f}")
        print(f"  prof[0]: median={np.median(prof0):.0f}  min={min(prof0):.0f}  max={max(prof0):.0f}")


if __name__ == "__main__":
    run()
