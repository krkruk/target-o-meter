"""Empirical feature-signal probe — settles whether the bullet-hole signal
lives in pure grayscale when the right feature extractor is used.

Standalone diagnostic (does NOT touch detect.py or any other repo file).
Imports helpers from detect.py only to localize + crop the target.

Run from repo root:

    uv run python cv/feature_probe.py

Outputs:
  * /tmp/feature_maps/<id>_<method>.png   — normalized feature maps for eyeballing
  * /tmp/feature_probe_cache/             — zone overlays + measurements.json
"""
from __future__ import annotations

import os
import sys
import json
from pathlib import Path

import cv2
import numpy as np

# Allow running as a script: insert this directory's parent on sys.path so
# `from cv.detect import ...` works regardless of cwd.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from detect import _stage1_localize, _stage2_rings, LOCATOR_LONG_SIDE  # noqa: E402

RES_DIR = _HERE.parent / "resources" / "paper_targets"
OUT_DIR = Path("/tmp/feature_maps")
DEBUG_DIR = Path("/tmp/feature_probe_cache")
OUT_DIR.mkdir(parents=True, exist_ok=True)
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

IMAGES = [
    {"id": 29, "caliber": "22lr", "bullet_d_mm": 5.7, "note": "clean 5-hit all-10 baseline"},
    {"id": 19, "caliber": "22lr", "bullet_d_mm": 5.7, "note": "dense 10-hit all-10 bullseye stack"},
    {"id": 12, "caliber": "9x19", "bullet_d_mm": 9.0, "note": "long-tail incl 0-point, 13 hits"},
    {"id": 31, "caliber": "9x19", "bullet_d_mm": 9.0, "note": "mixed 9x19+22lr, 14 hits"},
    {"id": 24, "caliber": "22lr", "bullet_d_mm": 5.7, "note": "bright outlier mean=175.7"},
]

ROI_HALF = 15  # 30x30 patch


# ---------------------------------------------------------------------------
# Localization
# ---------------------------------------------------------------------------
def localize(img: np.ndarray):
    h0, w0 = img.shape[:2]
    ls = max(h0, w0)
    scale = LOCATOR_LONG_SIDE / ls if ls > LOCATOR_LONG_SIDE else 1.0
    locator = cv2.resize(img, (int(w0 * scale), int(h0 * scale)),
                         interpolation=cv2.INTER_AREA)
    bbox, _meta, fail = _stage1_localize(locator, "air_pistol")
    if fail:
        crop = img
    else:
        x, y, bw, bh = bbox
        sx, sy = w0 / locator.shape[1], h0 / locator.shape[0]
        bx0, by0 = int(x * sx), int(y * sy)
        bx1, by1 = int((x + bw) * sx), int((y + bh) * sy)
        crop = img[by0:by1, bx0:bx1]
    (cx, cy), sr, pmm, _ = _stage2_rings(crop, card_mm=170.0)
    return crop, (float(cx), float(cy)), float(sr), float(pmm)


# ---------------------------------------------------------------------------
# Zone masks (luminance + geometry — independent of features under test)
#
# Instead of picking single ROIs (fragile), we define two ZONES and report
# population statistics. The single-patch SNR the task asked for is also
# computed by sampling the median pixel of each zone — gives a representative
# ROI without biasing any single feature.
# ---------------------------------------------------------------------------
def build_zones(gray, bullseye, scoring_radius, bullet_radius_px):
    """Return (hole_zone, ink_zone, black_mask) boolean arrays.

    Geometry (Air Pistol card = 170 mm, black portion ≈ 112 mm diameter ≈
    0.66 * scoring_radius from bullseye):
      * hole_zone: disc within 0.20 * scoring_radius of bullseye — inner
                   rings where 9s/10s land for every study image.
      * ink_zone:  OUTER annulus 0.55–0.70 * scoring_radius — close to the
                   black-portion edge where there are usually no holes even
                   for long-tail targets (#12 has only one 5 and one 6).
                   Deliberately includes ring LINES so a feature that fires
                   equally on ring lines + holes shows poor discrimination.
    Both zones intersected with an Otsu-derived dark mask eroded 5x5 to drop
    the ink/paper boundary halo, and an interior margin so 30x30 patches
    never fall off-edge.
    """
    cx, cy = bullseye
    h, w = gray.shape
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)

    card_mask = dist < scoring_radius * 0.95
    card_pixels = gray[card_mask]
    if card_pixels.size == 0:
        dark_thr = 80.0
    else:
        dark_thr, _ = cv2.threshold(
            card_pixels.astype(np.uint8), 0, 255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )
        dark_thr = float(dark_thr)
    black = (gray < dark_thr).astype(np.uint8)
    black = cv2.morphologyEx(
        black, cv2.MORPH_ERODE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
    )

    hole_zone = (dist < scoring_radius * 0.20) & (black > 0)
    ink_zone = ((dist > scoring_radius * 0.55) &
                (dist < scoring_radius * 0.70) &
                (black > 0))

    # Exclude crop-boundary margin so 30x30 patches never fall off-edge.
    interior = ((xx >= ROI_HALF) & (xx < w - ROI_HALF) &
                (yy >= ROI_HALF) & (yy < h - ROI_HALF))
    hole_zone = hole_zone & interior
    ink_zone = ink_zone & interior

    if hole_zone.sum() < 100:
        hole_zone = ((dist < scoring_radius * 0.28) & (black > 0) & interior)
    if ink_zone.sum() < 100:
        ink_zone = ((dist > scoring_radius * 0.45) &
                    (dist < scoring_radius * 0.75) &
                    (black > 0) & interior)
    return hole_zone, ink_zone, black


def find_hole_roi(gray, black_mask, bullet_radius_px, w, h):
    """Find the strongest bullet-hole candidate in the black portion using
    local_std (the user's hypothesized best feature). Used as the hole ROI
    for ALL methods. NOTE: this biases the local_std measurement upward
    (we're measuring at the local_std peak). For local_std's own SNR, rely
    on pop_snr (zone population) which doesn't depend on ROI picking.

    Returns (cx, cy) of the strongest hole candidate, constrained to the
    crop interior."""
    std_map = m_local_std(gray, 15)
    std_masked = std_map * (black_mask > 0)
    # Non-max suppression via dilation; pick the strongest local maximum.
    d = max(7, int(2 * bullet_radius_px))
    if d % 2 == 0:
        d += 1
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d))
    dil = cv2.dilate(std_masked, kern)
    peaks = (std_masked == dil) & (std_masked > std_masked.max() * 0.5)
    ys, xs = np.where(peaks)
    if len(xs) == 0:
        idx = int(np.argmax(std_masked))
        return int(idx % w), int(idx // w)
    vals = std_masked[ys, xs]
    i = int(np.argmax(vals))
    hx, hy = int(xs[i]), int(ys[i])
    hx = int(max(ROI_HALF, min(w - ROI_HALF - 1, hx)))
    hy = int(max(ROI_HALF, min(h - ROI_HALF - 1, hy)))
    return hx, hy


def representative_roi(fmap, zone):
    """Pick (cx, cy) inside `zone` whose fmap value is CLOSEST to the zone
    MEDIAN — no tolerance band, exact nearest. Median (not min/max) is the
    typical patch; the nearest-to-median pixel is a representative sample."""
    ys, xs = np.where(zone)
    if len(xs) == 0:
        return None
    vals = fmap[ys, xs]
    target = float(np.median(vals))
    diffs = np.abs(vals - target)
    i = int(np.argmin(diffs))
    return int(xs[i]), int(ys[i])


def bullseye_roi(bullseye, w, h):
    """The bullseye is GUARANTEED to be a hole region for every study image
    (all 5 have multiple 9s + 10s). Clamp to the crop interior so the 30x30
    patch never falls off-edge."""
    cx = int(max(ROI_HALF, min(w - ROI_HALF - 1, bullseye[0])))
    cy = int(max(ROI_HALF, min(h - ROI_HALF - 1, bullseye[1])))
    return cx, cy


# ---------------------------------------------------------------------------
# Feature methods — each returns float32 same shape as gray
# ---------------------------------------------------------------------------
def m_luminance(g):
    return g.astype(np.float32)

def m_local_std(g, k):
    f = g.astype(np.float32)
    mean = cv2.boxFilter(f, ddepth=cv2.CV_32F, ksize=(k, k), normalize=True)
    mean_sq = cv2.boxFilter(f * f, ddepth=cv2.CV_32F, ksize=(k, k), normalize=True)
    var = np.maximum(mean_sq - mean * mean, 0.0)
    return np.sqrt(var)

def _entropy_raw(g, k):
    h, w = g.shape
    ent = np.zeros((h, w), dtype=np.float32)
    for b in range(256):
        mask = (g == b).astype(np.float32)
        if mask.sum() == 0:
            continue
        p = cv2.boxFilter(mask, ddepth=cv2.CV_32F, ksize=(k, k), normalize=True)
        contrib = np.where(p > 1e-9, -p * np.log2(p + 1e-12), 0.0)
        ent += contrib.astype(np.float32)
    return ent

def m_local_entropy(g, k=15):
    h, w = g.shape
    if max(h, w) > 700:
        s = 700.0 / max(h, w)
        g_small = cv2.resize(g, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        ent_small = _entropy_raw(g_small, k)
        return cv2.resize(ent_small, (w, h), interpolation=cv2.INTER_LINEAR)
    return _entropy_raw(g, k)

def m_dog(g, s1, s2):
    f = g.astype(np.float32)
    return cv2.GaussianBlur(f, (0, 0), s1) - cv2.GaussianBlur(f, (0, 0), s2)

def m_gabor_sum(g):
    f = g.astype(np.float32)
    accum = np.zeros_like(f)
    for theta_deg in (0, 45, 90, 135):
        theta = np.deg2rad(theta_deg)
        kern = cv2.getGaborKernel((21, 21), 4.0, theta, 10.0, 0.5, 0, ktype=cv2.CV_32F)
        resp = cv2.filter2D(f, ddepth=cv2.CV_32F, kernel=kern)
        accum += np.abs(resp)
    return accum

def m_canny(g, lo=50, hi=150):
    return cv2.Canny(g, lo, hi).astype(np.float32)

def m_canny_clahe(g):
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gc = clahe.apply(g)
    return cv2.Canny(gc, 50, 150).astype(np.float32)

def m_sobel_mag(g):
    f = g.astype(np.float32)
    gx = cv2.Sobel(f, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(f, cv2.CV_32F, 0, 1, ksize=3)
    return np.sqrt(gx * gx + gy * gy)

def m_shadow_grad(g):
    """Horizontal-only Sobel magnitude — captures the asymmetric shadow cast
    on one side of each hole under oblique-overhead lighting."""
    f = g.astype(np.float32)
    gx = cv2.Sobel(f, cv2.CV_32F, 1, 0, ksize=3)
    return np.abs(gx)


METHODS = [
    # name, fn, direction
    # direction = which side should be HIGHER for "hole" signal.
    # 'higher_at_hole': SNR = hole/ink (higher=better)
    # 'abs'           : use |value|; SNR = |hole|/|ink|
    # 'higher_in_ink' : SNR = ink/hole (invert so >1 = good)
    ("luminance",         lambda g: m_luminance(g),                "higher_at_hole"),
    ("local_std_k15",     lambda g: m_local_std(g, 15),            "higher_at_hole"),
    ("local_std_k25",     lambda g: m_local_std(g, 25),            "higher_at_hole"),
    ("local_entropy_k15", lambda g: m_local_entropy(g, 15),        "higher_at_hole"),
    ("dog_1_3",           lambda g: m_dog(g, 1.0, 3.0),            "abs"),
    ("dog_2_5",           lambda g: m_dog(g, 2.0, 5.0),            "abs"),
    ("gabor_sum",         lambda g: m_gabor_sum(g),                "higher_at_hole"),
    ("canny",             lambda g: m_canny(g),                    "higher_at_hole"),
    ("canny_clahe",       lambda g: m_canny_clahe(g),              "higher_at_hole"),
    ("sobel_mag",         lambda g: m_sobel_mag(g),                "higher_at_hole"),
    ("shadow_grad",       lambda g: m_shadow_grad(g),              "higher_at_hole"),
]


# ---------------------------------------------------------------------------
# Save feature map as PNG (8-bit display normalization)
# ---------------------------------------------------------------------------
def save_png(fmap, path, signed=False):
    arr = fmap.astype(np.float32)
    if signed:
        m = max(abs(float(arr.min())), abs(float(arr.max())))
        if m < 1e-6:
            m = 1.0
        arr = (arr / m) * 127.0 + 128.0
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    else:
        lo, hi = float(arr.min()), float(arr.max())
        if hi - lo < 1e-6:
            arr = np.zeros_like(arr, dtype=np.uint8)
        else:
            arr = ((arr - lo) / (hi - lo) * 255.0).astype(np.uint8)
        arr = cv2.equalizeHist(arr)
    cv2.imwrite(str(path), arr)


def measure_roi(fmap, cx, cy):
    x0, x1 = cx - ROI_HALF, cx + ROI_HALF
    y0, y1 = cy - ROI_HALF, cy + ROI_HALF
    patch = fmap[y0:y1, x0:x1]
    return float(patch.mean())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_one(img_id, caliber, bullet_d_mm, note):
    img_path = RES_DIR / f"{img_id}.jpg"
    img = cv2.imread(str(img_path))
    if img is None:
        return None
    crop, bull, sr, pmm = localize(img)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    bullet_radius_px = (bullet_d_mm / 2.0) * pmm

    hole_zone, ink_zone, black_mask = build_zones(
        gray, bull, sr, bullet_radius_px
    )

    # Zone overlay (luminance-only) so the user can verify zone geometry.
    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    zone_vis = overlay.copy()
    zone_vis[hole_zone] = (0, 0, 255)
    zone_vis[ink_zone] = (0, 255, 0)
    overlay = cv2.addWeighted(overlay, 0.55, zone_vis, 0.45, 0)
    # hole ROI = auto-detected local_std peak (red rect)
    bx, by = find_hole_roi(
        gray, black_mask, bullet_radius_px, gray.shape[1], gray.shape[0]
    )
    cv2.rectangle(overlay, (bx - ROI_HALF, by - ROI_HALF),
                  (bx + ROI_HALF, by + ROI_HALF), (0, 0, 255), 2)
    cv2.circle(overlay, (int(bull[0]), int(bull[1])), 3, (255, 255, 0), -1)
    cv2.imwrite(str(DEBUG_DIR / f"{img_id}_zones.png"), overlay)
    cv2.imwrite(str(DEBUG_DIR / f"{img_id}_black_mask_eroded.png"), black_mask * 255)

    save_png(gray.astype(np.float32), OUT_DIR / f"{img_id}_luminance.png", signed=False)

    # Hole ROI: auto-detected strongest local_std peak in black portion.
    # NOTE — biases local_std measurement upward; for local_std SNR rely on
    # pop_snr (zone-based, ROI-independent).
    hole_roi_xy = find_hole_roi(
        gray, black_mask, bullet_radius_px, gray.shape[1], gray.shape[0]
    )

    rows = []
    for name, fn, direction in METHODS:
        fmap = fn(gray)
        signed = direction == "abs"
        save_png(fmap, OUT_DIR / f"{img_id}_{name}.png", signed=signed)

        # population-based percentiles
        hole_vals = fmap[hole_zone]
        ink_vals = fmap[ink_zone]
        if direction == "abs":
            hole_vals_m = np.abs(hole_vals)
            ink_vals_m = np.abs(ink_vals)
        else:
            hole_vals_m = hole_vals
            ink_vals_m = ink_vals
        hole_p50 = float(np.percentile(hole_vals_m, 50))
        ink_p50 = float(np.percentile(ink_vals_m, 50))
        if direction == "higher_in_ink":
            pop_snr = (ink_p50 / hole_p50) if hole_p50 > 1e-9 else 0.0
        else:
            pop_snr = (hole_p50 / ink_p50) if ink_p50 > 1e-9 else 0.0

        # ink ROI: nearest-to-median pixel in ink_zone
        ink_roi_xy = representative_roi(fmap, ink_zone)
        h_mean = measure_roi(fmap, *hole_roi_xy)
        if ink_roi_xy is None:
            i_mean = float("nan")
            patch_snr = float("nan")
        else:
            i_mean = measure_roi(fmap, *ink_roi_xy)
            if direction == "abs":
                patch_snr = (abs(h_mean) / abs(i_mean)) if abs(i_mean) > 1e-9 else 0.0
            elif direction == "higher_in_ink":
                patch_snr = (i_mean / h_mean) if h_mean > 1e-9 else 0.0
            else:
                patch_snr = (h_mean / i_mean) if i_mean > 1e-9 else 0.0

        rows.append({
            "image": img_id,
            "method": name,
            "hole_p50": hole_p50,
            "ink_p50": ink_p50,
            "pop_snr": pop_snr,
            "patch_hole_mean": h_mean,
            "patch_ink_mean": i_mean,
            "patch_snr": patch_snr,
            "direction": direction,
            "hole_roi_xy": hole_roi_xy,
            "ink_roi_xy": ink_roi_xy,
        })
    info = {
        "img_id": img_id,
        "caliber": caliber,
        "note": note,
        "crop_shape": list(gray.shape),
        "bullseye": list(bull),
        "scoring_radius_px": sr,
        "px_per_mm": pmm,
        "bullet_radius_px": bullet_radius_px,
        "hole_zone_n": int(hole_zone.sum()),
        "ink_zone_n": int(ink_zone.sum()),
    }
    return rows, info


def main():
    all_rows = []
    infos = []
    for img in IMAGES:
        print(f"Processing {img['id']}.jpg ...", flush=True)
        out = run_one(img["id"], img["caliber"], img["bullet_d_mm"], img["note"])
        if out is None:
            print("  SKIP — could not read")
            continue
        rows, info = out
        all_rows.extend(rows)
        infos.append(info)
        print(f"  crop={info['crop_shape']} pmm={info['px_per_mm']:.2f} "
              f"hole_zone_n={info['hole_zone_n']} ink_zone_n={info['ink_zone_n']}")

    with open(DEBUG_DIR / "measurements.json", "w") as fh:
        json.dump({"rows": all_rows, "infos": infos}, fh, indent=2)
    print(f"\nSaved {len(all_rows)} measurements to {DEBUG_DIR}/measurements.json")
    print(f"Saved feature PNGs to {OUT_DIR}/")


if __name__ == "__main__":
    main()
