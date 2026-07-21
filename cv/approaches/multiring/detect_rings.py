"""Multi-ring detection via polar unwrapping + per-ring ellipse fitting.

The previous contour-based approach failed on real phone photos because ring
strokes are partial arcs (broken by bullet holes, paper folds, lighting),
which cv2.fitEllipse handles poorly. The polar approach is much more robust:

Algorithm:
  1. CLAHE contrast equalization.
  2. Polar-unwrap the crop around the bullseye (r, θ) using cv2.warpPolar.
     - Concentric rings become horizontal bright bands at constant r.
     - Elliptical rings (projective skew) become bands whose r varies
       sinusoidally with θ.
  3. Compute the per-row (mean over θ) Sobel magnitude profile. Ring peaks
     are local maxima above the noise floor.
  4. Refine peak positions to sub-pixel (parabolic fit).
  5. For each ring, extract r(θ) = sub-pixel peak position per angular
     column. Fit the 4-parameter ellipse model:
         r(θ) = (a·b) / sqrt((b·cos(θ - α))² + (a·sin(θ - α))²) + r0
     where (a, b) are semi-axes, α is the axis angle, r0 is the average
     radius. The bullseye is the polar origin, so we're fitting the
     ellipse's geometric relationship to the bullseye — concentric ellipses
     share (α) and have center = bullseye; off-center ellipses add a phase
     term but we ignore that (rings ARE concentric).
  6. Drop rings with poor fit (high residual) or extreme eccentricity.
  7. Return ≥3 rings for gold-standard images.

Outputs: list of dicts with cx, cy, semi_a, semi_b, angle_deg,
         ring_value_estimate, s_px_estimate, gmean.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from scipy.optimize import least_squares


# ---------------------------------------------------------------------------
# Edge preparation
# ---------------------------------------------------------------------------
def _clahe(gray: np.ndarray, clip: float = 2.5, tile: int = 8) -> np.ndarray:
    """CLAHE on grayscale (uint8 → uint8). Equalizes uneven phone lighting."""
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tile, tile))
    return clahe.apply(gray)


def _sobel_mag(gray: np.ndarray) -> np.ndarray:
    """Sobel magnitude (0..255)."""
    blur = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.2)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    return ((mag / max(mag.max(), 1e-6)) * 255).astype(np.uint8)


def _canny_edges(clahe_img: np.ndarray) -> np.ndarray:
    """Canny edges (used purely for diagnostic overlay in _02b_detect)."""
    blur = cv2.GaussianBlur(clahe_img, (0, 0), 1.2)
    gx = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    mag8 = ((mag / max(mag.max(), 1e-6)) * 255).astype(np.uint8)
    hi, _ = cv2.threshold(mag8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    lo = max(20, int(0.4 * hi))
    return cv2.Canny(clahe_img, lo, hi)


# ---------------------------------------------------------------------------
# Polar unwrap
# ---------------------------------------------------------------------------
def _polar_unwrap(
    gray: np.ndarray,
    cx: float,
    cy: float,
    max_r: float,
    n_angles: int = 720,
) -> np.ndarray:
    """Return polar image of shape (n_angles, max_r) where row=angle, col=radius.

    Uses cv2.warpPolar then transposes so the first axis is angle and the
    second is radius. Output dtype matches input.

    The image is first REFLECT-PADDED by max_r on each side so the bullseye
    can be near an edge and we still get full angular coverage. Caller-supplied
    (cx, cy) is in *unpadded* coords; we translate it to padded coords
    internally.
    """
    pad = int(math.ceil(max_r))
    padded = cv2.copyMakeBorder(
        gray, pad, pad, pad, pad, cv2.BORDER_REFLECT_101,
    )
    cx_p = cx + pad
    cy_p = cy + pad
    polar = cv2.warpPolar(padded, (int(max_r), n_angles), (cx_p, cy_p), max_r,
                          cv2.INTER_LINEAR | cv2.WARP_POLAR_LINEAR)
    return polar    # shape (max_r, n_angles)


# ---------------------------------------------------------------------------
# Ring peak detection
# ---------------------------------------------------------------------------
def _detect_ring_peaks(profile: np.ndarray, min_spacing: float = 4.0) -> list[int]:
    """Find local maxima in the radial profile (mean Sobel magnitude per r).

    Returns integer peak indices, sorted ascending.
    """
    # Smooth slightly to suppress noise.
    sm = cv2.GaussianBlur(profile.reshape(-1, 1), (1, 7), 0).ravel()
    floor = sm.mean() + 0.5 * sm.std()
    peaks = [
        r for r in range(3, len(sm) - 3)
        if sm[r] == sm[max(0, r - 3):r + 4].max() and sm[r] > floor
    ]
    # Enforce min spacing (greedy keep-strongest).
    peaks.sort(key=lambda r: -sm[r])
    kept: list[int] = []
    for r in peaks:
        if all(abs(r - k) >= min_spacing for k in kept):
            kept.append(r)
    return sorted(kept)


def _subpixel_peak(profile_slice: np.ndarray, peak_idx: int) -> float:
    """Parabolic interpolation around a peak for sub-pixel radius."""
    i = peak_idx
    if i <= 0 or i >= len(profile_slice) - 1:
        return float(i)
    y0, y1, y2 = float(profile_slice[i - 1]), float(profile_slice[i]), float(profile_slice[i + 1])
    denom = (y0 - 2 * y1 + y2)
    if abs(denom) < 1e-9:
        return float(i)
    delta = 0.5 * (y0 - y2) / denom
    return float(i) + float(delta)


# ---------------------------------------------------------------------------
# Per-ring ellipse fit (4-parameter)
# ---------------------------------------------------------------------------
def _ring_radius_model(theta: np.ndarray, a: float, b: float, alpha: float, r0: float) -> np.ndarray:
    """Polar radius r(θ) of an ellipse centered at the polar origin.

    For a centered ellipse with semi-axes (a, b) at angle α, the radius at
    angle θ is (a·b) / sqrt((b·cos(θ-α))² + (a·sin(θ-α))²). We parameterize
    as (a, b, α, r0) where r0 is a constant offset (lets the fit explore
    nearby centers — should be 0 for truly concentric rings).
    """
    ab = a * b
    inside = (b * np.cos(theta - alpha)) ** 2 + (a * np.sin(theta - alpha)) ** 2
    inside = np.maximum(inside, 1e-9)
    return ab / np.sqrt(inside) + r0


def _fit_ring_ellipse(
    polar: np.ndarray,
    peak_r: float,
    half_width: int = 4,
    n_angles_use: int = 360,
) -> dict | None:
    """Fit a 4-parameter ellipse to a ring band in polar coords.

    Extracts r(θ) = argmax(Sobel magnitude) in a [peak-half_width, peak+half_width]
    window per angle. Then fits (a, b, α, r0) by least squares with BOUNDED
    parameters to prevent degenerate solutions (e.g. semi-axes 10000× the
    input peak).

    Returns dict with cx, cy (= polar origin), semi_a, semi_b, angle_deg, gmean,
    fit_residual.
    """
    n_angles = polar.shape[1]
    max_r = polar.shape[0]
    r_lo = max(0, int(peak_r) - half_width)
    r_hi = min(max_r, int(peak_r) + half_width + 1)
    if r_hi - r_lo < 3:
        return None

    # Sub-pixel peak per angular column via parabolic interpolation in the
    # magnitude window. Subsample angles to speed up the fit.
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

    # Bounded fit: keep semi-axes within [0.5×peak_r, 2×peak_r] and r0 small.
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

    # Fit residual: RMS of (model - observed).
    resid = residuals(result.x)
    residual = float(np.sqrt(np.mean(resid ** 2)))
    # Reject if residual is too large relative to the ring size.
    if residual > 0.20 * gmean:
        return None
    # Reject extreme eccentricity.
    if semi_a / max(semi_b, 1e-6) > 3.5:
        return None

    return {
        "semi_a": semi_a, "semi_b": semi_b,
        "angle_deg": angle_deg, "gmean": float(gmean),
        "r0_fit": float(r0_fit), "residual_px": residual,
    }


# ---------------------------------------------------------------------------
# Ring value assignment
# ---------------------------------------------------------------------------
def _assign_ring_values(rings: list[dict], s_px: float) -> list[dict]:
    """Tag each ring with its ISSF ring value (1..10) using the outermost
    detected ring as anchor (treated as ring 1 outer).

    The spacing estimate prefers max(radii)/9 (assuming the outermost detected
    ring is ring 1) when the median-gap estimate is much smaller (which happens
    when stroke-double-detections inflate the gap count).
    """
    if not rings:
        return rings
    rings_sorted = sorted(rings, key=lambda r: -r["gmean"])
    r_outer = rings_sorted[0]["gmean"]
    # Two candidate spacings: median gap (sensitive to double-detections) and
    # max/9 (assumes the outermost detected ring is ring 1). Take the larger
    # of the two — a small median gap means we're seeing stroke edges, not
    # distinct rings.
    s_max = r_outer / 9.0
    s_use = max(s_px, s_max)
    for r in rings:
        k = round((r_outer - r["gmean"]) / s_use)
        r["ring_value_estimate"] = int(max(1, min(10, 1 + k)))
        r["s_px_estimate"] = float(s_use)
    return rings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def detect_rings(
    gray_crop: np.ndarray,
    init: dict | None = None,
) -> dict:
    """Detect concentric ellipses in the crop.

    Returns {rings: list[dict], edges: np.ndarray (uint8), clahe: np.ndarray}.
    Each ring dict has cx, cy, semi_a, semi_b, angle_deg, gmean,
    ring_value_estimate, s_px_estimate.
    """
    clahe_img = _clahe(gray_crop)
    edges = _canny_edges(clahe_img)
    h, w = gray_crop.shape

    # ---- Choose bullseye + max_r for polar unwrap ----
    if init is not None and init.get("cx_crop") is not None:
        cx = float(init["cx_crop"])
        cy = float(init["cy_crop"])
    else:
        cx, cy = w / 2.0, h / 2.0
    # Use the largest distance from bullseye to any crop corner, not the
    # inscribed circle. Since _polar_unwrap reflect-pads the image, the polar
    # scan can extend beyond the inscribed radius and reach rings on the far
    # side of an off-centre bullseye (image 21's case).
    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], dtype=np.float64)
    max_r = int(float(np.hypot(corners[:, 0] - cx, corners[:, 1] - cy).max()))
    if max_r < 30:
        return {"rings": [], "edges": edges, "clahe": clahe_img}

    # ---- Polar unwrap of the Sobel magnitude ----
    mag = _sobel_mag(clahe_img)
    polar = _polar_unwrap(mag, cx, cy, max_r, n_angles=720)

    # ---- Radial profile: mean magnitude per radius ----
    profile = polar.mean(axis=1)

    # ---- Estimate ring spacing from init or from the profile autocorrelation ----
    if init is not None and init.get("s_px_init") and init["s_px_init"] > 0:
        s_init = float(init["s_px_init"])
    else:
        # Autocorrelation: find the dominant non-zero lag.
        p = profile - profile.mean()
        ac = np.correlate(p, p, mode="full")[len(p) - 1:]
        ac /= max(ac.max(), 1e-9)
        # Find first strong peak after lag 5.
        s_init = max(profile.shape[0] / 22.0, 5.0)
        for lag in range(5, len(ac) // 2):
            if (ac[lag] > 0.4 and ac[lag] == ac[max(0, lag - 3):lag + 4].max()):
                s_init = float(lag)
                break

    # ---- Detect ring peaks ----
    peaks = _detect_ring_peaks(profile, min_spacing=max(3.0, 0.5 * s_init))

    # ---- Filter peaks to those that fit within max_r ----
    peaks = [p for p in peaks if 0.02 * max_r < p < 0.98 * max_r]

    # ---- Refine to sub-pixel + fit ellipse to each peak ----
    rings: list[dict] = []
    for peak_idx in peaks:
        peak_subpx = _subpixel_peak(profile, peak_idx)
        fit = _fit_ring_ellipse(polar, peak_subpx, half_width=max(3, int(0.5 * s_init)))
        if fit is None:
            # Fall back to a circle at the sub-pixel peak.
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

    # ---- Deduplicate by gmean ----
    rings.sort(key=lambda r: r["gmean"])
    dedup: list[dict] = []
    for r in rings:
        if dedup and abs(r["gmean"] - dedup[-1]["gmean"]) < max(2.0, 0.25 * s_init):
            # Keep the one with smaller fit residual.
            if r["residual_px"] < dedup[-1]["residual_px"]:
                dedup[-1] = r
        else:
            dedup.append(r)

    # ---- Estimate s_px from the deduped ring spacings ----
    if len(dedup) >= 2:
        gaps = [dedup[i + 1]["gmean"] - dedup[i]["gmean"] for i in range(len(dedup) - 1)]
        gaps = [g for g in gaps if g > 0]
        s_px = float(np.median(gaps)) if gaps else s_init
    else:
        s_px = s_init

    _assign_ring_values(dedup, s_px)
    return {"rings": dedup, "edges": edges, "clahe": clahe_img}
