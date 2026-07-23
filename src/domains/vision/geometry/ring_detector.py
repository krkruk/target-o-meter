"""Stage 3 detect rings — bounded 4-parameter ellipse fit per ring (7–13 rings).

Ported verbatim from ``cv/approaches/multiring/detect_rings.py`` (375 LOC at
commit 76f6fc4). The polar-unwrap approach is much more robust to broken
ring strokes (bullet holes, paper folds) than contour-based fitting.

Math is lifted as-is into class methods and module private helpers; only the
structure (free functions → ``RingDetector`` class + helpers) changes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from scipy.optimize import least_squares


@dataclass
class RingDetection:
    """Shape returned by ``RingDetector.detect`` — preserved as the cv/ dict so
    downstream code reads ``r["cx"], r["semi_a"]`` etc. unchanged."""

    rings: list[dict]
    edges: np.ndarray      # uint8 Canny overlay (diagnostic)
    clahe: np.ndarray      # uint8 CLAHE-equalized crop (diagnostic)


def _clahe(gray: np.ndarray, clip: float = 2.5, tile: int = 8) -> np.ndarray:
    """CLAHE on grayscale (uint8 → uint8). cv/approaches/multiring/detect_rings.py:42-45."""
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tile, tile))
    return clahe.apply(gray)


def _sobel_mag(gray: np.ndarray) -> np.ndarray:
    """Sobel magnitude (0..255). cv/approaches/multiring/detect_rings.py:48-54."""
    blur = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.2)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    return ((mag / max(mag.max(), 1e-6)) * 255).astype(np.uint8)


def _canny_edges(clahe_img: np.ndarray) -> np.ndarray:
    """Canny edges (diagnostic overlay). cv/approaches/multiring/detect_rings.py:57-66."""
    blur = cv2.GaussianBlur(clahe_img, (0, 0), 1.2)
    gx = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    mag8 = ((mag / max(mag.max(), 1e-6)) * 255).astype(np.uint8)
    hi, _ = cv2.threshold(mag8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    lo = max(20, int(0.4 * hi))
    return cv2.Canny(clahe_img, lo, hi)


def _polar_unwrap(
    gray: np.ndarray, cx: float, cy: float, max_r: float, n_angles: int = 720,
) -> np.ndarray:
    """Polar image of shape ``(n_angles, max_r)``. cv/approaches/multiring/detect_rings.py:72-97."""
    pad = int(math.ceil(max_r))
    padded = cv2.copyMakeBorder(gray, pad, pad, pad, pad, cv2.BORDER_REFLECT_101)
    cx_p = cx + pad
    cy_p = cy + pad
    polar = cv2.warpPolar(padded, (int(max_r), n_angles), (cx_p, cy_p), max_r,
                          cv2.INTER_LINEAR | cv2.WARP_POLAR_LINEAR)
    return polar    # shape (max_r, n_angles)


def _detect_ring_peaks(profile: np.ndarray, min_spacing: float = 4.0) -> list[int]:
    """Local maxima in the radial profile. cv/approaches/multiring/detect_rings.py:103-121."""
    sm = cv2.GaussianBlur(profile.reshape(-1, 1), (1, 7), 0).ravel()
    floor = sm.mean() + 0.5 * sm.std()
    peaks = [
        r for r in range(3, len(sm) - 3)
        if sm[r] == sm[max(0, r - 3):r + 4].max() and sm[r] > floor
    ]
    peaks.sort(key=lambda r: -sm[r])
    kept: list[int] = []
    for r in peaks:
        if all(abs(r - k) >= min_spacing for k in kept):
            kept.append(r)
    return sorted(kept)


def _subpixel_peak(profile_slice: np.ndarray, peak_idx: int) -> float:
    """Parabolic interpolation around a peak. cv/approaches/multiring/detect_rings.py:124-134."""
    i = peak_idx
    if i <= 0 or i >= len(profile_slice) - 1:
        return float(i)
    y0, y1, y2 = float(profile_slice[i - 1]), float(profile_slice[i]), float(profile_slice[i + 1])
    denom = (y0 - 2 * y1 + y2)
    if abs(denom) < 1e-9:
        return float(i)
    delta = 0.5 * (y0 - y2) / denom
    return float(i) + float(delta)


def _ring_radius_model(theta: np.ndarray, a: float, b: float, alpha: float, r0: float) -> np.ndarray:
    """Polar radius r(θ) of a centered ellipse. cv/approaches/multiring/detect_rings.py:140-151."""
    ab = a * b
    inside = (b * np.cos(theta - alpha)) ** 2 + (a * np.sin(theta - alpha)) ** 2
    inside = np.maximum(inside, 1e-9)
    return ab / np.sqrt(inside) + r0


def _fit_ring_ellipse(
    polar: np.ndarray, peak_r: float,
    half_width: int = 4, n_angles_use: int = 360,
) -> dict | None:
    """Fit a 4-parameter ellipse to a ring band. cv/approaches/multiring/detect_rings.py:154-242."""
    n_angles = polar.shape[1]
    max_r = polar.shape[0]
    r_lo = max(0, int(peak_r) - half_width)
    r_hi = min(max_r, int(peak_r) + half_width + 1)
    if r_hi - r_lo < 3:
        return None

    angle_idx = np.linspace(0, n_angles - 1, n_angles_use).astype(int)
    thetas = (angle_idx.astype(float) / n_angles) * 2.0 * math.pi
    r_per_theta = np.zeros(n_angles_use, dtype=np.float64)
    valid = np.ones(n_angles_use, dtype=bool)
    for i, ai in enumerate(angle_idx):
        col = polar[r_lo:r_hi, ai].astype(np.float64)
        if col.size < 3 or col.max() < 1e-6:
            valid[i] = False
            continue
        local_peak = int(np.argmax(col))
        if local_peak == 0 or local_peak == len(col) - 1:
            r_per_theta[i] = float(r_lo + local_peak)
            continue
        y0, y1, y2 = float(col[local_peak - 1]), float(col[local_peak]), float(col[local_peak + 1])
        denom = (y0 - 2 * y1 + y2)
        delta = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-9 else 0.0
        r_per_theta[i] = float(r_lo + local_peak + delta)

    if valid.sum() < n_angles_use // 2:
        return None

    a0 = max(1.0, float(peak_r))
    r_lo_bound = max(0.5 * float(peak_r), 1.0)
    r_hi_bound = max(2.0 * float(peak_r), float(peak_r) + 5.0)

    def residuals(p):
        a, b, alpha, r0 = p
        return _ring_radius_model(thetas, a, b, alpha, r0)[valid] - r_per_theta[valid]

    try:
        result = least_squares(
            residuals, x0=[a0, a0, 0.0, 0.0],
            method="trf",
            bounds=([r_lo_bound, r_lo_bound, -math.pi, -0.3 * float(peak_r)],
                    [r_hi_bound, r_hi_bound,  math.pi,  0.3 * float(peak_r)]),
            max_nfev=200,
        )
    except Exception:
        return None

    a_fit, b_fit, alpha_fit, r0_fit = result.x
    semi_a = float(max(a_fit, b_fit))
    semi_b = float(min(a_fit, b_fit))
    if b_fit > a_fit:
        alpha_fit += math.pi / 2
    angle_deg = float(math.degrees(alpha_fit) % 180.0)
    gmean = math.sqrt(semi_a * semi_b)

    resid = residuals(result.x)
    residual = float(np.sqrt(np.mean(resid ** 2)))
    if residual > 0.20 * gmean:
        return None
    if semi_a / max(semi_b, 1e-6) > 3.5:
        return None

    return {
        "semi_a": semi_a, "semi_b": semi_b,
        "angle_deg": angle_deg, "gmean": float(gmean),
        "r0_fit": float(r0_fit), "residual_px": residual,
    }


def _assign_ring_values(rings: list[dict], s_px: float) -> list[dict]:
    """Tag each ring with its ISSF ring value. cv/approaches/multiring/detect_rings.py:248-270."""
    if not rings:
        return rings
    rings_sorted = sorted(rings, key=lambda r: -r["gmean"])
    r_outer = rings_sorted[0]["gmean"]
    s_max = r_outer / 9.0
    s_use = max(s_px, s_max)
    for r in rings:
        k = round((r_outer - r["gmean"]) / s_use)
        r["ring_value_estimate"] = int(max(1, min(10, 1 + k)))
        r["s_px_estimate"] = float(s_use)
    return rings


class RingDetector:
    """Detect concentric ellipses in the crop.

    ``detect(gray_crop, init=None) -> RingDetection`` where ``RingDetection``
    carries ``{rings, edges, clahe}`` (the cv/ return shape — preserved so
    downstream code reads ``r["cx"], r["semi_a"]`` etc. unchanged).

    Ported verbatim from cv/approaches/multiring/detect_rings.py:276-375.
    """

    @staticmethod
    def detect(gray_crop: np.ndarray, init: dict | None = None) -> RingDetection:
        clahe_img = _clahe(gray_crop)
        edges = _canny_edges(clahe_img)
        h, w = gray_crop.shape

        # ---- Choose bullseye + max_r for polar unwrap ----
        if init is not None and init.get("cx_crop") is not None:
            cx = float(init["cx_crop"])
            cy = float(init["cy_crop"])
        else:
            cx, cy = w / 2.0, h / 2.0
        corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], dtype=np.float64)
        max_r = int(float(np.hypot(corners[:, 0] - cx, corners[:, 1] - cy).max()))
        if max_r < 30:
            return RingDetection(rings=[], edges=edges, clahe=clahe_img)

        mag = _sobel_mag(clahe_img)
        polar = _polar_unwrap(mag, cx, cy, max_r, n_angles=720)

        profile = polar.mean(axis=1)

        if init is not None and init.get("s_px_init") and init["s_px_init"] > 0:
            s_init = float(init["s_px_init"])
        else:
            p = profile - profile.mean()
            ac = np.correlate(p, p, mode="full")[len(p) - 1:]
            ac /= max(ac.max(), 1e-9)
            s_init = max(profile.shape[0] / 22.0, 5.0)
            for lag in range(5, len(ac) // 2):
                if (ac[lag] > 0.4 and ac[lag] == ac[max(0, lag - 3):lag + 4].max()):
                    s_init = float(lag)
                    break

        peaks = _detect_ring_peaks(profile, min_spacing=max(3.0, 0.5 * s_init))
        peaks = [p for p in peaks if 0.02 * max_r < p < 0.98 * max_r]

        rings: list[dict] = []
        for peak_idx in peaks:
            peak_subpx = _subpixel_peak(profile, peak_idx)
            fit = _fit_ring_ellipse(polar, peak_subpx, half_width=max(3, int(0.5 * s_init)))
            if fit is None:
                r = float(peak_subpx)
                fit = {
                    "semi_a": r, "semi_b": r, "angle_deg": 0.0,
                    "gmean": r, "r0_fit": 0.0, "residual_px": 0.0,
                }
            rings.append({
                "cx": cx, "cy": cy,
                "semi_a": fit["semi_a"], "semi_b": fit["semi_b"],
                "angle_deg": fit["angle_deg"],
                "gmean": fit["gmean"],
                "r0_fit": fit["r0_fit"],
                "residual_px": fit["residual_px"],
                "peak_r": float(peak_subpx),
            })

        rings.sort(key=lambda r: r["gmean"])
        dedup: list[dict] = []
        for r in rings:
            if dedup and abs(r["gmean"] - dedup[-1]["gmean"]) < max(2.0, 0.25 * s_init):
                if r["residual_px"] < dedup[-1]["residual_px"]:
                    dedup[-1] = r
            else:
                dedup.append(r)

        if len(dedup) >= 2:
            gaps = [dedup[i + 1]["gmean"] - dedup[i]["gmean"] for i in range(len(dedup) - 1)]
            gaps = [g for g in gaps if g > 0]
            s_px = float(np.median(gaps)) if gaps else s_init
        else:
            s_px = s_init

        _assign_ring_values(dedup, s_px)
        return RingDetection(rings=dedup, edges=edges, clahe=clahe_img)
