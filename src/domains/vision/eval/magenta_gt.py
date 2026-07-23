"""Magenta ground-truth parser for ``resources/train/*_marked.jpg``.

Ported verbatim from ``cv/gt.py`` (commit 76f6fc4). The user manually marked
each true bullet hole with a small magenta DOT at its centre. This module
extracts those dots and returns per-image ground-truth hole centres.

IMPORTANT: magenta is eval-only. The detection algorithm itself is pure
grayscale and must never depend on this colour.

Lives in ``eval/`` per the plan (Phase 4 ports the rest of the eval tooling
here). ``AdaptiveFrameSizer`` imports from here because it consumes the GT
hole extent when running in CLI / eval mode — production mode does not pass
``gt_marked_path``, so the GT code path is dormant in production.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageOps


def load_bgr(path: str) -> np.ndarray:
    """Load an image EXIF-normalised to upright orientation, as BGR uint8.
    Kept here as an eval-side helper so eval/metadata_loader + adaptive_frame
    don't have to reach into geometry/. Ported verbatim from cv/gt.py:19-24."""
    pil = Image.open(path)
    pil = ImageOps.exif_transpose(pil)
    rgb = np.array(pil.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def extract_magenta_mask(bgr: np.ndarray) -> np.ndarray:
    """Binary mask of magenta pixels.

    Magenta = high R, high B, low G (balanced between red and blue, clearly
    below the green channel so white/grey paper is excluded).

    Ported verbatim from cv/gt.py:27-43.
    """
    r = bgr[:, :, 2].astype(np.int16)
    g = bgr[:, :, 1].astype(np.int16)
    b = bgr[:, :, 0].astype(np.int16)
    magenta = (
        (r >= 150)
        & (b >= 140)
        & (g <= 110)
        & (np.minimum(r, b) - g >= 40)
        & (np.abs(r - b) <= 90)
    )
    return magenta.astype(np.uint8) * 255


# The magenta brush is a fixed-size disk. Estimated from the data:
# unit-dot area ≈ 1605 px → radius ≈ 22.6 px. Used to split merged dots.
DOT_RADIUS_PX = 22.6


def _split_component(
    comp_mask: np.ndarray, dot_r: float,
) -> list[tuple[float, float]]:
    """Split one magenta component (possibly several overlapping dots) into
    centres via distanceTransform peaks.

    Ported verbatim from cv/gt.py:51-78.
    """
    dist = cv2.distanceTransform(comp_mask, cv2.DIST_L2, 5)
    kr = max(3, int(0.8 * dot_r))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * kr + 1, 2 * kr + 1))
    dilated = cv2.dilate(dist, kernel)
    peaks = (dist >= dilated) & (dist > 0.5 * dot_r)
    ys, xs = np.where(peaks)
    if len(xs) == 0:
        m = cv2.moments(comp_mask)
        if m["m00"] > 0:
            return [(m["m10"] / m["m00"], m["m01"] / m["m00"])]
        return []
    h, w = comp_mask.shape
    centers: list[tuple[float, float]] = []
    for x, y in zip(xs, ys):
        x0, x1 = max(0, x - int(dot_r)), min(w, x + int(dot_r) + 1)
        y0, y1 = max(0, y - int(dot_r)), min(h, y + int(dot_r) + 1)
        m = cv2.moments(comp_mask[y0:y1, x0:x1])
        if m["m00"] > 0:
            centers.append((m["m10"] / m["m00"] + x0, m["m01"] / m["m00"] + y0))
        else:
            centers.append((float(x), float(y)))
    return centers


def magenta_centers(
    bgr: np.ndarray,
    min_area: int = 5,
    max_area_frac: float = 0.004,
    dot_r: float = DOT_RADIUS_PX,
) -> tuple[list[tuple[float, float]], np.ndarray]:
    """Return ``(centers, label_viz)`` for magenta dots.

    Touching/overlapping dots (common in dense clusters) are split via
    distanceTransform peaks, so each true hole yields one centre.

    Ported verbatim from cv/gt.py:81-109.
    """
    mask = extract_magenta_mask(bgr)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    max_area = max(8, int(mask.size * max_area_frac))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    centers: list[tuple[float, float]] = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area or area > max_area:
            continue
        comp = (labels == i).astype(np.uint8)
        centers.extend(_split_component(comp, dot_r))

    viz = bgr.copy()
    for x, y in centers:
        cv2.circle(viz, (int(x), int(y)), 8, (0, 255, 0), 2)
    return centers, viz
