"""Probe v7: Per-image target type + dual cross-checked calibration + extrapolation.

Fixes over v6:
1. target_type is per-image (passed as arg) — v6 hardcoded air_pistol for all 10
   images. This was the root cause of the 46.jpg truncation: it is actually a
   precision_pistol target whose visible rings 6-10 were re-labelled as 1-10
   under the wrong air_pistol assumption.

2. Two-method scale calibration with cross-check:
   - Method A (ISSF black-disc, AUTHORITATIVE): detect the black disc via
     adaptive threshold + largest circular contour; recover pmm from the
     known black-disc radius per target_type. This is a fixed physical
     constant per ISSF spec.
   - Method B (HoughCircles concentric fit): run wide-radius HoughCircles
     on a Sobel edge map; for each hypothesis "largest detected circle =
     ring K", compute the pmm and score how well other detected circles
     land on other ISSF rings.
   - Cross-check: |pmm_A - pmm_B| / max(pmm_A, pmm_B). Large disagreement
     is a warning sign that target_type assumption is wrong.

3. Canvas expansion: pad the cropped image symmetrically with paper-color
   (245, 245, 245) so that extrapolated ring 1 + margin fits in the canvas.
   This addresses the "truncation" complaint for cropped targets like 46.jpg.

4. New 08_holes_magenta.png: extrapolated rings (rainbow palette) + magenta-
   filled hole detections (color #FF00FF in BGR) with per-hole score labels.

ISSF geometry (verified per research.md from Wikipedia):
    Air Pistol 10m:       card 170mm, black-disc r=29.75mm (rings 7-10)
                          ring radii [5.75, 13.75, ..., 77.75] (rings 10..1)
    Precision Pistol 25m: card 500mm scoring (550mm with paper margin),
                          black-disc r=150mm (rings 5-10)
                          ring radii [25, 50, ..., 250] (rings 10..1)
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

_HERE = Path(__file__).resolve().parent
_CV_DIR = _HERE.parent
_REPO = _CV_DIR.parent
sys.path.insert(0, str(_CV_DIR))
from detect import (  # noqa: E402
    _stage1_localize,
    _stage3_morph,
    _stage5_score,
    LOCATOR_LONG_SIDE,
    CALIBER_DIAMETER_MM,
)

TRAIN_DIR = _REPO / "resources" / "train"
OUT_DIR = _REPO / "resources" / "train" / "intermediate_v7"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ISSF ring radii (mm) indexed 0..9 for rings 10..1 (i.e. index 0 = ring 10).
ISSF_RADII_MM: dict[str, list[float]] = {
    "air_pistol":       [5.75 + 8.0 * i for i in range(10)],
    "precision_pistol": [25.0 + 25.0 * i for i in range(10)],
}

# Black-disc outer radius (mm) per target type — the outermost BLACK ring.
# Air pistol black = rings 7-10 (outer ring 7 radius = 29.75mm).
# Precision pistol black = rings 5-10 (outer ring 5 radius = 150mm).
ISSF_BLACK_RADIUS_MM: dict[str, float] = {
    "air_pistol":       29.75,
    "precision_pistol": 150.0,
}

# Card / scoring-area size per target type (mm).
ISSF_CARD_MM: dict[str, float] = {
    "air_pistol":       170.0,
    "precision_pistol": 500.0,
}

EXTRACTION_MARGIN_MM = 30.0
PAPER_COLOR = (245, 245, 245)
RAINBOW = [
    (0, 0, 255), (0, 127, 255), (0, 255, 255), (0, 255, 0),
    (255, 255, 0), (255, 127, 0), (255, 0, 0), (255, 0, 127),
    (255, 0, 255), (127, 0, 255),
]
MAGENTA = (255, 0, 255)  # BGR


def load_exif_normalized(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
    return np.asarray(im)[:, :, ::-1].copy()


# ---------------------------------------------------------------------------
# Stage 1 — bbox crop with generous expansion
# ---------------------------------------------------------------------------
def bbox_crop(img: np.ndarray, expand: float = 0.6) -> tuple[np.ndarray, dict]:
    """Stage 1 bbox crop with `expand` fractional padding each side.

    v6 used expand=0.20 which under-cropped for precision targets where the
    black disc is much smaller than the full target. v7 default is 0.60.
    """
    h0, w0 = img.shape[:2]
    long_side = max(h0, w0)
    scale = LOCATOR_LONG_SIDE / long_side if long_side > LOCATOR_LONG_SIDE else 1.0
    locator = cv2.resize(img, (int(w0 * scale), int(h0 * scale)),
                         interpolation=cv2.INTER_AREA)
    bbox, _, fail = _stage1_localize(locator, "air_pistol")
    if fail:
        return img, {"x0": 0, "y0": 0, "scale": scale, "failed": True}
    x, y, bw, bh = bbox
    sx_full = w0 / locator.shape[1]
    sy_full = h0 / locator.shape[0]
    bx0 = max(0, int((x - bw * expand) * sx_full))
    by0 = max(0, int((y - bh * expand) * sy_full))
    bx1 = min(w0, int((x + bw * (1 + expand)) * sx_full))
    by1 = min(h0, int((y + bh * (1 + expand)) * sy_full))
    return img[by0:by1, bx0:bx1], {
        "x0": bx0, "y0": by0, "scale": scale, "failed": False,
        "raw_bbox": (x, y, bw, bh),
    }


# ---------------------------------------------------------------------------
# Method A — ISSF black-disc detection (AUTHORITATIVE)
# ---------------------------------------------------------------------------
def detect_black_disc(gray: np.ndarray) -> tuple[float, float, float] | None:
    """Find the largest dark roughly-circular blob = target black portion.

    Returns (cx, cy, radius_px) where:
      - (cx, cy) is the blob's centroid via image moments
      - radius_px is the INSCRIBED circle radius via distanceTransform

    The inscribed-circle radius is robust to protrusions (dark holes outside
    the true black disc, shadows, torn paper). `cv2.minEnclosingCircle`
    overestimates when protrusions exist; equivalent-area (`sqrt(area/π)`)
    is inflated by any extra dark area. The inscribed circle is the largest
    circle centered at the blob's distance-transform peak that fits entirely
    inside the blob — this matches the physical black disc when the disc
    itself is contiguous and the protrusions are narrow extensions.
    """
    h, w = gray.shape
    binv = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        blockSize=max(51, (max(h, w) // 16) | 1), C=5,
    )
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binv = cv2.morphologyEx(binv, cv2.MORPH_OPEN, k)
    binv = cv2.morphologyEx(binv, cv2.MORPH_CLOSE, k, iterations=2)
    contours, _ = cv2.findContours(binv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_metric = -1.0
    for c in contours:
        area = cv2.contourArea(c)
        if area < 0.005 * h * w:
            continue
        perim = cv2.arcLength(c, True)
        if perim < 1:
            continue
        circ = 4 * math.pi * area / (perim * perim)
        # Relaxed circularity — dense hole clusters and slug tears make the
        # black-disc blob non-circular. Inscribed-circle radius is robust;
        # the threshold just filters out long thin shapes (shadows, edges).
        if circ < 0.20:
            continue
        metric = area * (0.5 + 0.5 * circ)
        if metric > best_metric:
            best_metric = metric
            best = c
    if best is None:
        return None

    # Mask of just this contour.
    mask = np.zeros(gray.shape, dtype=np.uint8)
    cv2.drawContours(mask, [best], -1, 255, thickness=-1)

    # Distance transform: peak value = inscribed circle radius.
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    _, max_val, _, max_loc = cv2.minMaxLoc(dist)
    # Centroid via image moments (more stable than the distance-transform
    # peak location, especially when the blob is asymmetric).
    m = cv2.moments(best)
    if m["m00"] <= 0:
        cx, cy = float(max_loc[0]), float(max_loc[1])
    else:
        cx = m["m10"] / m["m00"]
        cy = m["m01"] / m["m00"]
    return float(cx), float(cy), float(max_val)


# ---------------------------------------------------------------------------
# Method B — HoughCircles concentric fit (cross-check)
# ---------------------------------------------------------------------------
def detect_concentric_circles(gray: np.ndarray) -> list[tuple[float, float, float]]:
    """Wide-radius HoughCircles on Sobel magnitude map. Cluster duplicates."""
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
    gx = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    if mag.max() < 1e-6:
        return []
    mag_u8 = ((mag / mag.max()) * 255).astype(np.uint8)

    short_side = min(h, w)
    r_min = max(5, int(0.005 * short_side))
    r_max = int(0.55 * short_side)
    ranges = [
        (r_min, int(0.15 * short_side), 0.75),
        (int(0.10 * short_side), int(0.30 * short_side), 0.85),
        (int(0.20 * short_side), r_max, 0.90),
    ]
    all_circles: list[tuple[float, float, float]] = []
    for r_lo, r_hi, p2 in ranges:
        if r_hi <= r_lo + 5:
            continue
        min_dist = max(r_lo + 1, int(0.05 * short_side))
        circles = cv2.HoughCircles(
            mag_u8, cv2.HOUGH_GRADIENT_ALT, dp=1.5,
            minDist=min_dist, param1=80, param2=p2,
            minRadius=r_lo, maxRadius=r_hi,
        )
        if circles is None:
            continue
        for c in circles[0]:
            all_circles.append((float(c[0]), float(c[1]), float(c[2])))

    # Cluster duplicates (same ring picked up by multiple passes).
    all_circles.sort(key=lambda c: -c[2])
    clustered: list[tuple[float, float, float]] = []
    for cx, cy, r in all_circles:
        merged = False
        for i, (ccx, ccy, cr) in enumerate(clustered):
            if (math.hypot(cx - ccx, cy - ccy) < max(10, 0.02 * r) and
                    abs(r - cr) / max(r, cr) < 0.05):
                clustered[i] = ((ccx + cx) / 2, (ccy + cy) / 2, (cr + r) / 2)
                merged = True
                break
        if not merged:
            clustered.append((cx, cy, r))
    return clustered


def best_fit_pmm_from_rings(
    clustered: list[tuple[float, float, float]],
    target_type: str,
) -> tuple[float, int, float, float, int] | None:
    """Hypothesis-test each "largest detected circle = ring K" assignment.

    Returns (pmm, ring_idx_of_largest, bx, by, n_inliers) for the best fit.
    """
    if not clustered:
        return None
    cand = sorted(clustered, key=lambda c: -c[2])[:8]
    radii_mm = ISSF_RADII_MM[target_type]
    ring_spacing = (radii_mm[9] - radii_mm[0]) / 9.0

    largest = cand[0]
    cx_L, cy_L, r_L = largest

    best = None
    best_score = -1.0
    for ring_idx_L in range(1, 11):
        r_mm_L = radii_mm[10 - ring_idx_L]
        pmm_hyp = r_L / r_mm_L
        if pmm_hyp < 1.0 or pmm_hyp > 50.0:
            continue
        bx, by = cx_L, cy_L
        score = 0.0
        inliers = 0
        for cx, cy, r in cand[1:]:
            r_mm_obs = r / pmm_hyp
            best_k = min(range(10), key=lambda k: abs(r_mm_obs - radii_mm[k]))
            residual = abs(r_mm_obs - radii_mm[best_k])
            if residual < 0.3 * ring_spacing:
                score += 1.0 - residual / (0.3 * ring_spacing)
                inliers += 1
        if len(cand) > 1:
            score *= inliers / (len(cand) - 1)
        if score > best_score:
            best_score = score
            best = (pmm_hyp, ring_idx_L, bx, by, inliers)
    return best


# ---------------------------------------------------------------------------
# Canvas expansion
# ---------------------------------------------------------------------------
def expand_canvas(
    img: np.ndarray,
    bullseye: tuple[float, float],
    pmm: float,
    target_type: str,
    margin_mm: float = EXTRACTION_MARGIN_MM,
) -> tuple[np.ndarray, tuple[float, float], int, int]:
    """Pad the image symmetrically with paper color so ring-1 + margin fits.

    Returns (padded_img, new_bullseye, pad_left, pad_top).
    """
    h, w = img.shape[:2]
    bx, by = bullseye
    ring1_mm = ISSF_RADII_MM[target_type][-1]
    outer_r_px = int((ring1_mm + margin_mm) * pmm)
    left = max(0, int(outer_r_px - bx))
    right = max(0, int(outer_r_px - (w - bx)))
    top = max(0, int(outer_r_px - by))
    bot = max(0, int(outer_r_px - (h - by)))
    padded = cv2.copyMakeBorder(img, top, bot, left, right,
                                cv2.BORDER_CONSTANT, value=PAPER_COLOR)
    new_bullseye = (bx + left, by + top)
    return padded, new_bullseye, left, top


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------
def draw_rings(
    img: np.ndarray,
    bullseye: tuple[float, float],
    pmm: float,
    target_type: str,
    thickness: int = 2,
    label_color: tuple[int, int, int] = (0, 0, 0),
) -> np.ndarray:
    """Draw 10 ISSF rings on the image. Solid lines, rainbow palette."""
    out = img.copy()
    bx, by = bullseye
    radii = ISSF_RADII_MM[target_type]
    for ring in range(1, 11):
        r_mm = radii[10 - ring]
        r_px = int(r_mm * pmm)
        col = RAINBOW[(ring - 1) % len(RAINBOW)]
        cv2.circle(out, (int(bx), int(by)), r_px, col, thickness)
        # Label at top of ring (12 o'clock position).
        cv2.putText(out, str(ring),
                    (int(bx + r_px * math.cos(-math.pi / 2)) + 3,
                     int(by + r_px * math.sin(-math.pi / 2)) - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, label_color, 1)
    cv2.drawMarker(out, (int(bx), int(by)), (0, 0, 0),
                   cv2.MARKER_CROSS, 20, 2)
    return out


def draw_text(img: np.ndarray, lines: list[str]) -> np.ndarray:
    out = img.copy()
    # Draw a small black rectangle behind the text for readability.
    y = 30
    for ln in lines:
        cv2.putText(out, ln, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 255, 0), 2)
        y += 25
    return out


# ---------------------------------------------------------------------------
# Hole detection — reuses _stage3_morph from detect.py
# ---------------------------------------------------------------------------
def detect_holes(
    img_bgr: np.ndarray, pmm: float, caliber: str,
) -> tuple[list[tuple[float, float]], float]:
    """Run texture-based HoughCircles detection from detect.py.

    Returns (centroids, bullet_radius_px). Centroids are in the input-image
    coordinate frame.
    """
    bullet_diameter_mm = CALIBER_DIAMETER_MM.get(caliber, 5.7)
    bullet_radius_px = (bullet_diameter_mm / 2.0) * pmm
    _mask, centers, fail = _stage3_morph(img_bgr, bullet_radius_px)
    if fail:
        return [], bullet_radius_px
    return centers, bullet_radius_px


# ---------------------------------------------------------------------------
# Main per-image pipeline
# ---------------------------------------------------------------------------
def run_one(
    img_id: int,
    target_type: str = "air_pistol",
    caliber: str = "22lr",
) -> dict[str, Any]:
    img_path = TRAIN_DIR / f"{img_id}.jpg"
    img = load_exif_normalized(img_path)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_01_original.jpg"), img)

    crop, crop_meta = bbox_crop(img, expand=0.6)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_02_crop.png"), crop)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # --- Method A: ISSF black-disc (authoritative) -----------------------
    bd = detect_black_disc(gray)
    if bd is None:
        return {"img_id": img_id, "target_type": target_type,
                "error": "black-disc detection failed"}
    cx_bd, cy_bd, r_bd_px = bd
    pmm_A = r_bd_px / ISSF_BLACK_RADIUS_MM[target_type]
    bullseye_A = (cx_bd, cy_bd)

    # --- Method B: HoughCircles concentric fit (cross-check) -------------
    clustered = detect_concentric_circles(gray)
    fit_B = best_fit_pmm_from_rings(clustered, target_type)
    if fit_B is None:
        pmm_B: float | None = None
        bullseye_B = None
        ring_idx_L = None
        inliers_B = 0
    else:
        pmm_B, ring_idx_L, bx_B, by_B, inliers_B = fit_B
        bullseye_B = (bx_B, by_B)

    # Save edge map (the magn Sobel map from method B is computed internally;
    # re-compute here for the intermediate output).
    blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
    gx = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    mag_u8 = ((mag / max(mag.max(), 1e-6)) * 255).astype(np.uint8)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_03_sobel_edges.png"), mag_u8)

    # --- Cross-check -----------------------------------------------------
    crosscheck = "n/a (B unavailable)"
    crosscheck_pct = None
    if pmm_B is not None:
        rel = abs(pmm_A - pmm_B) / max(pmm_A, pmm_B)
        crosscheck_pct = rel * 100
        crosscheck = (
            f"Δ={rel*100:.1f}%  A={pmm_A:.2f}  B={pmm_B:.2f}  "
            f"ring_L_B={ring_idx_L}  inliers_B={inliers_B}/{len(clustered)}"
        )

    # Use method A as ground truth.
    pmm = pmm_A
    bullseye = bullseye_A

    # --- Canvas expansion to fit extrapolated ring 1 + margin -----------
    padded, new_bullseye, pad_l, pad_t = expand_canvas(
        crop, bullseye, pmm, target_type,
    )

    # --- Target mask + extracted ----------------------------------------
    h_p, w_p = padded.shape[:2]
    ring1_mm = ISSF_RADII_MM[target_type][-1]
    outer_r_px = int((ring1_mm + EXTRACTION_MARGIN_MM) * pmm)
    mask = np.zeros((h_p, w_p), dtype=np.uint8)
    cv2.circle(mask, (int(new_bullseye[0]), int(new_bullseye[1])),
               outer_r_px, 255, -1)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_04_target_mask.png"), mask)

    bg = np.full_like(padded, PAPER_COLOR, dtype=np.uint8)
    extracted = cv2.bitwise_and(padded, padded, mask=mask)
    bg_masked = cv2.bitwise_and(bg, bg, mask=cv2.bitwise_not(mask))
    extracted = cv2.add(extracted, bg_masked)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_05_target_extracted.png"), extracted)

    # --- Ring overlay (with metadata text) ------------------------------
    overlay = draw_rings(padded, new_bullseye, pmm, target_type, thickness=2)
    pmm_B_str = f"{pmm_B:.2f}" if pmm_B is not None else "n/a"
    text_lines = [
        f"target_type: {target_type}  |  caliber: {caliber}",
        f"pmm A (ISSF black-disc, AUTHORITATIVE): {pmm_A:.2f}",
        f"pmm B (HoughCircles fit): {pmm_B_str}",
        f"cross-check: {crosscheck}",
        f"black-disc radius: {r_bd_px:.0f} px "
        f"(ISSF spec {ISSF_BLACK_RADIUS_MM[target_type]:.1f} mm)",
        f"ring 1 radius: {ring1_mm * pmm:.0f} px ({ring1_mm:.1f} mm)",
        f"card {ISSF_CARD_MM[target_type]:.0f} mm at pmm {pmm:.2f} "
        f"= {ISSF_CARD_MM[target_type] * pmm:.0f} px wide",
        f"canvas padded by (l={pad_l}, t={pad_t}) to fit ring 1 + margin",
    ]
    overlay = draw_text(overlay, text_lines)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_06_ring_overlay.png"), overlay)

    # --- Rings on extracted target --------------------------------------
    final = draw_rings(extracted, new_bullseye, pmm, target_type, thickness=1)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_07_extracted_with_rings.png"), final)

    # --- Hole detection on the crop (translated to padded coords) -------
    centers_crop, bullet_radius_px = detect_holes(crop, pmm, caliber)
    centers_padded = [(cx + pad_l, cy + pad_t) for cx, cy in centers_crop]

    # Score holes against the corrected bullseye + ring-1 radius.
    scoring_radius_px = ring1_mm * pmm
    scores = _stage5_score(
        centers_padded, new_bullseye, scoring_radius_px, bullet_radius_px,
    )

    # --- Magenta overlay (NEW): rings + magenta-filled holes ------------
    magenta_img = draw_rings(extracted, new_bullseye, pmm, target_type, thickness=1)
    for i, ((cx, cy), s) in enumerate(zip(centers_padded, scores)):
        cv2.circle(magenta_img, (int(cx), int(cy)),
                   max(3, int(bullet_radius_px)), MAGENTA, -1)
        # Outline for visibility against dark ink.
        cv2.circle(magenta_img, (int(cx), int(cy)),
                   max(3, int(bullet_radius_px)), (255, 255, 255), 1)
        cv2.putText(magenta_img, f"#{i + 1}:{s}",
                    (int(cx) + 6, int(cy) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_08_holes_magenta.png"), magenta_img)

    return {
        "img_id": img_id,
        "target_type": target_type,
        "caliber": caliber,
        "crop_size": list(crop.shape[:2]),
        "padded_size": list(padded.shape[:2]),
        "pad_left_top": [pad_l, pad_t],
        "pmm_A_blackdisc": pmm_A,
        "pmm_B_rings": pmm_B,
        "crosscheck_pct": crosscheck_pct,
        "crosscheck_text": crosscheck,
        "ring_idx_of_largest_B": ring_idx_L,
        "inliers_B": inliers_B,
        "bullseye_crop": list(bullseye),
        "black_disc_radius_px": r_bd_px,
        "ring1_px": ring1_mm * pmm,
        "scoring_radius_px": scoring_radius_px,
        "bullet_radius_px": bullet_radius_px,
        "n_holes_detected": len(centers_padded),
        "scores": scores,
        "centers_padded": [[float(cx), float(cy)] for cx, cy in centers_padded],
    }


# Per-image config. Default = air_pistol; 46 overridden to precision_pistol
# per user direction (research.md follow-up). Metadata.yml says 46.jpg is
# caliber 9x19 — user clarified it is actually .22LR. Discrepancy noted.
IMAGE_CONFIG: dict[int, tuple[str, str]] = {
    1:  ("air_pistol",       "22lr"),
    4:  ("air_pistol",       "9x19"),
    6:  ("air_pistol",       ".223Rem"),
    10: ("air_pistol",       "slug"),
    12: ("air_pistol",       "9x19"),
    19: ("air_pistol",       "22lr"),
    21: ("air_pistol",       "slug"),
    29: ("air_pistol",       "22lr"),
    31: ("air_pistol",       "9x19"),   # mixed caliber per metadata.yml
    46: ("precision_pistol", "22lr"),   # per user direction (metadata says 9x19)
}


def main() -> None:
    results = []
    header = (
        f"{'id':>3}  {'target':>16}  {'cal':>7}  {'crop':>11}  "
        f"{'padded':>11}  {'pmmA':>6}  {'pmmB':>6}  {'xchk%':>6}  "
        f"{'rL_B':>4}  {'holes':>5}"
    )
    print(header)
    print("-" * len(header))
    for img_id, (target_type, caliber) in IMAGE_CONFIG.items():
        try:
            r = run_one(img_id, target_type, caliber)
            results.append(r)
            if "error" in r:
                print(f"{img_id}: ERROR: {r['error']}")
                continue
            xb = f"{r['pmm_B_rings']:.2f}" if r['pmm_B_rings'] else "  n/a"
            xc = f"{r['crosscheck_pct']:.1f}" if r['crosscheck_pct'] is not None else "  n/a"
            rl = f"{r['ring_idx_of_largest_B']}" if r['ring_idx_of_largest_B'] is not None else "-"
            print(
                f"{img_id:>3}  {target_type:>16}  {caliber:>7}  "
                f"{r['crop_size'][1]}x{r['crop_size'][0]:>4}  "
                f"{r['padded_size'][1]}x{r['padded_size'][0]:>4}  "
                f"{r['pmm_A_blackdisc']:>6.2f}  {xb:>6}  {xc:>6}  "
                f"{rl:>4}  {r['n_holes_detected']:>5}",
                flush=True,
            )
        except Exception as e:
            print(f"{img_id}: EXCEPTION: {e}", flush=True)
            import traceback
            traceback.print_exc()

    out_path = OUT_DIR / "ring_calibration_v7_results.json"
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nResults → {out_path}")
    print(f"Intermediates → {OUT_DIR}/<id>_01..08*.png")
    print("Key validation images:")
    print(f"  - <id>_06_ring_overlay.png   (rings + cross-check metadata)")
    print(f"  - <id>_07_extracted_with_rings.png  (clean target with rings)")
    print(f"  - <id>_08_holes_magenta.png  (rings + magenta hole detections)")


if __name__ == "__main__":
    main()
