"""Localization for the single-ellipse approach.

Goal: find the BLACK SCORING DISC directly on the full-resolution source image,
then crop a generous margin around it so every ring + every bullet hole stays
in frame after the inverse-perspective warp.

This is intentionally simpler than cv/blob_detect.crop_to_target: we do not
trust "biggest dark blob" because phone photos often have larger dark
background patches (sky, shadow, clothing). We trust "biggest *circular* dark
blob" — the black scoring disc is, by construction, the roundest large dark
feature on a ISSF target.

For image 29 specifically: a printed logo caused the existing pipeline's
downstream calibrate() to anchor on the wrong centroid. The disc itself is
correctly recoverable via adaptive-threshold + circularity filter applied
directly to the source.

Returns: (crop_gray, bbox) where bbox = (x0, y0, w, h) in source-image pixels.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from cv.blob_detect import to_gray


def _find_disc_candidates(gray: np.ndarray) -> list[dict]:
    """Run adaptive threshold + circularity filter at multiple kernel scales.

    Each candidate dict contains: contour, cx, cy, semi_a, semi_b, angle,
    anisotropy, circularity, area, score, blurred (whether pre-blur was used).

    Two threshold passes are run:
      * pass 1 (no pre-blur): finds the black scoring disc on its own, distinct
        from surrounding dark rings (image 12 case).
      * pass 2 (Gaussian blur σ=3): merges nearby dark regions; needed when
        the disc edge is soft (image 21 case).
    The downstream picker chooses the most circular large candidate across
    both passes.
    """
    h, w = gray.shape
    block = max(51, (max(h, w) // 16) | 1)
    k_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    candidates: list[dict] = []
    seen_centers: list[tuple[float, float, float]] = []
    for blurred, img in ((False, gray), (True, cv2.GaussianBlur(gray, (0, 0), 3))):
        binv = cv2.adaptiveThreshold(
            img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
            blockSize=block, C=5,
        )
        binv = cv2.morphologyEx(binv, cv2.MORPH_OPEN, k_small)
        for close_ks in (9, 17, 25, 35, 51, 71, 101):
            k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_ks, close_ks))
            b = cv2.morphologyEx(binv, cv2.MORPH_CLOSE, k_close)
            contours, _ = cv2.findContours(b, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                area = cv2.contourArea(c)
                if area < 0.005 * h * w:
                    continue
                perim = cv2.arcLength(c, True)
                if perim < 1:
                    continue
                circ = 4 * math.pi * area / (perim * perim)
                if circ < 0.30:
                    continue
                if len(c) < 5:
                    continue
                (cx, cy), (ea, eb), ang = cv2.fitEllipse(c)
                semi_a, semi_b = float(max(ea, eb)) / 2.0, float(min(ea, eb)) / 2.0
                aniso = semi_a / max(semi_b, 1e-6)
                # Deduplicate: skip if we already have a candidate near this center.
                dup = False
                for pcx, pcy, pr in seen_centers:
                    if math.hypot(cx - pcx, cy - pcy) < 0.3 * pr:
                        dup = True
                        break
                if dup:
                    continue
                seen_centers.append((cx, cy, semi_a))
                # Score: favour circular AND large (the disc is both).
                # Penalise extreme anisotropy — the disc under perspective is at
                # most ~2.5:1 in our test set; anything more is non-disc.
                score = area * (0.3 + 0.7 * circ) / max(aniso, 1.0)
                candidates.append({
                    "contour": c, "cx": float(cx), "cy": float(cy),
                    "semi_a": semi_a, "semi_b": semi_b, "angle": float(ang),
                    "anisotropy": aniso, "circularity": circ, "area": float(area),
                    "score": score, "close_ks": close_ks, "blurred": blurred,
                })
    return candidates


def _pick_disc(candidates: list[dict], img_shape: tuple[int, int]) -> dict | None:
    """Pick the best disc candidate.

    Heuristic (ordered):
      1. Drop candidates whose anisotropy exceeds 2.5 — beyond what perspective
         produces for a phone-camera disc.
      2. Drop candidates whose enclosing ROI touches the image border on >2
         sides (likely background fragments).
      3. Among the rest, pick the highest `area * circ²` (roundness dominates).
    """
    if not candidates:
        return None
    h, w = img_shape
    filtered = []
    for c in candidates:
        if c["anisotropy"] > 2.5:
            continue
        x0 = c["cx"] - c["semi_a"]; x1 = c["cx"] + c["semi_a"]
        y0 = c["cy"] - c["semi_a"]; y1 = c["cy"] + c["semi_a"]
        borders = 0
        if x0 < 0.02 * w: borders += 1
        if x1 > 0.98 * w: borders += 1
        if y0 < 0.02 * h: borders += 1
        if y1 > 0.98 * h: borders += 1
        if borders >= 3:
            continue
        filtered.append(c)
    if not filtered:
        # Fallback: ignore border filter.
        filtered = [c for c in candidates if c["anisotropy"] <= 2.5] or candidates
    filtered.sort(
        key=lambda c: c["area"] * c["circularity"] * c["circularity"],
        reverse=True,
    )
    return filtered[0]


def find_disc(gray: np.ndarray) -> dict | None:
    """Top-level disc detector: returns the best black-disc candidate or None."""
    candidates = _find_disc_candidates(gray)
    return _pick_disc(candidates, gray.shape)


def crop_to_target(
    gray: np.ndarray,
    margin_factor: float = 5.5,
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Locate the black scoring disc and crop a generous margin around it.

    margin_factor: half-crop size = margin_factor * semi_a. 5.5× covers the
    full 1-ring (radius = black_disc_radius + 3*s ≈ 1.6× black_disc_radius in
    air-pistol geometry) plus ample room for perspective expansion on the far
    side and bullet holes near the edge. For distorted photos (image 21) this
    keeps all 5 holes in frame where the existing pipeline cropped them out.
    """
    h, w = gray.shape
    disc = find_disc(gray)
    if disc is None:
        # Last-resort fallback: return the whole image.
        return gray, (0, 0, w, h)
    cx, cy = disc["cx"], disc["cy"]
    half = int(margin_factor * disc["semi_a"])
    x0 = max(0, int(cx - half)); y0 = max(0, int(cy - half))
    x1 = min(w, int(cx + half)); y1 = min(h, int(cy + half))
    return gray[y0:y1, x0:x1], (x0, y0, x1 - x0, y1 - y0)
