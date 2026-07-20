"""Characterize GT holes vs non-holes numerically on image 46.

Strategy: a hole is a dark disk at a fixed (per-image) bullet radius sitting on
brighter paper. Detect via:

  1. Estimate bullet radius from auto-correlation / black-hat response energy
     across a sweep of kernel sizes (the bullet radius should produce a peak).
  2. Morphological black-hat with that kernel -> reveals dark blobs at bullet scale.
  3. Local maxima of the black-hat response -> candidate centres.
  4. Verify each candidate by radial intensity profile (centre must be darker
     than the surrounding annulus by a calibrated margin).
"""
from __future__ import annotations
from pathlib import Path
import math
import cv2
import numpy as np

from cv.blob_detect import to_gray, crop_to_target, calibrate, _local_std, _sobel_mag
from cv.gt import load_bgr, magenta_centers


def estimate_bullet_radius(crop, s_px):
    """Estimate bullet hole radius by sweeping black-hat kernel sizes.

    A bullet hole at the target's scale sits inside one ring; for 9mm at ~30 mm
    ring spacing, that's ~9/30 = 0.3 ring. For 4.5mm air pistol at 8mm spacing,
    that's 4.5/8 = 0.56 ring (large). For 5.56mm, that's 5.56/30 = 0.19 ring.
    So we sweep kernel sizes in [0.05, 0.5] * s_px and pick the radius whose
    black-hat response has the highest energy on the black disc.
    """
    h, w = crop.shape
    # Restrict to the black disc + small margin to focus on bullet-scale dark structure
    # (paper outside has no holes; black disc inside has holes).
    crop_f = crop.astype(np.float32)
    best_r, best_score = None, -1.0
    radii = np.arange(0.05 * s_px, 0.45 * s_px, max(1.0, 0.01 * s_px))
    scores = []
    for r in radii:
        kr = max(3, int(round(r)))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * kr + 1, 2 * kr + 1))
        bh = cv2.morphologyEx(crop, cv2.MORPH_BLACKHAT, k)
        # Energy = sum of squares > top-quartile (focus on strong responses)
        flat = bh.ravel()
        thr = np.percentile(flat, 99.5)
        score = float(np.sqrt((bh[bf := bh >= thr].astype(np.float32) ** 2).mean())) if (bf := bh >= thr).any() else 0.0
        scores.append((float(r), score))
        if score > best_score:
            best_score = score
            best_r = float(r)
    return best_r, scores


def radial_profile(crop, cx, cy, r):
    """Intensity profile from centre outward, in unit-radius bins up to 3r."""
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
    profile = []
    for b in range(6):
        m = bins == b
        profile.append(float(patch[m].mean()) if m.any() else 0.0)
    return profile


def detect_holes_new(crop, cal, debug=False):
    """Black-hat + local-maxima + radial-profile verifier."""
    s = cal["s_px"]
    cx, cy = cal["cx"], cal["cy"]
    r1 = cal["r_bull_px"] + 9 * s  # target extent

    # Step 1: estimate bullet radius
    r_bullet, sweep = estimate_bullet_radius(crop, s)
    if debug:
        print(f"  estimated bullet radius: {r_bullet:.1f} px ({r_bullet/s:.2f} s)")
    # Step 2: black-hat
    kr = max(3, int(round(r_bullet)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * kr + 1, 2 * kr + 1))
    bh = cv2.morphologyEx(crop, cv2.MORPH_BLACKHAT, k)
    bh_n = cv2.normalize(bh, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

    # Step 3: candidate local maxima
    # Use dilation peaks; min distance = 1.5*r to avoid duplicates
    kr2 = max(3, int(1.0 * r_bullet))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * kr2 + 1, 2 * kr2 + 1))
    dil = cv2.dilate(bh, kernel)
    peaks = (bh == dil) & (bh > 0.35 * bh.max())
    ys, xs = np.where(peaks)
    candidates = [(float(x), float(y)) for x, y in zip(xs, ys)
                  if math.hypot(x - cx, y - cy) < r1]
    if debug:
        print(f"  raw candidates: {len(candidates)}")

    # Sub-pixel refine: centroid of black-hat response in a small window
    refined = []
    for x, y in candidates:
        win = int(1.5 * r_bullet)
        x0, x1 = max(0, int(x - win)), min(crop.shape[1], int(x + win + 1))
        y0, y1 = max(0, int(y - win)), min(crop.shape[0], int(y + win + 1))
        w_resp = bh[y0:y1, x0:x1].astype(np.float32)
        if w_resp.size == 0 or w_resp.sum() < 1e-3:
            refined.append((x, y))
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        cx_w = float((xx * w_resp).sum() / w_resp.sum())
        cy_w = float((yy * w_resp).sum() / w_resp.sum())
        refined.append((cx_w, cy_w))

    # Merge duplicates after refinement
    refined.sort()
    merged = []
    for x, y in refined:
        if merged and math.hypot(x - merged[-1][0], y - merged[-1][1]) < 0.8 * r_bullet:
            continue
        merged.append((x, y))
    if debug:
        print(f"  after refine+merge: {len(merged)}")

    # Step 4: radial-profile verifier
    # Hole: profile[0] (centre) is dark, profile[2] (paper) is bright.
    # dip = profile[2] - profile[0] must exceed a threshold.
    # The threshold is calibrated: bullet holes show dip > ~0.15 * (paper dynamic range).
    # Use the response strength as primary filter + dip as secondary.
    kept = []
    for x, y in merged:
        prof = radial_profile(crop, x, y, r_bullet)
        if prof is None:
            continue
        dip = prof[2] - prof[0]
        # Strength = black-hat response at this point
        ix, iy = int(x), int(y)
        if 0 <= ix < crop.shape[1] and 0 <= iy < crop.shape[0]:
            resp = float(bh[iy, ix])
        else:
            resp = 0.0
        kept.append((x, y, r_bullet, dip, resp, prof))

    return {
        "r_bullet": r_bullet,
        "candidates": merged,
        "kept": kept,
        "bh": bh,
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

    bgr_mark = load_bgr(train / f"{img_id}_marked.jpg")
    gt_full, _ = magenta_centers(bgr_mark)
    gt_crop = [(x - x0, y - y0) for (x, y) in gt_full]

    result = detect_holes_new(crop, cal, debug=True)
    r_b = result["r_bullet"]
    print(f"\nGT (5 expected):")
    for gx, gy in gt_crop:
        prof = radial_profile(crop, gx, gy, r_b)
        dip = prof[2] - prof[0] if prof else 0
        # Strength at GT
        bh = result["bh"]
        ix, iy = int(gx), int(gy)
        resp = float(bh[iy, ix]) if 0 <= ix < crop.shape[1] and 0 <= iy < crop.shape[0] else 0
        # nearest candidate
        ds = sorted([(math.hypot(gx - cx_), gy - cy_) for cx_, cy_ in result["candidates"]])
        d_nn = ds[0][0] if ds else 999
        print(f"  ({gx:.0f},{gy:.0f}) dip={dip:.0f} resp={resp:.0f} prof0={prof[0]:.0f} prof2={prof[2]:.0f} d_nn={d_nn:.0f}")

    # Save visualisations: black-hat response and candidates
    bh = result["bh"]
    bh_vis = cv2.normalize(bh, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    cv2.imwrite(str(Path("resources/train/intermediate_blob/46_blackhat.png")), bh_vis)

    viz = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    for x, y in gt_crop:
        cv2.circle(viz, (int(x), int(y)), int(r_b * 2), (0, 255, 0), 3)
        cv2.circle(viz, (int(x), int(y)), 5, (0, 255, 0), -1)
    for x, y, r, dip, resp, prof in result["kept"]:
        cv2.circle(viz, (int(x), int(y)), int(r), (0, 0, 255), 2)
        cv2.putText(viz, f"{dip:.0f}", (int(x) + 15, int(y) - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
    cv2.imwrite(str(Path("resources/train/intermediate_blob/46_newdetector.png")), viz)

    # Sort kept by dip descending; show top 20
    print(f"\nKept candidates ({len(result['kept'])}), sorted by dip:")
    print(f"{'x':>5} {'y':>5} {'dip':>5} {'resp':>5} {'prof':<40}")
    for x, y, r, dip, resp, prof in sorted(result["kept"], key=lambda c: -c[3])[:25]:
        prof_str = ",".join(f"{p:.0f}" for p in prof)
        print(f"{x:>5.0f} {y:>5.0f} {dip:>5.0f} {resp:>5.0f} [{prof_str}]")


if __name__ == "__main__":
    run()
