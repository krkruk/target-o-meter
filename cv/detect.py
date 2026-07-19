"""CV spike — ISSF paper-target bullet-hole detection.

Django-independent. Depends only on opencv-python-headless, numpy.

User's 5-stage pipeline:
    1. Perspective normalization (homography warp to canonical target)
    2. Geometry extraction (concentric rings + bullseye center)
    3. Morphological isolation of bullet holes
    4. Watershed de-clustering of overlapping holes
    5. Radial scoring (ISSF line-break rule)
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Caliber table — bullet *projectile* diameter in mm. Used both for the
# morphological kernel size and for the line-break score adjustment.
# ---------------------------------------------------------------------------
CALIBER_DIAMETER_MM: dict[str, float] = {
    "22lr": 5.7,
    "9x19": 9.0,
    ".223Rem": 5.56,
    "slug": 18.0,  # 12-gauge slug ~18 mm
}

# Canonical target card size in mm (PRD §2 — only Air Pistol + Precision supported).
TARGET_CARD_MM: dict[str, float] = {
    "air_pistol": 170.0,
    "precision_pistol": 550.0,
}

# Default target type when caller doesn't specify.
DEFAULT_TARGET_TYPE = "air_pistol"

# Downscale long side to this for the *localization* pass only. The actual
# detection runs on a full-resolution crop extracted from the original image,
# so tiny calibers (22lr ≈ 5.7 mm) still have enough pixels.
LOCATOR_LONG_SIDE = 1200


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def detect(
    image_path: str | Path,
    caliber: str | list[str] | None,
    target_type: str = DEFAULT_TARGET_TYPE,
    debug: bool = False,
) -> dict[str, Any]:
    """Run the full 5-stage pipeline on a single image.

    Returns a dict with:
        scores         list[int]  per-hole scores 0..10 (X encoded as 10 here;
                                  caller can re-derive X from centers if needed)
        total          int        sum(scores)
        centers        list[(x,y)] hole centroids in source-image pixel coords
        bullet_radius_px float    bullet radius in source px (caliber-driven)
        target_center  (x,y)      bullseye in source px
        target_radius_px float    estimated scoring radius (1-ring) in source px
        px_per_mm      float      estimated scale
        failure_stage  str|None   one of None / 'homography' / 'rings' /
                                  'morph' / 'watershed' / 'scoring'
        notes          list[str]  diagnostics
    """
    img = cv2.imread(str(image_path))
    if img is None:
        return _empty(failure_stage="homography", notes=["cv2.imread returned None"])

    # --- keep full-resolution original; build a small locator image --------
    h0, w0 = img.shape[:2]
    long_side = max(h0, w0)
    loc_scale = 1.0
    if long_side > LOCATOR_LONG_SIDE:
        loc_scale = LOCATOR_LONG_SIDE / long_side
    locator = cv2.resize(
        img, (int(w0 * loc_scale), int(h0 * loc_scale)), interpolation=cv2.INTER_AREA
    )
    notes: list[str] = [
        f"input {w0}x{h0}, locator {locator.shape[1]}x{locator.shape[0]} (scale {loc_scale:.3f})"
    ]

    # Normalize caliber input — metadata may be list (mixed-caliber target).
    if isinstance(caliber, list):
        primary = caliber[0] if caliber else None
        notes.append(f"mixed calibers {caliber}; using primary={primary}")
        caliber = primary
    if caliber not in CALIBER_DIAMETER_MM:
        notes.append(f"unknown caliber '{caliber}'; defaulting to 22lr")
        caliber = "22lr"
    bullet_diameter_mm = CALIBER_DIAMETER_MM[caliber]

    # ----------------------------------------------------------------------
    # Stage 1 — perspective normalization / target localization.
    #
    # The spike does NOT run a full 4-corner homography because the phone
    # photos rarely show a clean target rectangle. Instead we localize the
    # target as the largest roughly-square dark blob on a *downscaled* image,
    # then crop from the FULL-RESOLUTION original so tiny calibers keep
    # enough pixels.
    # ----------------------------------------------------------------------
    bbox, warp_meta, fail1 = _stage1_localize(locator, target_type)
    if fail1:
        warped = img
        warp_meta = {"method": "raw_fallback", "bbox": (0, 0, img.shape[1], img.shape[0])}
    else:
        x0, y0, bw, bh = bbox
        # Map bbox back to original-resolution coords.
        sx, sy = w0 / locator.shape[1], h0 / locator.shape[0]
        bx0, by0 = int(x0 * sx), int(y0 * sy)
        bx1, by1 = int((x0 + bw) * sx), int((y0 + bh) * sy)
        warped = img[by0:by1, bx0:bx1]
        warp_meta["bbox_orig"] = (bx0, by0, bx1 - bx0, by1 - by0)

    # ----------------------------------------------------------------------
    # Stage 2 — ring geometry: find bullseye center + scoring radius.
    # ----------------------------------------------------------------------
    center_px, scoring_radius_px, px_per_mm, fail2 = _stage2_rings(
        warped, card_mm=TARGET_CARD_MM[target_type]
    )
    if fail2:
        return _empty(
            failure_stage="rings",
            notes=notes + warp_meta.get("notes", []) + ["ring extraction failed"],
        )

    bullet_radius_px = (bullet_diameter_mm / 2.0) * px_per_mm

    # ----------------------------------------------------------------------
    # Stage 3 — morphological isolation of bullet-hole blobs.
    # Returns (mask, centroids, failed). When Stage 3 detects holes via
    # HoughCircles it returns centroids directly; the mask is also returned
    # for visualization / debugging. Stage 4 watershed is only used as a
    # fallback when Stage 3 produces no centroids.
    # ----------------------------------------------------------------------
    hole_mask, centers, fail3 = _stage3_morph(warped, bullet_radius_px)
    if fail3:
        return _empty(
            failure_stage="morph",
            notes=notes + [f"bullet_radius_px={bullet_radius_px:.2f}", "morph stage failed"],
        )

    # ----------------------------------------------------------------------
    # Stage 4 — watershed de-clustering (fallback only).
    # ----------------------------------------------------------------------
    if not centers:
        centers, fail4 = _stage4_watershed(hole_mask, bullet_radius_px)
        if fail4:
            return _empty(
                failure_stage="watershed",
                notes=notes + ["watershed stage failed"],
            )

    # ----------------------------------------------------------------------
    # Stage 5 — radial scoring.
    # ----------------------------------------------------------------------
    scores = _stage5_score(centers, center_px, scoring_radius_px, bullet_radius_px)

    return {
        "scores": scores,
        "total": int(sum(scores)),
        "centers": [(int(x), int(y)) for (x, y) in centers],
        "bullet_radius_px": float(bullet_radius_px),
        "target_center": (int(center_px[0]), int(center_px[1])),
        "target_radius_px": float(scoring_radius_px),
        "px_per_mm": float(px_per_mm),
        "failure_stage": None,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Stage implementations
# ---------------------------------------------------------------------------
def _stage1_localize(
    img: np.ndarray, target_type: str
) -> tuple[tuple[int, int, int, int] | None, dict, bool]:
    """Find the largest dark roughly-square blob. Return its bbox.

    Returns (bbox=(x,y,w,h) in img coords or None, meta, failed_flag).
    Caller is responsible for cropping at the desired resolution.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape

    # Otsu threshold (inverted so dark = foreground).
    _, binv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Clean noise.
    kernel3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binv = cv2.morphologyEx(binv, cv2.MORPH_OPEN, kernel3, iterations=1)
    binv = cv2.morphologyEx(binv, cv2.MORPH_CLOSE, kernel3, iterations=2)

    n_lbl, labels, stats, _ = cv2.connectedComponentsWithStats(binv, connectivity=8)
    if n_lbl <= 1:
        return None, {"method": "raw_fallback"}, True

    areas = stats[:, cv2.CC_STAT_AREA]
    areas[0] = 0  # ignore background
    best = None
    best_score = -1.0
    order = np.argsort(-areas)
    for idx in order[:15]:
        a = areas[idx]
        if a < (0.05 * h * w):
            continue
        x = stats[idx, cv2.CC_STAT_LEFT]
        y = stats[idx, cv2.CC_STAT_TOP]
        bw = stats[idx, cv2.CC_STAT_WIDTH]
        bh = stats[idx, cv2.CC_STAT_HEIGHT]
        aspect = min(bw, bh) / max(bw, bh)
        fill = a / float(bw * bh)
        # Score: prefer square + reasonably filled.
        score = aspect * (0.5 + 0.5 * fill)
        if score > best_score:
            best_score = score
            best = (x, y, bw, bh, aspect, fill, a)

    if best is None or best_score < 0.30:
        return None, {"method": "raw_fallback", "notes": [f"best blob score={best_score:.2f}"]}, True

    x, y, bw, bh, aspect, fill, area = best
    meta = {
        "method": "bbox_crop",
        "bbox": (x, y, bw, bh),
        "aspect": float(aspect),
        "fill": float(fill),
        "notes": [f"localized bbox {bw}x{bh} aspect={aspect:.2f} fill={fill:.2f}"],
    }
    return (x, y, bw, bh), meta, False


def _stage2_rings(
    img: np.ndarray, card_mm: float
) -> tuple[tuple[float, float], float, float, bool]:
    """Find bullseye center + scoring radius (px) + px_per_mm.

    Strategy:
      - The black portion of the target is the largest dark blob inside the
        cropped frame. Its centroid is the bullseye approximation.
      - Estimate px_per_mm from the black portion diameter. ISSF Air Pistol
        black portion ≈ 0.85 * card_mm in diameter; precision pistol similar.
      - Scoring radius (1-ring, outermost) ≈ card_size_px / 2 (the card edge).
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape

    # Adaptive threshold handles uneven illumination better than Otsu here.
    binv = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        blockSize=max(51, (max(h, w) // 16) | 1), C=5,
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binv = cv2.morphologyEx(binv, cv2.MORPH_OPEN, kernel)
    binv = cv2.morphologyEx(binv, cv2.MORPH_CLOSE, kernel, iterations=2)

    # Pick the largest roughly-circular blob as the target black portion.
    contours, _ = cv2.findContours(binv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    black_blob = None
    best_metric = -1.0
    for c in contours:
        area = cv2.contourArea(c)
        if area < 0.005 * h * w:
            continue
        perim = cv2.arcLength(c, True)
        if perim < 1:
            continue
        circ = 4 * math.pi * area / (perim * perim)
        # Weight by area too — prefer large circular blobs.
        metric = area * circ
        if metric > best_metric:
            best_metric = metric
            black_blob = c

    if black_blob is None:
        cx, cy = w / 2.0, h / 2.0
        black_diam_px = min(h, w)
    else:
        (cx, cy), radius = cv2.minEnclosingCircle(black_blob)
        black_diam_px = 2.0 * radius

    px_per_mm = black_diam_px / (0.85 * card_mm)
    card_px = px_per_mm * card_mm
    scoring_radius_px = card_px / 2.0

    return (float(cx), float(cy)), float(scoring_radius_px), float(px_per_mm), False


def _stage3_morph(img: np.ndarray, bullet_radius_px: float) -> tuple[np.ndarray, list[tuple[float, float]], bool]:
    """Isolate bullet-hole blobs via local-variance texture + circular Hough.

    Strategy: bullet holes have torn-paper fiber interiors; printed target
    ink is smooth. We compute a local-standard-deviation map at the kernel
    size that empirically maximized hole-vs-ink SNR (k=25), CLAHE-equalize
    it to spread the long-tailed distribution, then run `HoughCircles` with
    `HOUGH_GRADIENT_ALT` (caliber-bounded radius) on the equalized texture
    map. Detected circle centers are returned directly as centroids — this
    avoids the Stage 4 watershed collapsing adjacent detections.

    This replaces a prior HoughCircles-on-luminance + black-hat morphology
    approach, which failed because hole interiors match surrounding ink in
    mean brightness but NOT in local texture. See
    `context/changes/cv-service-boundary/frame.md` for the framing evidence
    and `cv/feature_probe.py` for the empirical feature-signal probe.

    Returns (mask, centroids, failed). Stage 4 watershed is used as a
    fallback only when HoughCircles returns no detections.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    f = gray.astype(np.float32)

    # --- Local-std map with kernel scaled to caliber ---------------------
    # Probe fixed k=25 for ~5-9 mm calibers; slug (18 mm) needs a bigger
    # window to capture the hole-interior texture rather than just the rim.
    # Cap keeps the boxFilter cheap on high-px/mm images.
    k = max(15, min(51, int(1.5 * bullet_radius_px)))
    if k % 2 == 0:
        k += 1
    mu = cv2.boxFilter(f, ddepth=cv2.CV_32F, ksize=(k, k))
    mu_sq = cv2.boxFilter(f * f, ddepth=cv2.CV_32F, ksize=(k, k))
    std_map = np.sqrt(np.maximum(mu_sq - mu * mu, 0.0))
    std_map = cv2.GaussianBlur(std_map, (0, 0), sigmaX=2.0)

    # Normalize to uint8 then CLAHE-equalize — the raw std distribution is
    # heavily long-tailed (most pixels ≈ 2-5, holes ≈ 30-70); CLAHE spreads
    # the tail so HoughCircles' internal Canny can find hole-boundary arcs.
    std_max = float(std_map.max()) + 1e-9
    std_u8 = (std_map / std_max * 255.0).clip(0, 255).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    std_eq = clahe.apply(std_u8)

    # --- HoughCircles on the texture map ---------------------------------
    # HOUGH_GRADIENT_ALT (OpenCV 4.3+) is more center-accurate than the
    # classic algorithm; param2 sensitivity is on a [0, 1] scale.
    # param2=0.80 is stricter than the 0.75 default — reduces over-detection
    # on images with rich texture (e.g. #12, #20).
    min_r = max(3, int(0.7 * bullet_radius_px))
    max_r = max(min_r + 2, int(1.3 * bullet_radius_px))
    min_dist = max(int(1.5 * bullet_radius_px), min_r + 1)
    circles = cv2.HoughCircles(
        std_eq, cv2.HOUGH_GRADIENT_ALT, dp=1.5,
        minDist=min_dist, param1=80, param2=0.80,
        minRadius=min_r, maxRadius=max_r,
    )

    # --- Build centroid list + visualization mask ------------------------
    mask = np.zeros_like(gray, dtype=np.uint8)
    centers: list[tuple[float, float]] = []
    if circles is not None:
        for c in circles[0]:
            cx, cy, r = float(c[0]), float(c[1]), int(c[2])
            centers.append((cx, cy))
            # Draw a small marker (NOT the full detection radius) so the
            # mask remains a sparse set of disjoint disks. Avoids Stage 4
            # watershed merging adjacent detections.
            cv2.circle(mask, (int(cx), int(cy)), max(2, int(r * 0.5)), 255, thickness=-1)

    # Light cleanup: 3x3 open removes any 1-pixel speckle.
    k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k3, iterations=1)

    if not centers:
        return mask, [], True
    return mask, centers, False


def _filter_blob_area(
    mask: np.ndarray,
    bullet_radius_px: float,
    min_mult: float = 0.20,
    max_mult: float = 6.0,
) -> np.ndarray:
    """Zero out blobs whose area is outside the plausible bullet-hole range.

    `max_mult` is generous (15.0 from Stage 3) so overlap clusters survive
    to be split by Stage 4 watershed; stricter defaults are kept for other
    callers.
    """
    expected = math.pi * (bullet_radius_px ** 2)
    min_a = min_mult * expected
    max_a = max_mult * expected
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n <= 1:
        return mask
    out = np.zeros_like(mask)
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if min_a <= a <= max_a:
            out[labels == i] = 255
    return out


def _stage4_watershed(mask: np.ndarray, bullet_radius_px: float) -> tuple[list[tuple[float, float]], bool]:
    """Split overlapping blobs with watershed; return list of centroids."""
    # Connected components on the mask gives initial blob grouping.
    n_lbl, labels = cv2.connectedComponents(mask)
    if n_lbl <= 1:
        return [], True

    # Distance transform inside each blob; peaks = hole centers.
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    if dist.max() < 1e-3:
        # Empty mask edge case.
        return [], True

    # Foreground markers = local maxima at distance >= ~bullet radius.
    marker_thresh = max(2.0, bullet_radius_px * 0.6)
    _, fg = cv2.threshold(dist, marker_thresh, 255, cv2.THRESH_BINARY)
    fg = fg.astype(np.uint8)

    n_fg, fg_labels = cv2.connectedComponents(fg)
    if n_fg <= 1:
        # Distance peaks too small — fall back to single-component centroids.
        return _centroids_from_mask(mask)

    # Build markers for watershed (background=1, fg labels start at 2).
    markers = fg_labels.copy().astype(np.int32)
    markers[markers > 0] += 1
    # Mark unknown region as 1.
    unknown = cv2.subtract(mask, fg)
    markers[unknown > 0] = 1

    # watershed needs a 3-channel image.
    color = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    try:
        ws = cv2.watershed(color, markers)
    except cv2.error:
        return _centroids_from_mask(mask)

    # Collect centroids per watershed label (label > 1 are objects, -1 is boundary).
    centers: list[tuple[float, float]] = []
    expected_area = math.pi * (bullet_radius_px ** 2)
    min_area = 0.15 * expected_area
    max_area = 8.0 * expected_area
    for lbl in np.unique(ws):
        if lbl <= 1 or lbl == -1:
            continue
        blob = (ws == lbl).astype(np.uint8)
        a = int(blob.sum())
        if a < min_area or a > max_area:
            continue
        m = cv2.moments(blob)
        if m["m00"] <= 0:
            continue
        cx = m["m10"] / m["m00"]
        cy = m["m01"] / m["m00"]
        centers.append((float(cx), float(cy)))

    if not centers:
        # Watershed fragmented everything out of the area filter — fall back.
        return _centroids_from_mask(mask)
    return centers, False


def _centroids_from_mask(mask: np.ndarray) -> tuple[list[tuple[float, float]], bool]:
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return [], True
    centers = []
    for i in range(1, n):
        centers.append((float(cents[i, 0]), float(cents[i, 1])))
    return centers, False


def _stage5_score(
    centers: list[tuple[float, float]],
    bullseye: tuple[float, float],
    scoring_radius_px: float,
    bullet_radius_px: float,
) -> list[int]:
    """ISSF line-break rule: subtract bullet radius from distance, then map.

    Score = 10 - floor(10 * adj_d / scoring_radius_px), clamped to [0, 10].
    """
    bx, by = bullseye
    scores: list[int] = []
    for (cx, cy) in centers:
        d = math.hypot(cx - bx, cy - by)
        # Line-break: a hole touches the higher-value ring if its edge crosses
        # into that ring. Effectively we measure from the *edge* of the hole,
        # not its center.
        adj = max(0.0, d - bullet_radius_px)
        score = 10 - int(math.floor(10 * adj / scoring_radius_px))
        score = max(0, min(10, score))
        scores.append(score)
    return scores


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _empty(failure_stage: str | None, notes: list[str]) -> dict[str, Any]:
    return {
        "scores": [],
        "total": 0,
        "centers": [],
        "bullet_radius_px": 0.0,
        "target_center": (0, 0),
        "target_radius_px": 0.0,
        "px_per_mm": 0.0,
        "failure_stage": failure_stage,
        "notes": notes,
    }
