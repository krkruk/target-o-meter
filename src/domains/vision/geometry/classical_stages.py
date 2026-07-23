"""Classical primitives the pipeline still uses (stage 1 grayscale + black-disc
calibration fallback + ISSF line-break scoring).

Ported from ``cv/blob_detect.py`` — only ``to_gray``, the collaborators of
``calibrate`` (``_sobel_mag``, ``blackdisc_center``, ``ellipse_geometry``),
``calibrate`` itself, and ``score_holes`` (the symbols
``full_pipeline/pipeline.py`` imports). The matched-filter/hole-detection
code is NOT ported (LLM owns hole detection now).

Per the one-class-per-file rule (``lessons.md``), the three logical concerns
grayscale / black-disc calibration / scoring are three classes; helpers that
serve only one class stay as private module functions or static methods of
that class.

Math is lifted verbatim from cv/ — only structure (free function → class
method, dict → ``Calibration`` arg) changes.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from src.domains.vision.geometry.calibration import Calibration


# Ring-index prior: the black/white boundary is between rings 6 and 7, i.e.
# the outer edge of ring 7 = 3 ring-steps outside the 10-ring (bullseye).
RING_STEPS_BW_TO_BULL = 3

# Caliber → bullet radius table (carried verbatim from cv/blob_detect.py:35).
BULLET_RADIUS_MM = {"22lr": 2.85, "9x19": 4.5, ".223Rem": 2.78, "slug": 9.0}


def _sobel_mag(gray: np.ndarray) -> np.ndarray:
    """Sobel magnitude (0..255 uint8) — ported verbatim from cv/blob_detect.py:158-163."""
    blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
    gx = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    return ((mag / max(mag.max(), 1e-6)) * 255).astype(np.uint8)


def _ellipse_geometry(contour: np.ndarray) -> dict:
    """Robust ellipse axes + major direction (convention-free via projection
    range). Ported verbatim from cv/blob_detect.py:138-152."""
    (cx, cy), (w, h), ang = cv2.fitEllipse(contour)
    th = math.radians(ang)
    dir_w = np.array([math.cos(th), math.sin(th)])
    dir_h = np.array([-math.sin(th), math.cos(th)])
    pts = contour[:, 0, :].astype(np.float64) - np.array([cx, cy])
    proj_w = np.abs(pts @ dir_w)
    proj_h = np.abs(pts @ dir_h)
    if proj_w.max() >= proj_h.max():
        major_dir, semi_a, semi_b = dir_w, float(proj_w.max()), float(proj_h.max())
    else:
        major_dir, semi_a, semi_b = dir_h, float(proj_h.max()), float(proj_w.max())
    return {
        "cx": cx, "cy": cy, "semi_a": semi_a, "semi_b": semi_b,
        "major_dir": major_dir, "anisotropy": semi_a / max(semi_b, 1e-6),
    }


def _blackdisc_center(gray: np.ndarray) -> tuple[float, float, float, np.ndarray, float, float]:
    """Bullseye estimate = centroid of the largest dark blob (the black scoring
    disc), with intra-disc holes filled by a closing kernel. Returns
    ``(cx, cy, anisotropy, major_dir, semi_a, semi_b)`` — the cv/ signature
    unpacked into positional returns (callers read it positionally).

    Ported verbatim from cv/blob_detect.py:226-250.
    """
    h, w = gray.shape
    g = cv2.GaussianBlur(gray, (0, 0), 3)
    b = cv2.adaptiveThreshold(
        g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        max(51, (max(h, w) // 16) | 1), C=5,
    )
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, k)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(b, 8)
    if n <= 1:
        return w / 2, h / 2, 1.0, np.array([1.0, 0.0]), 0.0, 0.0
    areas = [int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n)]
    big = 1 + int(np.argmax(areas))
    cx, cy = float(cents[big][0]), float(cents[big][1])
    cnts, _ = cv2.findContours(
        (labels == big).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
    )
    aniso = 1.0
    major_dir = np.array([1.0, 0.0])
    semi_a = semi_b = 0.0
    if cnts:
        geo = _ellipse_geometry(max(cnts, key=cv2.contourArea))
        aniso = geo["anisotropy"]
        major_dir = geo["major_dir"]
        semi_a, semi_b = geo["semi_a"], geo["semi_b"]
    return cx, cy, aniso, major_dir, semi_a, semi_b


class ImageGrayscaler:
    """Stage-1 grayscale conversion — ``cv/blob_detect.py:41-42`` verbatim."""

    @staticmethod
    def to_gray(bgr: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


class BlackDiscCalibrator:
    """Two-anchor radial-profile calibration — ``cv/blob_detect.py:253-294`` verbatim.

    Anchors: black/white boundary (ring-7 outer = 3 steps from the 10-ring) and
    bullseye (10-ring outer = boundary − 3·s). The boundary is read off the
    angle-averaged radial intensity transition (refined to the nearest Sobel
    gradient peak); the ring spacing s is the value best aligning gradient
    peaks to boundary ± k·s. Robust to bullet-hole noise (angle averaging).
    """

    @staticmethod
    def calibrate(gray_crop: np.ndarray) -> Calibration:
        h, w = gray_crop.shape
        cx, cy, aniso, major_dir, semi_a, semi_b = _blackdisc_center(gray_crop)
        maxR = int(min(cx, cy, w - cx, h - cy) - 2)
        mag = _sobel_mag(gray_crop)
        g_pol = cv2.warpPolar(
            mag, (maxR, 720), (cx, cy), maxR,
            cv2.INTER_LINEAR | cv2.WARP_POLAR_LINEAR,
        )
        i_pol = cv2.warpPolar(
            gray_crop, (maxR, 720), (cx, cy), maxR,
            cv2.INTER_LINEAR | cv2.WARP_POLAR_LINEAR,
        )
        gp = cv2.GaussianBlur(g_pol.mean(axis=0).reshape(-1, 1), (1, 9), 0).ravel()
        ip = cv2.GaussianBlur(i_pol.mean(axis=0).reshape(-1, 1), (1, 21), 0).ravel()

        dip = np.gradient(ip)
        lo, hi = int(0.20 * maxR), int(0.80 * maxR)
        r_t = lo + int(np.argmax(dip[lo:hi]))
        win = max(8, int(0.08 * maxR))
        r_bw = max(0, r_t - win) + int(np.argmax(gp[max(0, r_t - win):r_t + win]))

        peaks = [
            r for r in range(6, maxR - 6)
            if gp[r] == gp[max(0, r - 6):r + 7].max() and gp[r] > gp.mean()
        ]
        best_s, best_score = r_bw / 8.0, -1
        for s in np.arange(r_bw / 14, r_bw / 4, max(1.0, r_bw / 200)):
            sc = sum(
                1 for k in range(-4, 9)
                if 4 < (r_bw + k * s) < maxR - 4
                and min(abs(r_bw + k * s - p) for p in peaks) < max(2.0, 0.12 * s)
            )
            if sc > best_score:
                best_score, best_s = sc, float(s)
        s_px = best_s
        r_bull = r_bw - 3 * s_px

        ok = r_bull > 0.05 * r_bw and s_px > 0
        return Calibration(
            shape=(h, w),
            cx=cx,
            cy=cy,
            s_px=float(s_px),
            r_bull_px=float(r_bull),
            r_bw_px=float(r_bw),
            ok=ok,
        )


class IssfScorer:
    """ISSF line-break scoring — ``cv/blob_detect.py:604-614`` verbatim.

    ``score = 10 - ceil((dist(bull,hole) - r_hole - r_bull)/s)``, clamped to
    ``[0, 10]``. Uses the *detected* hole radius (user direction).
    """

    @staticmethod
    def score_holes(
        holes: list[tuple[float, float, float]],
        cal: Calibration,
    ) -> list[int]:
        s, r_bull = cal.s_px, cal.r_bull_px
        cx, cy = cal.cx, cal.cy
        scores: list[int] = []
        for x, y, r in holes:
            d = math.hypot(x - cx, y - cy) - r           # line-break: subtract hole radius
            steps = int(math.ceil((d - r_bull) / s)) if d > r_bull else 0
            scores.append(max(0, min(10, 10 - steps)))
        return scores
