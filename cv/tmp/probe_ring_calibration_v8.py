"""Probe v8: Per-image precision_pistol + 3-iteration differential calibration +
Anisotropic metric (no image rotation) + two-stage hole detection.

Fixes over v7 (all driven by user feedback on intermediate_v7 outputs):
1. ALL train images are precision_pistol 25m (550×550 card, 500mm scoring
   area, 10-ring Ø50mm, inner-10 Ø25mm, ring spacing 25mm). v7 incorrectly
   treated 9 of 10 as air_pistol.
2. Three-iteration differential calibration:
   - Iter 1 (coarse): pmm from black-disc inscribed-circle radius.
     Black-disc outer = 150mm (precision rings 5-10).
   - Iter 2 (refine): HoughCircles concentric fit. For each detected ring,
     find the best ISSF ring assignment that minimizes residual; update pmm.
     Constrained to ±30% of iter-1 pmm to prevent wild jumps.
   - Iter 3 (fine-tune): radial-profile residual minimization. Sample the
     gray value at each radius from the bullseye, find local minima (printed
     ring strokes), shift pmm by the signed mean residual, capped at ±15%.
   Stop early if |Δpmm| / pmm < 2%.
3. Anisotropic metric WITHOUT rotating the image (user direction):
   - Estimate black-disc ellipse (cv2.fitEllipse) → axes (major, minor) + angle.
   - Compute per-axis px-per-mm: sx = semi_major / 150, sy = semi_minor / 150.
   - For scoring, convert each pixel's displacement from bullseye into mm via
     rotation into the ellipse-aligned frame, then per-axis division.
   - For visualization, draw rings as ELLIPSES (cv2.ellipse) so the overlay
     visually matches the apparent ellipticity in the photo. The image itself
     is never rotated.
4. Two-stage hole detection (user direction):
   - Stage 1 (coarse, huge error margin): DoG + wide radius range +
     percentile threshold + liberal area filter → many candidates, accept
     false positives.
   - Stage 2 (fine per-candidate verifier): for each candidate, extract a
     small ROI and run a local SNR / matched-filter test against an ideal
     bullet-diameter disk. Drop candidates that fail.
   This replaces the brittle "tune HoughCircles param2 until counts look
   right" approach.
5. ISSF Precision Pistol ring table extended with inner-10 (X-ring) at
   12.5mm radius.

Geometry reference (ISSF Precision Pistol 25m):
    Inner-10 (X): r=12.5mm  Ø25mm
    Ring 10: r=25mm   Ø50mm
    Ring  9: r=50mm   Ø100mm
    Ring  8: r=75mm
    Ring  7: r=100mm
    Ring  6: r=125mm
    Ring  5: r=150mm  (black-disc outer)
    Ring  4: r=175mm
    Ring  3: r=200mm
    Ring  2: r=225mm
    Ring  1: r=250mm  (scoring-area outer; card 550mm incl margin)
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
from detect import _stage1_localize, _stage5_score, LOCATOR_LONG_SIDE, CALIBER_DIAMETER_MM  # noqa: E402

TRAIN_DIR = _REPO / "resources" / "train"
OUT_DIR = _REPO / "resources" / "train" / "intermediate_v8"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ISSF Precision Pistol — radii in mm. Index 0 = inner-10 (X), 1..10 = rings 10..1.
RING_RADII_MM: list[float] = [12.5, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0,
                              175.0, 200.0, 225.0, 250.0]
# ISSF ring index (1..10) → position in RING_RADII_MM (1-based: ring 10 = idx 1, ring 1 = idx 10)
def ring_radius_mm(ring_1_to_10: int) -> float:
    """ring_1_to_10: 1..10. Returns the radius in mm."""
    return RING_RADII_MM[11 - ring_1_to_10]

BLACK_DISC_R_MM = 150.0  # outer of ring 5
CARD_SCORING_MM = 500.0
CARD_PHYSICAL_MM = 550.0
EXTRACTION_MARGIN_MM = 30.0
PAPER_COLOR = (245, 245, 245)
RAINBOW = [
    (255, 255, 255),  # X / inner-10 — white (visible on black disc)
    (0, 0, 255), (0, 127, 255), (0, 255, 255), (0, 255, 0),
    (255, 255, 0), (255, 127, 0), (255, 0, 0), (255, 0, 127),
    (255, 0, 255),
]
MAGENTA = (255, 0, 255)  # BGR

# Per-image caliber mapping. All targets are precision_pistol 25m.
IMAGE_CALIBER: dict[int, str] = {
    1: "22lr", 4: "9x19", 6: ".223Rem", 10: "slug", 12: "9x19",
    19: "22lr", 21: "slug", 29: "22lr", 31: "9x19",  # mixed per metadata.yml
    46: "22lr",  # user direction; metadata says 9x19
}


def load_exif_normalized(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
    return np.asarray(im)[:, :, ::-1].copy()


# ---------------------------------------------------------------------------
# Stage 0 — bbox crop
# ---------------------------------------------------------------------------
def bbox_crop(img: np.ndarray, expand: float = 0.4) -> tuple[np.ndarray, dict]:
    h0, w0 = img.shape[:2]
    long_side = max(h0, w0)
    scale = LOCATOR_LONG_SIDE / long_side if long_side > LOCATOR_LONG_SIDE else 1.0
    locator = cv2.resize(img, (int(w0 * scale), int(h0 * scale)),
                         interpolation=cv2.INTER_AREA)
    bbox, _, fail = _stage1_localize(locator, "air_pistol")  # type only for shape
    if fail:
        return img, {"x0": 0, "y0": 0, "scale": scale, "failed": True}
    x, y, bw, bh = bbox
    sx_full = w0 / locator.shape[1]
    sy_full = h0 / locator.shape[0]
    bx0 = max(0, int((x - bw * expand) * sx_full))
    by0 = max(0, int((y - bh * expand) * sy_full))
    bx1 = min(w0, int((x + bw * (1 + expand)) * sx_full))
    by1 = min(h0, int((y + bh * (1 + expand)) * sy_full))
    return img[by0:by1, bx0:bx1], {"x0": bx0, "y0": by0, "scale": scale}


# ---------------------------------------------------------------------------
# Iteration 1 — coarse pmm from black disc + affine ellipse estimate
# ---------------------------------------------------------------------------
def detect_black_disc(
    gray: np.ndarray,
    bullet_radius_mm: float = 2.85,
) -> dict | None:
    """Find the largest dark blob = target black portion.

    Returns dict with:
      cx, cy: blob centroid (image moments)
      inscribed_r_px: largest inscribed circle radius via distanceTransform
      ellipse: (center, axes, angle) from cv2.fitEllipse — for affine estimate
      contour: the raw contour (for visualization)

    Robustness for dense-hole targets (image 19 = all-10s 22lr; slug targets):
      - Two-pass morphology: first clean with small kernel, then close with
        a kernel sized to the expected bullet holes. The closing kernel must
        be larger than 2*bullet_radius_px to fill holes inside the disc, but
        small enough that it doesn't merge the disc with surrounding dark area.
      - Low circularity threshold (0.10) — dense hole clusters + slug tears
        make the contour very irregular.
      - If no blob found at first closing size, retry with progressively
        larger kernels (fallback for dense-hole targets).
    """
    h, w = gray.shape
    binv0 = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        blockSize=max(51, (max(h, w) // 16) | 1), C=5,
    )
    k_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binv0 = cv2.morphologyEx(binv0, cv2.MORPH_OPEN, k_small)

    # Progressive closing-kernel sizes based on bullet size.
    # Bullet radius is unknown in px until we know pmm, so estimate pmm
    # coarsely from image dimensions: assume the target fills ~50% of crop.
    coarse_pmm = 0.5 * min(h, w) / 250.0  # 250mm = ring 1 radius
    bullet_r_px_est = max(3.0, bullet_radius_mm * coarse_pmm)
    base_close_ks = max(7, int(2.5 * bullet_r_px_est))
    if base_close_ks % 2 == 0:
        base_close_ks += 1
    close_ks_candidates = [base_close_ks, base_close_ks + 8, base_close_ks + 16]

    for close_ks in close_ks_candidates:
        k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_ks, close_ks))
        binv = cv2.morphologyEx(binv0, cv2.MORPH_CLOSE, k_close, iterations=1)
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
            if circ < 0.10:
                continue
            metric = area * (0.3 + 0.7 * circ)
            if metric > best_metric:
                best_metric = metric
                best = c
        if best is not None and len(best) >= 5:
            break

    if best is None or len(best) < 5:
        return None

    mask = np.zeros(gray.shape, dtype=np.uint8)
    cv2.drawContours(mask, [best], -1, 255, thickness=-1)
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    _, max_val, _, max_loc = cv2.minMaxLoc(dist)
    m = cv2.moments(best)
    if m["m00"] <= 0:
        cx, cy = float(max_loc[0]), float(max_loc[1])
    else:
        cx = m["m10"] / m["m00"]
        cy = m["m01"] / m["m00"]

    ellipse = cv2.fitEllipse(best)

    return {
        "cx": float(cx),
        "cy": float(cy),
        "inscribed_r_px": float(max_val),
        "ellipse": ellipse,
        "contour": best,
        "close_ks_used": close_ks,
    }


# ---------------------------------------------------------------------------
# Anisotropic metric (no image rotation — user direction)
# ---------------------------------------------------------------------------
def compute_anisotropic_metric(
    ellipse: tuple[tuple[float, float], tuple[float, float], float],
    black_disc_r_mm: float = BLACK_DISC_R_MM,
) -> dict[str, float]:
    """From a black-disc cv2.fitEllipse result, derive the per-axis px/mm
    scales and rotation angle that map pixel displacements back to mm.

    The image itself is NOT rotated. The affine parameters (sx, sy, angle)
    are used to compute anisotropic distances for scoring and to draw rings
    as ellipses for visualization.

    Returns dict with:
      cx, cy: bullseye (ellipse center)
      axis_major_px, axis_minor_px: FULL axis lengths (per cv2.fitEllipse)
      angle_deg: major-axis angle from horizontal (cv2 convention)
      sx, sy: px-per-mm along major / minor axis directions
      pmm_avg: geometric-mean px/mm — single-number approximation
      anisotropy: axis_major / axis_minor (1.0 = no perspective)
    """
    (cx, cy), (axis_major, axis_minor), angle_deg = ellipse
    # cv2.fitEllipse returns FULL axis lengths. Per-axis scale = semi-axis / radius_mm.
    sx = (axis_major / 2.0) / black_disc_r_mm
    sy = (axis_minor / 2.0) / black_disc_r_mm
    pmm_avg = math.sqrt(sx * sy)  # geometric mean — robust average
    anisotropy = axis_major / max(axis_minor, 1.0)
    return {
        "cx": float(cx),
        "cy": float(cy),
        "axis_major_px": float(axis_major),
        "axis_minor_px": float(axis_minor),
        "angle_deg": float(angle_deg),
        "sx": float(sx),
        "sy": float(sy),
        "pmm_avg": float(pmm_avg),
        "anisotropy": float(anisotropy),
    }


def anisotropic_distance_mm(
    x: float,
    y: float,
    metric: dict[str, float],
) -> float:
    """Distance from bullseye in mm, accounting for anisotropic scale.

    Algorithm:
      1. Compute pixel offset from bullseye.
      2. Rotate into the ellipse-aligned frame (major/minor axis directions).
      3. Divide each component by the per-axis px/mm scale.
      4. Return the hypot — this is the radius (in mm) of the ISSF ring that
         the point lies on, accounting for camera tilt.
    """
    dx = x - metric["cx"]
    dy = y - metric["cy"]
    theta = math.radians(metric["angle_deg"])
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    # cv2.fitEllipse angle is the major-axis direction. Rotate offset so
    # x-axis aligns with major axis.
    dx_major = dx * cos_t + dy * sin_t
    dy_minor = -dx * sin_t + dy * cos_t
    dx_mm = dx_major / metric["sx"]
    dy_mm = dy_minor / metric["sy"]
    return math.hypot(dx_mm, dy_mm)


def score_to_ring_anisotropic(
    distance_mm: float,
    bullet_radius_mm: float,
) -> int:
    """ISSF line-break rule with anisotropic distance: subtract bullet
    radius from the radial distance, then map to ring 1..10 (or 0).
    """
    adj = max(0.0, distance_mm - bullet_radius_mm)
    score = 10 - int(math.floor((adj - ring_radius_mm(10))
                                / (ring_radius_mm(1) - ring_radius_mm(10)) * 9.0))
    return max(0, min(10, score))


# ---------------------------------------------------------------------------
# Iteration 2 — HoughCircles concentric ring fit (refines pmm)
# ---------------------------------------------------------------------------
def detect_concentric_circles(gray: np.ndarray) -> list[tuple[float, float, float]]:
    """Wide-radius HoughCircles on Sobel magnitude map; cluster duplicates."""
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


def fit_pmm_to_issf_rings(
    clustered: list[tuple[float, float, float]],
    pmm_init: float,
    pmm_constraint_pct: float = 0.30,
) -> tuple[float, list[dict[str, Any]]]:
    """Find the best pmm such that detected rings land on ISSF ring positions.

    Strategy: only the radius matters (post-affine-warp, bullseye is fixed at
    image center). For each detected ring radius r_px, hypothesize that it
    corresponds to ISSF ring K (1..10), giving pmm_hyp = r_px / ring_radius_mm(K).
    Score each hypothesis by counting how many other detected rings also land
    near ISSF ring positions at this pmm. Pick the best.

    The hypothesis pmm must be within ±`pmm_constraint_pct` of `pmm_init` to
    prevent the ring-fit from jumping to a wildly wrong assignment (which
    happens when HoughCircles over-detects small false-positive circles).

    Returns (best_pmm, assignments) where assignments is a list of dicts
    {cx, cy, r_px, r_mm_obs, ring, residual_mm} for each detected ring.
    """
    if not clustered:
        return pmm_init, []

    cand = sorted(clustered, key=lambda c: -c[2])[:10]
    pmm_lo = pmm_init * (1.0 - pmm_constraint_pct)
    pmm_hi = pmm_init * (1.0 + pmm_constraint_pct)

    best_pmm = pmm_init
    best_score = -1.0
    best_assignments: list[dict[str, Any]] = []

    for ring_hyp in range(1, 11):
        r_mm_hyp = ring_radius_mm(ring_hyp)
        largest_three = cand[:3]
        median_r = float(np.median([r for _, _, r in largest_three]))
        pmm_hyp = median_r / r_mm_hyp
        if not (pmm_lo <= pmm_hyp <= pmm_hi):
            continue
        assignments = []
        inlier_score = 0.0
        for cx, cy, r in cand:
            r_mm_obs = r / pmm_hyp
            best_ring = min(range(1, 11),
                            key=lambda k: abs(r_mm_obs - ring_radius_mm(k)))
            residual = abs(r_mm_obs - ring_radius_mm(best_ring))
            tol = 0.30 * 25.0
            if residual < tol:
                inlier_score += 1.0 - residual / tol
            assignments.append({
                "cx": cx, "cy": cy, "r_px": r,
                "r_mm_obs": r_mm_obs,
                "ring": best_ring,
                "residual_mm": residual,
            })
        if inlier_score > best_score:
            best_score = inlier_score
            best_pmm = pmm_hyp
            best_assignments = assignments

    return best_pmm, best_assignments


# ---------------------------------------------------------------------------
# Iteration 3 — radial-profile residual minimization (fine-tune pmm)
# ---------------------------------------------------------------------------
def radial_profile(
    gray: np.ndarray, bullseye: tuple[float, float], max_r_px: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (radii, mean_gray) binned by 1-px annular rings from bullseye."""
    h, w = gray.shape
    bx, by = bullseye
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt((xx - bx) ** 2 + (yy - by) ** 2)
    dist_int = np.clip(dist.astype(np.int32), 0, max_r_px)
    sum_gray = np.bincount(dist_int.ravel(), weights=gray.ravel(),
                            minlength=max_r_px + 1)
    count = np.bincount(dist_int.ravel(), minlength=max_r_px + 1).astype(np.float32)
    count = np.maximum(count, 1.0)
    return np.arange(max_r_px + 1).astype(np.float32), sum_gray / count


def refine_pmm_via_profile(
    gray: np.ndarray,
    bullseye: tuple[float, float],
    pmm_current: float,
    max_iter: int = 3,
    tol: float = 0.02,
    max_step_pct: float = 0.15,
) -> tuple[float, list[dict[str, Any]]]:
    """Iteratively adjust pmm by minimizing radial-profile residual.

    At each iteration:
      1. Compute radial profile.
      2. For each predicted ISSF ring (1..10), find the actual local minimum
         (dark ring stroke) in a ±12.5mm window around the predicted radius.
      3. Compute residual = (observed_min_radius - predicted_radius) / predicted_radius.
      4. Update pmm: pmm_new = pmm_current * (1 + mean(residuals)), capped at
         ±`max_step_pct` per iteration to prevent overshoot.
      5. Stop if |pmm_new - pmm_current| / pmm_current < tol OR max_iter reached.

    Returns (final_pmm, debug_info_per_iter).
    """
    h, w = gray.shape
    max_r_px = int(0.6 * min(h, w))
    radii_px, profile = radial_profile(gray, bullseye, max_r_px)
    debug_iters: list[dict[str, Any]] = []

    pmm = pmm_current
    for it in range(max_iter):
        residuals: list[float] = []
        observed_radii: list[tuple[int, float, float]] = []
        for ring in range(1, 11):
            r_pred_mm = ring_radius_mm(ring)
            r_pred_px = r_pred_mm * pmm
            if r_pred_px >= max_r_px - 5 or r_pred_px < 5:
                continue
            window_mm = 25.0 * 0.5
            window_px = int(window_mm * pmm)
            lo = max(1, int(r_pred_px) - window_px)
            hi = min(max_r_px, int(r_pred_px) + window_px)
            if hi - lo < 3:
                continue
            window_profile = profile[lo:hi + 1]
            min_idx = int(np.argmin(window_profile))
            r_obs_px = lo + min_idx
            residual = (r_obs_px - r_pred_px) / r_pred_px
            residuals.append(residual)
            observed_radii.append((ring, r_pred_px, r_obs_px))

        if not residuals:
            break
        mean_res = float(np.mean(residuals))
        # Cap the step to prevent overshoot.
        step = max(-max_step_pct, min(max_step_pct, mean_res))
        new_pmm = pmm * (1.0 + step)
        delta = abs(new_pmm - pmm) / pmm
        debug_iters.append({
            "iter": it + 1,
            "pmm_in": pmm,
            "pmm_out": new_pmm,
            "delta_pct": delta * 100,
            "n_rings_used": len(residuals),
            "mean_residual_pct": mean_res * 100,
            "step_capped": step != mean_res,
            "observed_radii": [
                {"ring": r, "r_pred_px": rp, "r_obs_px": ro}
                for r, rp, ro in observed_radii
            ],
        })
        pmm = new_pmm
        if delta < tol:
            break

    return pmm, debug_iters


# ---------------------------------------------------------------------------
# Canvas expansion
# ---------------------------------------------------------------------------
def expand_canvas(
    img: np.ndarray,
    bullseye: tuple[float, float],
    pmm: float,
    margin_mm: float = EXTRACTION_MARGIN_MM,
) -> tuple[np.ndarray, tuple[float, float], int, int]:
    h, w = img.shape[:2]
    bx, by = bullseye
    outer_r_px = int((ring_radius_mm(1) + margin_mm) * pmm)
    left = max(0, int(outer_r_px - bx))
    right = max(0, int(outer_r_px - (w - bx)))
    top = max(0, int(outer_r_px - by))
    bot = max(0, int(outer_r_px - (h - by)))
    padded = cv2.copyMakeBorder(img, top, bot, left, right,
                                cv2.BORDER_CONSTANT, value=PAPER_COLOR)
    new_bullseye = (bx + left, by + top)
    return padded, new_bullseye, left, top


# ---------------------------------------------------------------------------
# Two-stage hole detection (coarse + fine) — user direction
# ---------------------------------------------------------------------------
def detect_holes_coarse(
    gray: np.ndarray,
    pmm: float,
    caliber: str,
    black_disc_mask: np.ndarray | None = None,
    area_range: tuple[float, float] = (0.15, 6.0),
) -> tuple[list[tuple[float, float, float]], float]:
    """Stage 1 — Coarse candidate generation via DoG with HUGE error margin.

    DoG(sigmas scaled to bullet_radius) is matched-filter-optimal for disk
    detection. Here we run it with deliberately loose parameters:
      - Liberal area filter (catches partial / torn / overlapping holes and
        even small clusters as one candidate)
      - No circularity filter at this stage
      - Otsu threshold on the DoG response

    Returns (candidates, bullet_radius_px) where candidates is a list of
    (cx, cy, area_px) — area is kept for the verifier to use.
    """
    bullet_diameter_mm = CALIBER_DIAMETER_MM.get(caliber, 5.7)
    bullet_radius_mm = bullet_diameter_mm / 2.0
    bullet_radius_px = bullet_radius_mm * pmm

    # DoG sigmas tuned for bullet-scale disk detection.
    sigma_small = max(0.3, bullet_radius_px / math.sqrt(2.0))
    sigma_large = max(0.5, bullet_radius_px * math.sqrt(2.0))
    g1 = cv2.GaussianBlur(gray, (0, 0), sigma_small)
    g2 = cv2.GaussianBlur(gray, (0, 0), sigma_large)
    dog = g2 - g1  # bright peaks at dark-disk centers

    if black_disc_mask is not None:
        dog = dog.copy()
        dog[black_disc_mask == 0] = 0

    # Otsu threshold on the search region only.
    d_min, d_max = float(dog.min()), float(dog.max())
    if d_max - d_min < 1e-6:
        return [], bullet_radius_px
    dog_u8 = ((dog - d_min) / (d_max - d_min) * 255).clip(0, 255).astype(np.uint8)
    if black_disc_mask is not None:
        search = dog_u8[black_disc_mask > 0]
    else:
        search = dog_u8
    if search.size == 0:
        return [], bullet_radius_px
    otsu_thresh, _ = cv2.threshold(
        search, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    _, binary = cv2.threshold(dog_u8, otsu_thresh, 255, cv2.THRESH_BINARY)
    binary = binary.astype(np.uint8)

    # Light cleanup: 3×3 open removes 1-pixel specks.
    k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k3)

    # Connected components with LIBERAL area filter — keep anything remotely
    # bullet-sized. Single-value tuning is intentionally loose; per-candidate
    # verification (next stage) does the precision work.
    expected_area = math.pi * (bullet_radius_px ** 2)
    min_area = area_range[0] * expected_area
    max_area = area_range[1] * expected_area
    n_lbl, labels, stats, cents = cv2.connectedComponentsWithStats(binary, connectivity=8)
    candidates: list[tuple[float, float, float]] = []
    for i in range(1, n_lbl):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if not (min_area <= a <= max_area):
            continue
        candidates.append((float(cents[i, 0]), float(cents[i, 1]), float(a)))
    return candidates, bullet_radius_px


def verify_hole_candidate(
    gray: np.ndarray,
    candidate: tuple[float, float, float],
    bullet_radius_px: float,
    min_texture_ratio: float = 1.3,
    min_circularity: float = 0.20,
) -> bool:
    """Stage 2 — Per-candidate verifier via local TEXTURE + size + shape tests.

    Inside the black disc, bullet holes are dark-on-dark — luminance SNR is
    useless. The discriminating signal is TEXTURE: torn paper fibers around
    the hole edge produce high local standard deviation, while printed ink
    (whether the black disc or painted digits) is smooth.

    Tests:
      1. Texture contrast: local-std inside the candidate disk (r<bullet_r)
         must exceed local-std in the annulus outside (1.3·bullet_r < r <
         2.5·bullet_r) by a factor ≥ `min_texture_ratio`.
      2. Size: the dark blob's equivalent-area radius within the central
         1.3·bullet_r disk must be in [0.4×, 1.8×] bullet_radius.
      3. Shape: the dark blob's circularity ≥ `min_circularity` — set low
         (0.20) because real bullet holes have torn/irregular edges.

    Returns True if the candidate passes ALL tests.
    """
    cx, cy, _area = candidate
    roi_r = int(max(3 * bullet_radius_px, 8))
    x0 = max(0, int(cx - roi_r))
    x1 = min(gray.shape[1], int(cx + roi_r))
    y0 = max(0, int(cy - roi_r))
    y1 = min(gray.shape[0], int(cy + roi_r))
    if x1 - x0 < 5 or y1 - y0 < 5:
        return False
    roi = gray[y0:y1, x0:x1].astype(np.float32)
    lx, ly = cx - x0, cy - y0

    h, w = roi.shape
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt((xx - lx) ** 2 + (yy - ly) ** 2)
    inside = dist < bullet_radius_px
    annulus = (dist >= 1.3 * bullet_radius_px) & (dist < 2.5 * bullet_radius_px)
    if inside.sum() < 3 or annulus.sum() < 3:
        return False

    # Local standard deviation via box-filter trick.
    k_size = max(3, int(bullet_radius_px))
    if k_size % 2 == 0:
        k_size += 1
    mu = cv2.boxFilter(roi, ddepth=cv2.CV_32F, ksize=(k_size, k_size))
    mu_sq = cv2.boxFilter(roi * roi, ddepth=cv2.CV_32F, ksize=(k_size, k_size))
    loc_std = np.sqrt(np.maximum(mu_sq - mu * mu, 0.0))

    inside_std = float(loc_std[inside].mean())
    annulus_std = float(loc_std[annulus].mean())
    if annulus_std < 1e-6:
        return False
    texture_ratio = inside_std / annulus_std
    if texture_ratio < min_texture_ratio:
        return False

    # Size + shape: local Otsu on the ROI, find the dark blob at center.
    roi_min_v = float(roi.min())
    roi_max_v = float(roi.max())
    if roi_max_v - roi_min_v < 1e-6:
        return False
    roi_u8 = ((roi - roi_min_v) / (roi_max_v - roi_min_v) * 255).clip(0, 255).astype(np.uint8)
    _, roi_bin = cv2.threshold(roi_u8, 0, 255,
                               cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    disk_mask = (dist < 1.3 * bullet_radius_px).astype(np.uint8)
    roi_bin_disk = cv2.bitwise_and(roi_bin, roi_bin, mask=disk_mask)
    if roi_bin_disk.sum() < 0.10 * disk_mask.sum() * 255:
        return False

    n, labels_c, stats_c, _ = cv2.connectedComponentsWithStats(roi_bin_disk, connectivity=8)
    if not (0 <= int(ly) < h and 0 <= int(lx) < w):
        return False
    center_label = labels_c[int(ly), int(lx)]
    if center_label == 0:
        near = (dist < 0.5 * bullet_radius_px) & (labels_c > 0)
        if not near.any():
            return False
        center_label = int(np.median(labels_c[near]))
    a = int(stats_c[center_label, cv2.CC_STAT_AREA])
    if a < 3:
        return False
    eq_r = math.sqrt(a / math.pi)
    if not (0.4 * bullet_radius_px <= eq_r <= 1.8 * bullet_radius_px):
        return False
    comp_mask = (labels_c == center_label).astype(np.uint8) * 255
    contours, _ = cv2.findContours(comp_mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False
    c = max(contours, key=cv2.contourArea)
    ca = cv2.contourArea(c)
    cp = cv2.arcLength(c, True)
    if ca < 3 or cp < 1:
        return False
    circ = 4 * math.pi * ca / (cp * cp)
    return circ >= min_circularity


def detect_holes_twostage(
    gray: np.ndarray,
    pmm: float,
    caliber: str,
    black_disc_mask: np.ndarray | None = None,
) -> tuple[list[tuple[float, float]], float, dict[str, Any]]:
    """Two-stage hole detection (user direction):
       coarse candidate generation → per-candidate verification.

    Returns (verified_centroids, bullet_radius_px, debug_info).
    """
    candidates, bullet_radius_px = detect_holes_coarse(
        gray, pmm, caliber, black_disc_mask,
    )
    verified: list[tuple[float, float]] = []
    rejected = 0
    for cand in candidates:
        if verify_hole_candidate(gray, cand, bullet_radius_px):
            verified.append((cand[0], cand[1]))
        else:
            rejected += 1
    debug = {
        "n_candidates": len(candidates),
        "n_verified": len(verified),
        "n_rejected": rejected,
        "bullet_radius_px": bullet_radius_px,
    }
    return verified, bullet_radius_px, debug


# ---------------------------------------------------------------------------
# Visualization helpers
# ---------------------------------------------------------------------------
def draw_rings(
    img: np.ndarray,
    bullseye: tuple[float, float],
    pmm: float,
    thickness: int = 2,
    draw_inner_ten: bool = True,
    metric: dict[str, float] | None = None,
) -> np.ndarray:
    """Draw the 10 ISSF rings + inner-10 (X).

    If `metric` (from `compute_anisotropic_metric`) is provided, rings are
    drawn as ELLIPSES matching the apparent ellipticity in the photo (user
    direction: do not rotate the image). Otherwise rings are drawn as
    circles using the average pmm.

    cv2.ellipse takes axes as SEMI-axis lengths. The metric's per-axis
    scales (sx, sy) are px-per-mm along the ellipse-aligned major/minor
    directions, so ring K's semi-axes in px = (r_K_mm * sx, r_K_mm * sy).
    """
    out = img.copy()
    bx, by = bullseye
    if metric is not None:
        angle_deg = metric["angle_deg"]
        sx, sy = metric["sx"], metric["sy"]
    else:
        angle_deg = 0.0
        sx = sy = pmm

    # Inner-10 (X) — dashed/dotted regardless.
    if draw_inner_ten:
        r_mm_x = RING_RADII_MM[0]
        if metric is not None:
            cv2.ellipse(out, (int(bx), int(by)),
                        (int(r_mm_x * sx), int(r_mm_x * sy)),
                        angle_deg, 0, 360, RAINBOW[0], 1)
        else:
            r_x_px = int(r_mm_x * pmm)
            n_dashes = 24
            for i in range(n_dashes):
                if i % 2 == 0:
                    continue
                a0 = 2 * math.pi * i / n_dashes
                a1 = 2 * math.pi * (i + 1) / n_dashes
                p0 = (int(bx + r_x_px * math.cos(a0)),
                      int(by + r_x_px * math.sin(a0)))
                p1 = (int(bx + r_x_px * math.cos(a1)),
                      int(by + r_x_px * math.sin(a1)))
                cv2.line(out, p0, p1, RAINBOW[0], 1)

    # Rings 10..1
    for ring in range(1, 11):
        r_mm = ring_radius_mm(ring)
        col = RAINBOW[ring % len(RAINBOW)]
        if metric is not None:
            semi_major = int(r_mm * sx)
            semi_minor = int(r_mm * sy)
            cv2.ellipse(out, (int(bx), int(by)),
                        (semi_major, semi_minor),
                        angle_deg, 0, 360, col, thickness)
            # Label at top of ring (along minor axis in ellipse-aligned frame).
            theta = math.radians(angle_deg - 90)
            lx = int(bx + semi_major * math.cos(theta))
            ly = int(by + semi_major * math.sin(theta))
            cv2.putText(out, str(ring), (lx + 3, ly - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        else:
            r_px = int(r_mm * pmm)
            cv2.circle(out, (int(bx), int(by)), r_px, col, thickness)
            cv2.putText(out, str(ring),
                        (int(bx + r_px * math.cos(-math.pi / 2)) + 3,
                         int(by + r_px * math.sin(-math.pi / 2)) - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    cv2.drawMarker(out, (int(bx), int(by)), (0, 0, 0),
                   cv2.MARKER_CROSS, 20, 2)
    return out


def draw_text(img: np.ndarray, lines: list[str]) -> np.ndarray:
    out = img.copy()
    y = 30
    for ln in lines:
        cv2.putText(out, ln, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (0, 255, 0), 2)
        y += 22
    return out


# ---------------------------------------------------------------------------
# Per-image pipeline (NO rotation — user direction)
# ---------------------------------------------------------------------------
def run_one(img_id: int, caliber: str) -> dict[str, Any]:
    img_path = TRAIN_DIR / f"{img_id}.jpg"
    img = load_exif_normalized(img_path)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_01_original.jpg"), img)

    crop, _ = bbox_crop(img, expand=0.4)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_02_crop.png"), crop)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # --- Iteration 1: coarse pmm + anisotropic metric estimate ---------
    bullet_diameter_mm = CALIBER_DIAMETER_MM.get(caliber, 5.7)
    bullet_radius_mm = bullet_diameter_mm / 2.0
    bd = detect_black_disc(gray, bullet_radius_mm=bullet_radius_mm)
    if bd is None:
        return {"img_id": img_id, "caliber": caliber,
                "error": "black-disc detection failed"}
    metric = compute_anisotropic_metric(bd["ellipse"])
    pmm_coarse = metric["pmm_avg"]
    bullseye = (metric["cx"], metric["cy"])
    anisotropy = metric["anisotropy"]

    # Draw black-disc detection.
    bd_vis = crop.copy()
    cv2.drawContours(bd_vis, [bd["contour"]], -1, (0, 255, 0), 2)
    cv2.circle(bd_vis, (int(bullseye[0]), int(bullseye[1])),
               int(bd["inscribed_r_px"]), (0, 0, 255), 2)
    cv2.ellipse(bd_vis, bd["ellipse"], (255, 0, 0), 2)
    cv2.drawMarker(bd_vis, (int(bullseye[0]), int(bullseye[1])),
                   (255, 255, 255), cv2.MARKER_CROSS, 20, 2)
    bd_vis = draw_text(bd_vis, [
        f"Iter 1 (black disc):",
        f"  inscribed r = {bd['inscribed_r_px']:.0f} px",
        f"  pmm_avg (geomean sx*sy) = {pmm_coarse:.3f}",
        f"  sx={metric['sx']:.3f}  sy={metric['sy']:.3f}  angle={metric['angle_deg']:.1f}°",
        f"  anisotropy = {anisotropy:.3f}  (1.0 = no perspective)",
    ])
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_03_blackdisc.png"), bd_vis)

    # --- Iteration 2: HoughCircles concentric ring fit on ORIGINAL crop -
    clustered = detect_concentric_circles(gray)
    pmm_iter2, assignments = fit_pmm_to_issf_rings(clustered, pmm_coarse)

    iter2_vis = crop.copy()
    for c in clustered:
        cv2.circle(iter2_vis, (int(c[0]), int(c[1])), int(c[2]), (0, 255, 0), 1)
    for a in assignments:
        col = (0, 255, 0) if a["residual_mm"] < 25.0 * 0.30 else (0, 0, 255)
        cv2.putText(iter2_vis, f"r{a['ring']}",
                    (int(a["cx"]) + 4, int(a["cy"]) - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
    iter2_vis = draw_text(iter2_vis, [
        f"Iter 2 (HoughCircles fit, original crop):",
        f"  pmm_in (from iter 1): {pmm_coarse:.3f}",
        f"  pmm_out: {pmm_iter2:.3f}  (Δ={(pmm_iter2 - pmm_coarse) / pmm_coarse * 100:+.1f}%)",
        f"  rings detected: {sorted({a['ring'] for a in assignments})}",
        f"  inliers: {sum(1 for a in assignments if a['residual_mm'] < 25.0 * 0.30)}/{len(assignments)}",
    ])
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_04_iter2_rings.png"), iter2_vis)

    # --- Iteration 3: radial-profile residual minimization --------------
    pmm_final, profile_iters = refine_pmm_via_profile(
        gray, bullseye, pmm_iter2, max_iter=3, tol=0.02,
    )
    # Update metric to use the refined pmm (keep sx/sy ratio and angle).
    scale_change = pmm_final / metric["pmm_avg"]
    metric["sx"] *= scale_change
    metric["sy"] *= scale_change
    metric["pmm_avg"] = pmm_final

    # Draw iter-3 radial-profile plot.
    max_r_plot = int(0.6 * min(gray.shape))
    radii_px, profile = radial_profile(gray, bullseye, max_r_plot)
    fig_w = max(800, max_r_plot)
    fig = np.full((400, fig_w, 3), 255, dtype=np.uint8)
    if profile.max() - profile.min() > 1e-6:
        prof_norm = (profile - profile.min()) / (profile.max() - profile.min())
        for i in range(0, min(len(prof_norm) - 1, fig_w - 1)):
            y0 = 350 - int(prof_norm[i] * 300)
            y1 = 350 - int(prof_norm[i + 1] * 300)
            cv2.line(fig, (i, y0), (i + 1, y1), (0, 0, 0), 1)
    for ring in range(1, 11):
        r_px = int(ring_radius_mm(ring) * pmm_final)
        if 0 < r_px < fig_w:
            cv2.line(fig, (r_px, 0), (r_px, 400),
                     RAINBOW[ring % len(RAINBOW)], 1)
            cv2.putText(fig, f"r{ring}", (r_px + 2, 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                        RAINBOW[ring % len(RAINBOW)], 1)
    fig = draw_text(fig, [
        f"Iter 3 (radial-profile refine):",
        f"  pmm_in: {pmm_iter2:.3f}  →  pmm_out: {pmm_final:.3f}",
        f"  iterations: {len(profile_iters)}",
    ] + [f"  iter {it['iter']}: Δ={it['delta_pct']:.2f}%, "
         f"mean_res={it['mean_residual_pct']:+.2f}%, n_rings={it['n_rings_used']}"
         for it in profile_iters])
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_05_iter3_profile.png"), fig)

    # --- Final ring overlay on the ORIGINAL crop (rings as ellipses) ----
    pmm = pmm_final
    overlay = draw_rings(crop, bullseye, pmm, thickness=2, metric=metric)
    overlay = draw_text(overlay, [
        f"FINAL: precision_pistol, {caliber}",
        f"  pmm: iter1={pmm_coarse:.3f} → iter2={pmm_iter2:.3f} → iter3={pmm:.3f}",
        f"  anisotropy: {anisotropy:.3f}  angle: {metric['angle_deg']:.1f}°",
        f"  ring-1 outer (avg): {ring_radius_mm(1) * pmm:.0f} px radius",
        f"  card {CARD_SCORING_MM:.0f}mm scoring Ø at pmm {pmm:.2f} = {CARD_SCORING_MM * pmm:.0f} px",
    ])
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_06_ring_overlay.png"), overlay)

    # --- Canvas expansion + extracted target ---------------------------
    padded, padded_bullseye, pad_l, pad_t = expand_canvas(crop, bullseye, pmm)
    # Translate metric's bullseye to padded coords.
    metric_padded = dict(metric)
    metric_padded["cx"] = metric["cx"] + pad_l
    metric_padded["cy"] = metric["cy"] + pad_t
    h_p, w_p = padded.shape[:2]
    outer_r_px = int((ring_radius_mm(1) + EXTRACTION_MARGIN_MM) * pmm)
    # Use ellipse mask for extraction.
    mask = np.zeros((h_p, w_p), dtype=np.uint8)
    cv2.ellipse(mask, (int(padded_bullseye[0]), int(padded_bullseye[1])),
                (int((ring_radius_mm(1) + EXTRACTION_MARGIN_MM) * metric_padded["sx"]),
                 int((ring_radius_mm(1) + EXTRACTION_MARGIN_MM) * metric_padded["sy"])),
                metric_padded["angle_deg"], 0, 360, 255, -1)
    bg = np.full_like(padded, PAPER_COLOR, dtype=np.uint8)
    extracted = cv2.bitwise_and(padded, padded, mask=mask)
    bg_masked = cv2.bitwise_and(bg, bg, mask=cv2.bitwise_not(mask))
    extracted = cv2.add(extracted, bg_masked)
    extracted_rings = draw_rings(extracted, padded_bullseye, pmm,
                                 thickness=1, metric=metric_padded)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_07_extracted_rings.png"),
                extracted_rings)

    # --- Two-stage hole detection on the ORIGINAL crop -----------------
    bd_mask = np.zeros(gray.shape, dtype=np.uint8)
    cv2.ellipse(bd_mask, (int(bullseye[0]), int(bullseye[1])),
                (int(BLACK_DISC_R_MM * metric["sx"]),
                 int(BLACK_DISC_R_MM * metric["sy"])),
                metric["angle_deg"], 0, 360, 255, -1)
    centers_crop, bullet_radius_px, hole_debug = detect_holes_twostage(
        gray, pmm, caliber, black_disc_mask=bd_mask,
    )
    centers_padded = [(cx + pad_l, cy + pad_t) for cx, cy in centers_crop]

    # Score each hole via the anisotropic metric (ISSF line-break rule).
    scores: list[int] = []
    for cx, cy in centers_padded:
        d_mm = anisotropic_distance_mm(cx, cy, metric_padded)
        scores.append(score_to_ring_anisotropic(d_mm, bullet_radius_mm))

    # Magenta overlay on extracted target.
    magenta_img = draw_rings(extracted, padded_bullseye, pmm,
                             thickness=1, metric=metric_padded)
    for i, ((cx, cy), s) in enumerate(zip(centers_padded, scores)):
        cv2.circle(magenta_img, (int(cx), int(cy)),
                   max(3, int(bullet_radius_px)), MAGENTA, -1)
        cv2.circle(magenta_img, (int(cx), int(cy)),
                   max(3, int(bullet_radius_px)), (255, 255, 255), 1)
        cv2.putText(magenta_img, f"#{i + 1}:{s}",
                    (int(cx) + 6, int(cy) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)
    magenta_img = draw_text(magenta_img, [
        f"Two-stage hole detection (coarse DoG → per-candidate verify)",
        f"  candidates: {hole_debug['n_candidates']}  "
        f"verified: {hole_debug['n_verified']}  "
        f"rejected: {hole_debug['n_rejected']}",
        f"  bullet_radius_px: {bullet_radius_px:.2f}",
    ])
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_08_holes_magenta.png"), magenta_img)

    # Also save the coarse-stage candidates (pre-verification) for debugging.
    coarse_only = draw_rings(extracted, padded_bullseye, pmm,
                             thickness=1, metric=metric_padded)
    coarse_candidates, _ = detect_holes_coarse(gray, pmm, caliber, bd_mask)
    for cx, cy, _a in coarse_candidates:
        cxp, cyp = cx + pad_l, cy + pad_t
        cv2.circle(coarse_only, (int(cxp), int(cyp)),
                   max(3, int(bullet_radius_px)), (0, 255, 255), 2)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_09_coarse_candidates.png"), coarse_only)

    return {
        "img_id": img_id,
        "caliber": caliber,
        "target_type": "precision_pistol",
        "crop_size": list(crop.shape[:2]),
        "padded_size": list(padded.shape[:2]),
        "pmm_iter1_blackdisc": pmm_coarse,
        "pmm_iter2_rings": pmm_iter2,
        "pmm_iter3_profile": pmm_final,
        "anisotropy": anisotropy,
        "ellipse_angle_deg": metric["angle_deg"],
        "bullseye_crop": list(bullseye),
        "n_candidates_coarse": hole_debug["n_candidates"],
        "n_holes_verified": hole_debug["n_verified"],
        "n_rejected": hole_debug["n_rejected"],
        "scores": scores,
        "centers": [[float(cx), float(cy)] for cx, cy in centers_padded],
        "bullet_radius_px": bullet_radius_px,
        "ring1_radius_px_avg": ring_radius_mm(1) * pmm,
        "ring10_radius_px_avg": ring_radius_mm(10) * pmm,
        "inner10_radius_px_avg": RING_RADII_MM[0] * pmm,
        "iter3_debug": profile_iters,
        "ring_assignments_iter2": assignments,
    }


def main() -> None:
    results = []
    header = (
        f"{'id':>3}  {'cal':>7}  {'crop':>11}  "
        f"{'pmm1':>6}  {'pmm2':>6}  {'pmm3':>6}  {'aniso':>5}  "
        f"{'cand':>4}  {'kept':>4}  scores"
    )
    print(header)
    print("-" * len(header))
    for img_id, caliber in IMAGE_CALIBER.items():
        try:
            r = run_one(img_id, caliber)
            results.append(r)
            if "error" in r:
                print(f"{img_id}: ERROR: {r['error']}")
                continue
            scores_str = ",".join(str(s) for s in r["scores"]) or "-"
            print(
                f"{img_id:>3}  {caliber:>7}  "
                f"{r['crop_size'][1]}x{r['crop_size'][0]:>4}  "
                f"{r['pmm_iter1_blackdisc']:>6.3f}  "
                f"{r['pmm_iter2_rings']:>6.3f}  "
                f"{r['pmm_iter3_profile']:>6.3f}  "
                f"{r['anisotropy']:>5.3f}  "
                f"{r['n_candidates_coarse']:>4}  "
                f"{r['n_holes_verified']:>4}  {scores_str}",
                flush=True,
            )
        except Exception as e:
            print(f"{img_id}: EXCEPTION: {e}", flush=True)
            import traceback
            traceback.print_exc()

    out_path = OUT_DIR / "ring_calibration_v8_results.json"
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nResults → {out_path}")
    print(f"Intermediates → {OUT_DIR}/<id>_01..09*.png")
    print("Key validation images:")
    print(f"  - <id>_03_blackdisc.png          (iter 1: disc + ellipse)")
    print(f"  - <id>_04_iter2_rings.png         (iter 2: HoughCircles on ORIGINAL crop)")
    print(f"  - <id>_05_iter3_profile.png       (iter 3: radial profile + ring marks)")
    print(f"  - <id>_06_ring_overlay.png        (final rings drawn as ELLIPSES on original)")
    print(f"  - <id>_07_extracted_rings.png     (extracted target + elliptical rings)")
    print(f"  - <id>_08_holes_magenta.png       (rings + VERIFIED magenta holes)")
    print(f"  - <id>_09_coarse_candidates.png   (pre-verification candidates, for tuning)")


if __name__ == "__main__":
    main()
