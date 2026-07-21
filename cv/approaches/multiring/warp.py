"""Apply the rectifying homography H to the crop.

Computes the output canvas size from the warped extent of the crop corners +
the warped bullseye location, then runs cv2.warpPerspective. Returns
(warped, H, H_inv, meta) where meta is a dict carrying:
  - bullseye_warped (cx, cy in warped px)
  - r_ring1_warped (1-ring outer radius in warped px, = r_bw_warped + 6·s_warped)
  - r_bw_warped
  - s_warped
  - out_size (w, h)

The bullseye location is recovered by applying H to the cropped-frame
bullseye (mean ring center). The 1-ring radius is recovered from the warped
semi-axis of the largest detected ring.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from cv.approaches.multiring.homography import (
    average_shared_metric,
    matrix_inverse_sqrt,
)


def _warp_pixel_extent(H: np.ndarray, corners: np.ndarray) -> tuple[float, float, float, float]:
    """Map image-plane corners through H, return (xmin, ymin, xmax, ymax)."""
    homog = np.hstack([corners, np.ones((corners.shape[0], 1))])
    mapped = (H @ homog.T).T
    mapped = mapped[:, :2] / mapped[:, 2:3]
    return (float(mapped[:, 0].min()), float(mapped[:, 1].min()),
            float(mapped[:, 0].max()), float(mapped[:, 1].max()))


def apply_homography_to_crop(
    crop: np.ndarray,
    rings: list[dict],
    H: np.ndarray,
    H_inv: np.ndarray,
    fill_value: int = 245,
) -> tuple[np.ndarray, dict]:
    """Warp the crop into a rectified canvas.

    Output canvas: square, sized to fit the warped extent of all crop corners
    + a 5% margin. The bullseye (mean ring center) lands at the centroid of
    the warped corners' bounding box.

    Returns (warped, meta).
    """
    h, w = crop.shape[:2]
    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], dtype=np.float64)
    xmin, ymin, xmax, ymax = _warp_pixel_extent(H, corners)

    # Translate so the warped image's bounding box starts at (0, 0).
    tx, ty = -xmin, -ymin
    T = np.array([
        [1, 0, tx],
        [0, 1, ty],
        [0, 0,  1 ],
    ], dtype=np.float64)
    H_eff = T @ H
    out_w = int(math.ceil(xmax - xmin)) + 1
    out_h = int(math.ceil(ymax - ymin)) + 1
    # Make it square (larger of two) so the warp canvas matches the
    # normalization stage's expectation of a roughly-square input.
    out_size = max(out_w, out_h)
    H_eff = T @ H
    H_eff_inv = np.linalg.inv(H_eff)

    # Update H, H_inv to the translated versions so the rest of the pipeline
    # uses coordinates consistent with the warped canvas.
    warped = cv2.warpPerspective(
        crop, H_eff, (out_size, out_size),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=fill_value,
    )

    # ---- Recover bullseye location in warped frame ----
    # Use the weighted-average ring center as the bullseye prior.
    try:
        _, center, _ = average_shared_metric(rings)
    except ValueError:
        center = np.array([w / 2.0, h / 2.0])
    cx_w, cy_w, _w = (H_eff @ np.array([center[0], center[1], 1.0]))
    cx_w /= max(_w, 1e-9)
    cy_w /= max(_w, 1e-9)
    bullseye_warped = (float(cx_w), float(cy_w))

    # ---- Recover 1-ring outer radius in warped frame ----
    # For each ring, the warped geometric-mean radius is gmean · |Q^{-1/2} · u|
    # averaged over directions u on the unit circle. For our H = T·[Q^{-1/2}|t]
    # (affine part), every direction rescales by a single factor that makes
    # ellipses → circles of radius proportional to gmean. Compute that factor
    # from Q^{-1/2}: the average of (1/||Q^{-1/2} · u||) over u on the unit
    # circle equals 1 / sqrt(det(Q^{-1/2})) ... no, more useful: take the
    # geometric mean of the eigenvalues of Q^{-1/2}, which is sqrt(1/sqrt(det Q)).
    # Simpler: directly transform the ring's contour points and fit a circle.
    gmean_warped_radii: list[float] = []
    for r in rings:
        # Sample 36 points on the ellipse, transform, fit radius.
        th = math.radians(r["angle_deg"])
        ca, sa = math.cos(th), math.sin(th)
        a, b = r["semi_a"], r["semi_b"]
        pts = np.array([
            [r["cx"] + a * ca * math.cos(k) - b * sa * math.sin(k),
             r["cy"] + a * sa * math.cos(k) + b * ca * math.sin(k)]
            for k in np.linspace(0, 2 * math.pi, 36, endpoint=False)
        ], dtype=np.float64)
        homog = np.hstack([pts, np.ones((pts.shape[0], 1))])
        mapped = (H_eff @ homog.T).T
        mapped = mapped[:, :2] / mapped[:, 2:3]
        d = np.hypot(mapped[:, 0] - cx_w, mapped[:, 1] - cy_w)
        gmean_warped_radii.append(float(np.sqrt(np.mean(d * d))))

    if not gmean_warped_radii:
        s_warped = float(out_size) / 22.0
        r_bw_warped = 7.0 * s_warped
        r_ring1_warped = 13.0 * s_warped
    else:
        # Sort outermost-first. Adjacent gaps = ring spacing.
        radii_sorted = sorted(gmean_warped_radii, reverse=True)
        # Enforce minimum spacing — collapse rings closer than 0.4 × median gap
        # (likely double-detections of the same ring stroke's inner+outer edges).
        if len(radii_sorted) >= 2:
            preliminary_gaps = [radii_sorted[i] - radii_sorted[i + 1]
                                 for i in range(len(radii_sorted) - 1)
                                 if radii_sorted[i] - radii_sorted[i + 1] > 0]
            s_prelim = float(np.median(preliminary_gaps)) if preliminary_gaps else radii_sorted[0] / 5.0
            collapsed = [radii_sorted[0]]
            for r in radii_sorted[1:]:
                if collapsed[-1] - r > 0.4 * s_prelim:
                    collapsed.append(r)
            radii_use = collapsed
        else:
            radii_use = radii_sorted

        # ---- Robust s estimation via radial Sobel profile of the warped image ----
        # The warped image should have circular rings; their radial Sobel profile
        # peaks at multiples of s from the bullseye. Use a brute-force search
        # over s to find the value that best explains the detected radii AND the
        # Sobel peaks.
        warped_clahe = warped if warped.ndim == 2 else cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
        gx = cv2.Sobel(warped_clahe.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(warped_clahe.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
        # Compute magnitude as uint8 to bound the range; warpPolar with float
        # types can produce Inf/NaN at the boundary that breaks downstream
        # statistics.
        mag_f = np.sqrt(gx * gx + gy * gy)
        mag_f = np.clip(mag_f, 0, 255).astype(np.uint8)
        # Cap the polar unwrap at the largest visible ring (the actual data
        # beyond it is just border fill). Avoids sampling huge polar images
        # when the warped canvas is much larger than the target.
        max_ring = max(gmean_warped_radii) if gmean_warped_radii else float(out_size) / 2
        max_r_prof = int(min(out_w - 1, out_h - 1, int(max_ring * 1.5)))
        if max_r_prof > 30:
            pol = cv2.warpPolar(mag_f, (max_r_prof, 360), (cx_w, cy_w), max_r_prof,
                                cv2.INTER_LINEAR | cv2.WARP_POLAR_LINEAR)
            profile = cv2.GaussianBlur(pol.mean(axis=0, dtype=np.float64).reshape(-1, 1), (1, 9), 0).ravel()
            prof_peaks = [
                r for r in range(3, max_r_prof - 3)
                if profile[r] == profile[max(0, r - 4):r + 5].max()
                and profile[r] > profile.mean() + 0.3 * profile.std()
            ]
        else:
            prof_peaks = []

        # Brute-force s: best value explains the most detected radii + Sobel peaks
        # as integer multiples of s.
        all_radii = list(radii_use) + list(prof_peaks)
        max_R = max(all_radii) if all_radii else out_size / 2
        best_s = float(np.median([radii_use[i] - radii_use[i + 1]
                                  for i in range(len(radii_use) - 1)
                                  if radii_use[i] - radii_use[i + 1] > 0]
                                 or [max_R / 5.0]))
        best_score = -1.0
        for s_try in np.arange(max(best_s * 0.6, 5.0), best_s * 1.6, max(0.5, best_s * 0.02)):
            sc = 0
            for r in all_radii:
                k = r / s_try
                ki = round(k)
                if 1 <= ki <= 12 and abs(k - ki) < 0.20:
                    sc += 1
            if sc > best_score:
                best_score = sc
                best_s = float(s_try)
        s_warped = best_s

        # Ring 1 outer = 9 × s (since bullseye is at origin).
        r_ring1_warped = 9.0 * s_warped
        r_bw_warped = 7.0 * s_warped

    meta = {
        "H_eff": H_eff,
        "H_eff_inv": H_eff_inv,
        "T_translation": (float(tx), float(ty)),
        "out_size": (int(out_size), int(out_size)),
        "bullseye_warped": bullseye_warped,
        "r_bw_warped": float(r_bw_warped),
        "r_ring1_warped": float(r_ring1_warped),
        "s_warped": float(s_warped),
        "warped_ring_radii": gmean_warped_radii,
    }
    return warped, meta
