"""Edge detection + ring-edge enhancement for iteredge.

Two outputs:
  - sobel_mag: smooth gradient magnitude (used for soft sampling).
  - canny_bin: binary edge map.

Ring-edge filtering:
  We can't perfectly separate ring-stroke edges from hole edges / digit
  edges / logo edges at this stage, but we can de-emphasize non-ring edges
  by exploiting the concentric structure: ring edges are roughly tangential
  to circles around the (initial) bullseye. We compute a "tangency weight"
  per edge pixel = 1 - |∇I · r_hat| where r_hat is the radial unit vector.
  Ring edges (tangential grad → edge tangent ⊥ radial) → weight ~1.
  Radial edges (digit strokes, hole edges facing the centre) → weight ~0.

Two potentials are produced for the optimizer:
  - dt_ring: distance transform computed from the ring-weighted edge map.
    Values near 0 mean "on a ring edge"; large values mean "far from any
    ring edge". Used as the primary energy: residuals = dt_ring at predicted
    source positions. Minimizing pulls predictions toward ring edges.
  - mag_smooth: smoothed edge magnitude (used for the coarsest stage as a
    broad-basin alignment signal).
"""
from __future__ import annotations

import cv2
import numpy as np


def sobel_magnitude(gray: np.ndarray, blur_sigma: float = 1.5) -> np.ndarray:
    ksize = max(3, int(2 * round(blur_sigma) + 1))
    blur = cv2.GaussianBlur(gray.astype(np.float32), (ksize, ksize), blur_sigma)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    return np.sqrt(gx * gx + gy * gy)


def canny_edges(gray: np.ndarray, low: float = 40.0, high: float = 120.0, blur_sigma: float = 1.5) -> np.ndarray:
    ksize = max(3, int(2 * round(blur_sigma) + 1))
    blur = cv2.GaussianBlur(gray, (ksize, ksize), blur_sigma)
    return cv2.Canny(blur, low, high, apertureSize=3, L2gradient=True)


def edge_distance_transform(canny_bin: np.ndarray) -> np.ndarray:
    """Distance to the nearest Canny edge (float32, L2)."""
    inv = (canny_bin == 0).astype(np.uint8)
    dt = cv2.distanceTransform(inv, cv2.DIST_L2, 5)
    return dt.astype(np.float32)


def enhance_ring_edges(
    gray: np.ndarray,
    cx: float,
    cy: float,
    s_px: float,
    smooth_sigma: float | None = None,
) -> dict:
    """Build the edge maps used by the optimizer.

    Returns:
      sobel:        float32 — smoothed Sobel magnitude.
      canny:        uint8   — binary Canny edges.
      ring_weight:  float32 — per-pixel weight in [0, 1] favouring tangential
                              (ring) edges over radial (digit/hole) edges.
      dt:           float32 — distance transform of all Canny edges.
      dt_ring:      float32 — distance transform of RING-WEIGHTED Canny edges
                              (canny × (ring_weight > 0.5)). This is the
                              primary potential the optimizer samples.
      mag_smooth:   float32 — smoothed (ring-weighted) edge magnitude, used
                              for the coarsest stage as a broad-basin signal.
      potential:    float32 — alias for dt_ring (legacy field name; the
                              optimizer samples this).
    """
    h, w = gray.shape
    if smooth_sigma is None:
        smooth_sigma = max(1.5, s_px / 6.0)

    gx = cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = xx - cx
    dy = yy - cy
    r = np.sqrt(dx * dx + dy * dy) + 1e-6
    rx = dx / r
    ry = dy / r

    gmag = np.maximum(mag, 1e-6)
    gdir_x = gx / gmag
    gdir_y = gy / gmag

    dot = np.abs(gdir_x * rx + gdir_y * ry)
    ring_weight = 1.0 - dot
    falloff = np.clip(r / (3.0 * max(s_px, 1.0)), 0.0, 1.0) * np.clip(
        1.0 - (r / (min(w, h) * 0.55)), 0.0, 1.0
    )
    ring_weight = ring_weight * falloff

    canny = canny_edges(gray)
    dt = edge_distance_transform(canny)

    # Ring-filtered edges: only count Canny pixels where ring_weight is high.
    ring_canny = ((canny > 0) & (ring_weight > 0.4)).astype(np.uint8) * 255
    if ring_canny.any():
        dt_ring = edge_distance_transform(ring_canny)
    else:
        dt_ring = dt.copy()

    # Smoothed ring-weighted magnitude (for the coarsest stage).
    weighted_mag = mag * ring_weight
    ksize = max(3, int(2 * round(smooth_sigma) + 1) | 1)
    mag_smooth = cv2.GaussianBlur(weighted_mag, (ksize, ksize), smooth_sigma)

    return {
        "sobel": mag,
        "canny": canny,
        "dt": dt,
        "dt_ring": dt_ring,
        "ring_weight": ring_weight.astype(np.float32),
        "mag_smooth": mag_smooth.astype(np.float32),
        # `potential` is what the optimizer samples. Default to ALL-canny DT
        # because ring-weighted DT can disagree with the all-edges evaluation
        # metric (the ring_weight is computed at the initial center, so it
        # becomes inaccurate as the warp shifts the rings).
        "potential": dt,
    }
