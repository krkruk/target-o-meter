"""Blob-detection pipeline for ISSF paper targets (cv-service-boundary, iter 9).

User direction (research-blob-detection):
  * Calibrate against the rings using TWO anchors: the black/white boundary
    (between rings 6 and 7) and the bullseye (innermost ring).
    Ring spacing s_px = (r_bw - r_bull) / 3.
  * Edge detection (blur -> gradient diff -> edges) + HoughCircles for rings.
  * Perspective normalisation via homography (affine fronto-parallel stretch
    along the black-disc minor axis; no in-plane rotation).
  * Hole detection via cv2.SimpleBlobDetector (multi-level thresholding).
  * Scoring: ISSF line-break rule, dist(bullseye,hole) - detected radius.
  * Pure grayscale for detection; magenta is eval-only.

Reusable primitives (adaptive-threshold black-disc, multi-range HoughCircles
rings) are ported from cv/tmp/probe_ring_calibration_v8.py and extended with
the two-anchor calibration + homography + blob hole detection.

Intermediates: resources/train/intermediate_blob/.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from cv.gt import load_bgr

# Ring-index prior: the black/white boundary is between rings 6 and 7, i.e. the
# outer edge of ring 7 = 3 ring-steps outside the 10-ring (bullseye).
RING_STEPS_BW_TO_BULL = 3

BULLET_RADIUS_MM = {"22lr": 2.85, "9x19": 4.5, ".223Rem": 2.78, "slug": 9.0}


# ---------------------------------------------------------------------------
# Load + localise
# ---------------------------------------------------------------------------
def to_gray(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


def crop_to_target(gray: np.ndarray, expand: float = 0.20) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Localise the target as the most *circular* large dark blob (the black disc),
    not merely the largest — phone photos often have larger dark background patches.
    Returns crop + (x0, y0, w, h) in source-image pixels."""
    h, w = gray.shape
    sw, sh = max(50, w // 6), max(50, h // 6)
    small = cv2.resize(gray, (sw, sh))
    small = cv2.GaussianBlur(small, (5, 5), 0)
    _, bin_ = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    bin_ = cv2.morphologyEx(bin_, cv2.MORPH_CLOSE, ker)
    bin_ = cv2.morphologyEx(bin_, cv2.MORPH_OPEN, ker)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bin_, 8)
    best, best_score = None, -1.0
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < 0.002 * small.size:
            continue
        cnts, _ = cv2.findContours((labels == i).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cnts, key=cv2.contourArea)
        perim = cv2.arcLength(c, True)
        circ = 4 * math.pi * area / (perim * perim) if perim > 0 else 0.0
        score = area * circ * circ            # circularity² favours the round target
        if score > best_score:
            best_score, best = score, i
    if best is None:
        return gray, (0, 0, w, h)
    sx, sy = w / sw, h / sh
    # Centroid + radius of the black-disc blob (in full-image px).
    m = cv2.moments((labels == best).astype(np.uint8))
    bcx = (m["m10"] / m["m00"]) * sx if m["m00"] > 0 else (stats[best, cv2.CC_STAT_LEFT] + stats[best, cv2.CC_STAT_WIDTH] / 2) * sx
    bcy = (m["m01"] / m["m00"]) * sy if m["m00"] > 0 else (stats[best, cv2.CC_STAT_TOP] + stats[best, cv2.CC_STAT_HEIGHT] / 2) * sy
    bd_r = 0.5 * max(stats[best, cv2.CC_STAT_WIDTH], stats[best, cv2.CC_STAT_HEIGHT]) * max(sx, sy)
    # Crop centred on the black disc, extending ~3.5× its radius to cover ring 1 + margin.
    half = int(3.5 * bd_r)
    x0 = int(max(0, bcx - half)); y0 = int(max(0, bcy - half))
    x1 = int(min(w, bcx + half)); y1 = int(min(h, bcy + half))
    return gray[y0:y1, x0:x1], (x0, y0, x1 - x0, y1 - y0)


# ---------------------------------------------------------------------------
# Black disc (= black/white boundary anchor) — ported/extended from v8
# ---------------------------------------------------------------------------
def detect_black_disc(gray: np.ndarray) -> dict | None:
    """Adaptive-threshold + circularity-filtered black scoring disc.

    Returns {contour, cx, cy, inscribed_r_px, ellipse, semi_a, semi_b, major_dir}.
    """
    h, w = gray.shape
    binv = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        blockSize=max(51, (max(h, w) // 16) | 1), C=5,
    )
    k_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binv = cv2.morphologyEx(binv, cv2.MORPH_OPEN, k_small)

    best, best_metric = None, -1.0
    for close_ks in (9, 17, 25, 35, 51):
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
            metric = area * (0.3 + 0.7 * circ)
            if metric > best_metric:
                best_metric, best = metric, c
        if best is not None and len(best) >= 5:
            break

    if best is None or len(best) < 5:
        return None

    mask = np.zeros(gray.shape, dtype=np.uint8)
    cv2.drawContours(mask, [best], -1, 255, -1)
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    inscribed_r = float(dist.max())
    m = cv2.moments(best)
    cx = m["m10"] / m["m00"] if m["m00"] > 0 else float(best[:, 0, 0].mean())
    cy = m["m01"] / m["m00"] if m["m00"] > 0 else float(best[:, 0, 1].mean())
    geo = ellipse_geometry(best)
    return {"contour": best, "cx": cx, "cy": cy, "inscribed_r_px": inscribed_r,
            "semi_a": geo["semi_a"], "semi_b": geo["semi_b"],
            "major_dir": geo["major_dir"], "anisotropy": geo["anisotropy"]}


def ellipse_geometry(contour: np.ndarray) -> dict:
    """Robust ellipse axes + major direction (convention-free via projection range)."""
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
    return {"cx": cx, "cy": cy, "semi_a": semi_a, "semi_b": semi_b,
            "major_dir": major_dir, "anisotropy": semi_a / max(semi_b, 1e-6)}


# ---------------------------------------------------------------------------
# Concentric rings — ported from v8
# ---------------------------------------------------------------------------
def _sobel_mag(gray: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
    gx = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    return ((mag / max(mag.max(), 1e-6)) * 255).astype(np.uint8)


def detect_concentric_circles(gray: np.ndarray) -> list[tuple[float, float, float]]:
    """Multi-band classic HoughCircles on Sobel magnitude (downscaled for speed),
    duplicate-clustered. NOTE: HOUGH_GRADIENT_ALT returns nothing on OpenCV 5
    here, so classic HOUGH_GRADIENT is used. Returns (x,y,r) in input px."""
    h, w = gray.shape
    S = 0.5
    small = cv2.resize(gray, (int(w * S), int(h * S)))
    short = min(h, w) * S
    mag = _sobel_mag(small)
    bands = [(0.03, 0.12, 30, 18), (0.10, 0.22, 40, 28), (0.18, 0.34, 50, 35), (0.30, 0.55, 50, 42)]
    found = []
    for lo, hi, p1, p2 in bands:
        cs = cv2.HoughCircles(mag, cv2.HOUGH_GRADIENT, dp=1.0,
                              minDist=max(5, int(0.4 * lo * short)),
                              param1=p1, param2=p2,
                              minRadius=int(lo * short), maxRadius=int(hi * short))
        if cs is not None:
            for c in cs[0]:
                found.append((float(c[0]) / S, float(c[1]) / S, float(c[2]) / S))
    # Cluster duplicates.
    found.sort(key=lambda c: -c[2])
    clustered = []
    for cx, cy, r in found:
        for i, (ccx, ccy, cr) in enumerate(clustered):
            if math.hypot(cx - ccx, cy - ccy) < 0.12 * r and abs(r - cr) / r < 0.10:
                clustered[i] = ((ccx + cx) / 2, (ccy + cy) / 2, (cr + r) / 2)
                break
        else:
            clustered.append((cx, cy, r))
    return clustered


def fit_concentric(circles: list[tuple[float, float, float]]) -> dict | None:
    """Find the common centre (vote) + arithmetic ring spacing from detected
    circles. Returns {cx, cy, radii (concentric, sorted)} or None."""
    if len(circles) < 2:
        return None
    # Vote for a common centre: each circle contributes its own centre; cluster.
    centers = [(c[0], c[1]) for c in circles]
    best = None
    for cx, cy in centers:
        n = sum(1 for (x, y, r) in circles if math.hypot(x - cx, y - cy) < 0.20 * r)
        if best is None or n > best[0]:
            best = (n, cx, cy)
    if best is None:
        return None
    _, cx, cy = best
    # Refine centre as the mean of concentric circles' centres.
    conc = [(x, y, r) for (x, y, r) in circles if math.hypot(x - cx, y - cy) < 0.20 * r]
    if not conc:
        return None
    cx = float(np.mean([x for x, _, _ in conc]))
    cy = float(np.mean([y for _, y, _ in conc]))
    radii = sorted(set(round(r, 1) for _, _, r in conc))
    return {"cx": cx, "cy": cy, "radii": radii}


# ---------------------------------------------------------------------------
# Two-anchor calibration via radial profile (robust to holes/noise)
# ---------------------------------------------------------------------------
def blackdisc_center(gray: np.ndarray) -> tuple[float, float, float]:
    """Bullseye estimate = centroid of the largest dark blob (the black scoring
    disc), with intra-disc holes filled by a closing kernel. Returns (cx, cy,
    anisotropy) where anisotropy comes from the blob's fitEllipse."""
    h, w = gray.shape
    g = cv2.GaussianBlur(gray, (0, 0), 3)
    b = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
                              max(51, (max(h, w) // 16) | 1), C=5)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, k)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(b, 8)
    if n <= 1:
        return w / 2, h / 2, 1.0
    areas = [int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n)]
    big = 1 + int(np.argmax(areas))
    cx, cy = float(cents[big][0]), float(cents[big][1])
    cnts, _ = cv2.findContours((labels == big).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    aniso = 1.0
    major_dir = np.array([1.0, 0.0])
    semi_a = semi_b = 0.0
    if cnts:
        geo = ellipse_geometry(max(cnts, key=cv2.contourArea))
        aniso = geo["anisotropy"]; major_dir = geo["major_dir"]
        semi_a, semi_b = geo["semi_a"], geo["semi_b"]
    return cx, cy, aniso, major_dir, semi_a, semi_b


def calibrate(gray_crop: np.ndarray) -> dict:
    """Radial-profile two-anchor calibration centred on the black-disc centroid.

    Anchors: black/white boundary (ring-7 outer = 3 steps from the 10-ring) and
    bullseye (10-ring outer = boundary − 3·s). The boundary is read off the
    angle-averaged radial intensity transition (refined to the nearest Sobel
    gradient peak); the ring spacing s is the value best aligning gradient peaks
    to boundary ± k·s. Robust to bullet-hole noise (angle averaging)."""
    h, w = gray_crop.shape
    cx, cy, aniso, major_dir, semi_a, semi_b = blackdisc_center(gray_crop)
    maxR = int(min(cx, cy, w - cx, h - cy) - 2)
    mag = _sobel_mag(gray_crop)
    g_pol = cv2.warpPolar(mag, (maxR, 720), (cx, cy), maxR, cv2.INTER_LINEAR | cv2.WARP_POLAR_LINEAR)
    i_pol = cv2.warpPolar(gray_crop, (maxR, 720), (cx, cy), maxR, cv2.INTER_LINEAR | cv2.WARP_POLAR_LINEAR)
    gp = cv2.GaussianBlur(g_pol.mean(axis=0).reshape(-1, 1), (1, 9), 0).ravel()
    ip = cv2.GaussianBlur(i_pol.mean(axis=0).reshape(-1, 1), (1, 21), 0).ravel()

    dip = np.gradient(ip)
    lo, hi = int(0.20 * maxR), int(0.80 * maxR)
    r_t = lo + int(np.argmax(dip[lo:hi]))
    win = max(8, int(0.08 * maxR))
    r_bw = max(0, r_t - win) + int(np.argmax(gp[max(0, r_t - win):r_t + win]))

    peaks = [r for r in range(6, maxR - 6)
             if gp[r] == gp[max(0, r - 6):r + 7].max() and gp[r] > gp.mean()]
    best_s, best_score = r_bw / 8.0, -1
    for s in np.arange(r_bw / 14, r_bw / 4, max(1.0, r_bw / 200)):
        sc = sum(1 for k in range(-4, 9)
                 if 4 < (r_bw + k * s) < maxR - 4
                 and min(abs(r_bw + k * s - p) for p in peaks) < max(2.0, 0.12 * s))
        if sc > best_score:
            best_score, best_s = sc, float(s)
    s_px = best_s
    r_bull = r_bw - 3 * s_px

    ok = r_bull > 0.05 * r_bw and s_px > 0
    return {
        "ok": ok, "shape": (h, w),
        "cx": cx, "cy": cy, "r_bw_px": float(r_bw), "r_bull_px": float(r_bull),
        "s_px": float(s_px), "anisotropy": aniso, "major_dir": major_dir,
        "semi_a": semi_a, "semi_b": semi_b, "peaks_aligned": best_score,
    }


def ring_radii_px(cal: dict) -> list[float]:
    """Ring-boundary radii in px, index 0 = ring 10 outer .. index 9 = ring 1 outer."""
    return [cal["r_bull_px"] + (10 - k) * cal["s_px"] for k in range(10, 0, -1)]


# ---------------------------------------------------------------------------
# Affine fronto-parallel warp (no in-plane rotation)
# ---------------------------------------------------------------------------
def warp_fronto_parallel(gray: np.ndarray, cal: dict):
    """Affine stretch of the black-disc minor axis to fronto-parallel (no in-plane
    rotation). Returns (warped, M2x2, out_center) where out_center is the bullseye
    location in the warped image."""
    h, w = gray.shape
    cx, cy = cal["cx"], cal["cy"]
    a, b = cal["semi_a"], cal["semi_b"]
    major = cal["major_dir"]
    minor = np.array([-major[1], major[0]])
    k = a / max(b, 1e-6)                                     # stretch along minor
    M2 = np.outer(major, major) + k * np.outer(minor, minor)  # det k > 0

    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], dtype=np.float64)
    wc = (corners - np.array([cx, cy])) @ M2.T
    mn = wc.min(0)
    out_w = int(math.ceil((wc - mn)[:, 0].max())) + 2
    out_h = int(math.ceil((wc - mn)[:, 1].max())) + 2
    t = np.array([out_w / 2, out_h / 2]) - M2 @ np.array([cx, cy])
    aff = np.float32([[M2[0, 0], M2[0, 1], t[0]],
                      [M2[1, 0], M2[1, 1], t[1]]])
    warped = cv2.warpAffine(gray, aff, (out_w, out_h), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=245)
    return warped, M2, (out_w / 2, out_h / 2)


# ---------------------------------------------------------------------------
# Debug runner (calibration only — hole detection added next)
# ---------------------------------------------------------------------------
def draw_rings_overlay(img: np.ndarray, cal: dict, warped: bool = False) -> np.ndarray:
    viz = img.copy()
    if not cal.get("ok"):
        return viz
    cx, cy = cal["cx"], cal["cy"]
    a = cal.get("semi_a", cal.get("r_bw_px", 1.0))
    b = cal.get("semi_b", a)
    angle = math.degrees(math.atan2(cal["major_dir"][1], cal["major_dir"][0])) if "major_dir" in cal else 0.0
    for idx, r in enumerate(ring_radii_px(cal)):      # idx0=ring10 ... idx9=ring1
        ring_val = 10 - idx
        axes = (int(r), int(r)) if warped else (int(r), int(r * (b / a)))
        col = (0, 255, 255) if ring_val == 10 else (0, 200, 0) if ring_val == 7 else (60, 200, 60)
        thick = 2 if ring_val in (1, 7, 10) else 1
        cv2.ellipse(viz, (int(cx), int(cy)), axes, int(angle), 0, 360, col, thick)
    cv2.circle(viz, (int(cx), int(cy)), 5, (0, 0, 255), -1)
    return viz


# ---------------------------------------------------------------------------
# Hole detection — multi-scale matched filter + Hessian/blobness verification
# ---------------------------------------------------------------------------
def _local_std(gray: np.ndarray, k: int) -> np.ndarray:
    f = gray.astype(np.float32)
    mu = cv2.boxFilter(f, -1, (k, k))
    sq = cv2.boxFilter(f * f, -1, (k, k))
    return np.sqrt(np.maximum(sq - mu * mu, 0))


def _hole_template(r: int, soft: int = 2) -> np.ndarray:
    """Synthetic hole template: dark disk + bright annulus [r, 2r].

    Zero-mean, unit L2 norm so correlation = dot product. Softened at edges by
    a small Gaussian to be robust to paper-tear jitter around real bullet holes.
    """
    sz = 3 * r + 2 * soft + 1
    c = sz // 2
    yy, xx = np.mgrid[0:sz, 0:sz]
    d = np.sqrt((yy - c) ** 2 + (xx - c) ** 2)
    t = np.zeros((sz, sz), dtype=np.float32)
    t[d <= r] = -1.0
    t[(d > r) & (d <= 2 * r)] = 1.0
    t = cv2.GaussianBlur(t, (2 * soft + 1, 2 * soft + 1), 0)
    t -= t.mean()
    n = float(np.linalg.norm(t))
    return t / n if n > 1e-6 else t


def _matched_filter(crop: np.ndarray, r: int) -> np.ndarray:
    """Response map = correlation of crop with the hole template at radius r.

    Normalized by sqrt(template_area) so the response is roughly scale-invariant
    (the unit-norm template dot-product scales with sqrt(area) for matching
    signals — without normalization, larger templates always dominate).
    """
    t = _hole_template(r)
    sz = t.shape[0]
    norm = math.sqrt(float(sz * sz))
    return cv2.filter2D(crop.astype(np.float32), cv2.CV_32F, t) / norm


def _radial_profile(crop: np.ndarray, cx: float, cy: float, r: float,
                    n_bins: int = 6) -> list[float] | None:
    """Mean intensity in concentric annular bins (in units of r) around (cx, cy).

    Bins: 0=[0,1)r, 1=[1,2)r, ..., 5=[5+,inf). For a bullet hole, profile[0]
    (the centre) should be the darkest bin, profile[2] approximates paper.
    """
    h, w = crop.shape
    pad = int(3 * r) + 2
    x0, x1 = max(0, int(cx - pad)), min(w, int(cx + pad))
    y0, y1 = max(0, int(cy - pad)), min(h, int(cy + pad))
    patch = crop[y0:y1, x0:x1].astype(np.float32)
    if patch.size == 0:
        return None
    H, W = patch.shape
    yy, xx = np.mgrid[0:H, 0:W]
    dist = np.sqrt((yy - (cy - y0)) ** 2 + (xx - (cx - x0)) ** 2) / max(r, 1)
    bins = np.minimum(dist.astype(int), n_bins - 1)
    return [float(patch[bins == b].mean()) if (bins == b).any() else 0.0
            for b in range(n_bins)]


def _hessian_blobness(resp_map: np.ndarray, x: float, y: float) -> float:
    """Roundness of the response peak = min(|λ|)/max(|λ|) of the local Hessian.

    For a true circular blob (hole): both eigenvalues are large and negative
    -> ratio close to 1.0. For a ridge (ring line or digit stroke): one
    eigenvalue near zero -> ratio close to 0.0.
    """
    ix, iy = int(round(x)), int(round(y))
    if iy < 1 or ix < 1 or iy >= resp_map.shape[0] - 1 or ix >= resp_map.shape[1] - 1:
        return 0.0
    dxx = resp_map[iy - 1, ix] - 2 * resp_map[iy, ix] + resp_map[iy + 1, ix]
    dyy = resp_map[iy, ix - 1] - 2 * resp_map[iy, ix] + resp_map[iy, ix + 1]
    dxy = (resp_map[iy + 1, ix + 1] - resp_map[iy + 1, ix - 1]
           - resp_map[iy - 1, ix + 1] + resp_map[iy - 1, ix - 1]) / 4.0
    H = np.array([[dxx, dxy], [dxy, dyy]])
    ev = np.linalg.eigvalsh(H)
    absmax = max(abs(ev[0]), abs(ev[1]))
    if absmax < 1e-6:
        return 0.0
    return float(min(abs(ev[0]), abs(ev[1])) / absmax)


def _auto_pick_radius(stack: np.ndarray, radii: list[int],
                      target_mask: np.ndarray) -> tuple[int, float]:
    """Pick the radius whose response map has the strongest signal in the
    target area (highest 99.5-percentile of positive responses inside the
    target mask). Returns (best_radius_idx, score_at_best)."""
    best_i, best_score = 0, -1.0
    for i in range(len(radii)):
        r_map = stack[i]
        in_target = r_map[target_mask > 0]
        pos = in_target[in_target > 0]
        if pos.size < 10:
            continue
        score = float(np.percentile(pos, 99.5))
        if score > best_score:
            best_score = score
            best_i = i
    return best_i, best_score


def _auto_pick_radius_strongest(stack: np.ndarray, radii: list[int],
                                target_mask: np.ndarray) -> int:
    """Pick the radius of the strongest scale-space peak inside the target.

    The strongest response in the target area is almost always a real bullet
    hole (printed ring numbers and ring lines are weaker). Its scale = the
    per-image bullet scale.
    """
    best_i, best_v = 0, -1.0
    for i in range(len(radii)):
        r_map = stack[i]
        in_target = r_map[target_mask > 0]
        if in_target.size == 0:
            continue
        v = float(in_target.max())
        if v > best_v:
            best_v = v
            best_i = i
    return best_i


def detect_holes(gray_crop: np.ndarray, cal: dict,
                 debug: bool = False) -> list[tuple[float, float, float]]:
    """Detect bullet holes via single-scale matched filter + verification.

    Pipeline (calibrated on image 46, generalised across the train set):
      1. **Multi-scale matched filter** at radii spanning 0.05s-0.36s. Each
         response map is normalized by sqrt(template_area) so responses are
         roughly scale-invariant. The strongest response in the target area
         picks the per-image bullet scale (a real hole dominates over ring
         lines / printed digits).
      2. **Spatial NMS** at the picked scale, threshold = 0.30 * max_at_scale.
      3. **Verification** per candidate (scale-invariant):
           * Hessian blobness > 0.30 (rejects ring lines / digit strokes —
             ridges in space, not 2D peaks)
           * radial profile dip_ratio = (prof[2]-prof[0]) / prof[2] > 0.20
             AND absolute dip > 20 (centre clearly darker than paper)
           * prof[0] below p60 of the BLACK DISC intensity (rejects bright
             printed ring numbers; restricting to the black disc gives a much
             tighter ceiling than the full target area which includes bright
             paper)

    Returns list of (x, y, radius) in crop px.
    """
    if not cal.get("ok"):
        return []
    s = cal["s_px"]
    cx, cy = cal["cx"], cal["cy"]
    r_target = cal["r_bull_px"] + 9 * s               # ring-1 outer (target extent)
    r_bw = cal["r_bw_px"]

    # ---- Step 1: multi-scale matched filter, auto-pick bullet scale ----
    ratios = [0.05, 0.08, 0.11, 0.14, 0.18, 0.22, 0.28, 0.36]
    radii = sorted({max(3, int(round(f * s))) for f in ratios})
    H, W = gray_crop.shape
    stack = np.zeros((len(radii), H, W), dtype=np.float32)
    for i, r in enumerate(radii):
        stack[i] = _matched_filter(gray_crop, r)

    # Target mask + black-disc mask
    yy, xx = np.mgrid[0:H, 0:W]
    a = max(cal.get("semi_a", r_bw), 1.0)
    b = max(cal.get("semi_b", a), 1.0)
    major = cal.get("major_dir", np.array([1.0, 0.0]))
    dx = xx - cx
    dy = yy - cy
    proj_maj = dx * major[0] + dy * major[1]
    proj_min = dx * (-major[1]) + dy * major[0]
    dist_metric = np.sqrt((proj_maj / a) ** 2 + (proj_min / b) ** 2) * r_bw
    target_mask = (dist_metric <= r_target).astype(np.uint8)
    black_disc_mask = (dist_metric <= r_bw).astype(np.uint8)

    # Pick top-K scales = the K scales with the strongest max response in
    # the target area. Using K=2 recovers holes whose blobness is low at the
    # single best scale but high at a nearby scale (e.g. image 46 GT#0/GT#2
    # are detectable at r=18 but not at the strongest r=14).
    per_scale_max = [(i, float(stack[i][target_mask > 0].max()))
                     for i in range(len(radii)) if target_mask.sum()]
    if not per_scale_max:
        return []
    per_scale_max.sort(key=lambda x: -x[1])
    top_k = [radii[i] for i, _ in per_scale_max[:2]]
    if debug:
        print(f"  radii: {radii}")
        print(f"  per-scale max in target: {[(radii[i], round(v)) for i, v in per_scale_max]}")
        print(f"  top-K scales: {top_k}")

    # ---- prof0 ceiling (p60 of black disc) ----
    # prof0 is the mean intensity inside a disk of radius r_pick around the
    # candidate. At larger r_pick the bin includes more surrounding paper, so
    # prof0 trends higher for the same hole. We compensate by adding a
    # per-scale offset proportional to r_pick (calibrated: ~0.5 px of paper
    # brightness per px of radius).
    if black_disc_mask.sum() > 100:
        base_ceil = float(np.percentile(gray_crop[black_disc_mask > 0], 60))
    else:
        base_ceil = float(np.percentile(gray_crop[target_mask > 0], 30))
    if debug:
        print(f"  base prof[0] ceiling (p60 of black disc): {base_ceil:.0f}")

    kept: list[tuple[float, float, float]] = []
    used: list[tuple[float, float]] = []
    for r_pick in top_k:
        i_pick = radii.index(r_pick)
        resp = stack[i_pick]
        max_resp = float(resp[target_mask > 0].max())

        # ---- Step 2: spatial NMS at this scale ----
        resp_pos = np.maximum(resp, 0)
        kr = max(5, int(1.0 * r_pick))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * kr + 1, 2 * kr + 1))
        dil = cv2.dilate(resp_pos, kernel)
        thr_resp = 0.30 * max_resp
        peaks = (resp_pos == dil) & (resp_pos > thr_resp) & (target_mask > 0)
        ys, xs = np.where(peaks)
        cands = [(float(x), float(y), float(resp[y, x])) for x, y in zip(xs, ys)]
        cands.sort(key=lambda c: -c[2])
        if debug:
            print(f"  r={r_pick}: NMS peaks={len(cands)}")

        # ---- Step 3: per-candidate verification ----
        # prof0 ceiling compensates for larger-r bins including more paper:
        # the bin mean trends up by ~1 intensity unit per px of radius.
        prof0_ceil = base_ceil + 1.5 * r_pick
        for x, y, v in cands:
            if any(math.hypot(x - ux, y - uy) < 0.6 * s for ux, uy in used):
                continue
            blob = _hessian_blobness(resp, x, y)
            prof = _radial_profile(gray_crop, x, y, float(r_pick))
            if prof is None:
                continue
            prof0, prof2 = prof[0], prof[2]
            dip = prof2 - prof0
            dip_ratio = dip / max(prof2, 1.0)
            if blob < 0.30:
                continue
            if dip <= 20 or dip_ratio < 0.20:
                continue
            if prof0 > prof0_ceil:
                continue
            kept.append((x, y, float(r_pick)))
            used.append((x, y))
            if debug:
                print(f"    KEEP ({x:.0f},{y:.0f}) r={r_pick} resp={v:.1f} blob={blob:.2f} "
                      f"dip={dip:.0f} dip_r={dip_ratio:.2f} prof0={prof0:.0f}")
    return kept


def score_holes(holes: list[tuple[float, float, float]], cal: dict) -> list[int]:
    """ISSF line-break rule: score = 10 - ceil((dist(bull,hole) - r_hole - r_bull)/s),
    clamped to [0, 10]. Uses the *detected* hole radius (user direction)."""
    s, r_bull = cal["s_px"], cal["r_bull_px"]
    cx, cy = cal["cx"], cal["cy"]
    scores = []
    for x, y, r in holes:
        d = math.hypot(x - cx, y - cy) - r           # line-break: subtract hole radius
        steps = int(math.ceil((d - r_bull) / s)) if d > r_bull else 0
        scores.append(max(0, min(10, 10 - steps)))
    return scores


def deliverable(gray_crop: np.ndarray, cal: dict, holes, scores) -> np.ndarray:
    """Normalised overlay: extrapolated ring geometry + magenta detected holes,
    drawn on the (mild-anisotropy) crop. Rings are ellipses (anisotropic metric);
    rings outside the photo are drawn dashed to show extrapolation."""
    viz = cv2.cvtColor(gray_crop, cv2.COLOR_GRAY2BGR)
    if not cal.get("ok"):
        return viz
    cx, cy = cal["cx"], cal["cy"]
    a, b = cal.get("semi_a", cal["r_bw_px"]), cal.get("semi_b", cal["r_bw_px"])
    ang = math.degrees(math.atan2(cal["major_dir"][1], cal["major_dir"][0]))
    H, W = gray_crop.shape
    for idx, r in enumerate(ring_radii_px(cal)):       # idx0=ring10 .. idx9=ring1
        ring_val = 10 - idx
        axes = (int(r), int(r * b / a))
        col = (0, 255, 255) if ring_val == 10 else (0, 200, 0) if ring_val == 7 else (60, 200, 60)
        cv2.ellipse(viz, (int(cx), int(cy)), axes, int(ang), 0, 360, col, 1)
    cv2.circle(viz, (int(cx), int(cy)), 5, (0, 0, 255), -1)
    for (x, y, r), sc in zip(holes, scores):
        cv2.circle(viz, (int(x), int(y)), max(3, int(r)), (255, 0, 255), -1)   # magenta
        cv2.putText(viz, str(sc), (int(x) + int(r) + 2, int(y) + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2)
    return viz


def run_one(img_id: int, debug: bool = True, out_dir: str = "resources/train/intermediate_blob") -> dict:
    import json
    bgr = load_bgr(Path("resources/train") / f"{img_id}.jpg")
    gray = to_gray(bgr)
    crop, bbox = crop_to_target(gray)
    cal = calibrate(crop)
    holes = detect_holes(crop, cal)
    scores = score_holes(holes, cal)
    result = {
        "image": f"{img_id}.jpg", "crop_bbox": list(bbox),
        "calibration": {k: cal[k] for k in ("ok", "cx", "cy", "r_bw_px", "r_bull_px", "s_px", "anisotropy")},
        "bullet_radius_px_est": float(np.median([r for _, _, r in holes])) if holes else None,
        "holes": [{"x": round(x, 1), "y": round(y, 1), "r_px": round(r, 1)} for x, y, r in holes],
        "scores": scores, "total": int(sum(scores)), "count": len(scores),
    }
    if debug:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        crop_bgr = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        cv2.imwrite(str(out / f"{img_id:02d}_crop.png"), crop_bgr)
        if cal.get("ok"):
            cv2.imwrite(str(out / f"{img_id:02d}_rings.png"), draw_rings_overlay(crop_bgr, cal))
            cv2.imwrite(str(out / f"{img_id:02d}_deliverable.png"), deliverable(crop, cal, holes, scores))
        (out / f"{img_id:02d}_result.json").write_text(json.dumps(result, indent=2))
        print(f"img {img_id}: ok={cal.get('ok')} s={cal.get('s_px',0):.0f} "
              f"holes={len(holes)} total={result['total']}")
    return result


if __name__ == "__main__":
    import sys
    import numpy as np  # noqa
    out_dir = "resources/train/intermediate_blob"
    args = sys.argv[1:]
    # Optional --out=DIR flag selects the intermediate output directory.
    ids = []
    for a in args:
        if a.startswith("--out="):
            out_dir = a.split("=", 1)[1]
        else:
            ids.append(int(a))
    for i in (ids or [1, 4, 6, 10, 12, 19, 21, 29, 31, 46]):
        run_one(i, out_dir=out_dir)
