"""Iteredge localization — robustly find the target's bullseye + initial crop.

Two-stage approach:
  Stage A (find candidate center):
    - Try blob_detect.crop_to_target + bd_calibrate first (this works for the
      well-behaved images: 12, 46, 21). If the calibration succeeds with
      decent peaks_aligned AND the bullseye blob isn't anomalously large
      (which is what fails for image 29 — the "black disc" detected is
      actually a logo or shading blob), accept and move on.
    - Otherwise fall back to a ring-score search: scan all candidate blob
      centres and pick the one whose radial Sobel profile best fits an
      arithmetic progression of ring spacings. This is what finds the actual
      target in image 29 (the rings reject the logo).

  Stage B (crop):
    - Center the crop on the chosen bullseye.
    - Extend by ~1-ring margin beyond the outer 1-ring (r_bull + 9·s) so all
      holes stay in frame even when the target is perspective-skewed (this is
      what saves image 21, where the affine warp would otherwise push holes
      out of frame).

Returns (crop_gray, bbox, init_dict).
init_dict carries the bullseye init + ring spacing init used by the optimizer.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from cv.blob_detect import (
    _sobel_mag,
    blackdisc_center,
    calibrate as bd_calibrate,
    crop_to_target as bd_crop_to_target,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _blob_centroids(binary: np.ndarray, min_area_frac: float = 0.002) -> list[tuple[float, float, float]]:
    n, labels, stats, cents = cv2.connectedComponentsWithStats(binary, 8)
    out = []
    h, w = binary.shape
    min_area = min_area_frac * h * w
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        out.append((float(cents[i][0]), float(cents[i][1]), float(area)))
    out.sort(key=lambda t: -t[2])
    return out


def _quick_ring_score(gray: np.ndarray, cx: float, cy: float, max_r: int | None = None) -> dict:
    """Quick radial-profile ring-fit score around (cx, cy).

    Tries all (r_bw, r_outer) pairs of Sobel peaks; counts how many other
    peaks fit an arithmetic progression. Returns the best (s, r_bw) + score.
    """
    h, w = gray.shape
    if max_r is None:
        max_r = int(min(cx, cy, w - cx, h - cy) - 2)
    max_r = max(20, min(max_r, int(min(h, w) / 2) - 2))
    mag = _sobel_mag(gray)
    pol = cv2.warpPolar(mag, (max_r, 360), (cx, cy), max_r,
                        cv2.INTER_LINEAR | cv2.WARP_POLAR_LINEAR)
    gp = cv2.GaussianBlur(pol.mean(axis=0).reshape(-1, 1), (1, 9), 0).ravel()

    peaks = [
        r for r in range(6, max_r - 6)
        if gp[r] == gp[max(0, r - 6):r + 7].max() and gp[r] > gp.mean()
    ]
    if len(peaks) < 3:
        return {"s_px": 0.0, "r_bw_px": 0.0, "score": 0, "n_peaks": len(peaks), "peaks": peaks}

    best = {"s_px": 0.0, "r_bw_px": 0.0, "score": 0, "n_peaks": len(peaks), "peaks": peaks}
    for i, r_bw in enumerate(peaks[:-1]):
        for r_outer in peaks[i + 1:]:
            s = (r_outer - r_bw) / 9.0
            if s < 3 or s > max_r / 4:
                continue
            sc = 0
            for k in range(0, 11):
                r_k = r_outer - k * s
                if r_k < 4 or r_k >= max_r - 4:
                    continue
                if min(abs(r_k - p) for p in peaks) < max(2.0, 0.12 * s):
                    sc += 1
            if sc > best["score"]:
                best = {"s_px": float(s), "r_bw_px": float(r_bw),
                        "score": int(sc), "n_peaks": len(peaks), "peaks": peaks}
    return best


# ---------------------------------------------------------------------------
# Candidate-based search (fallback for image 29 where the default lock fails)
# ---------------------------------------------------------------------------
def _candidate_centres(gray: np.ndarray, k_shortlist: int = 12) -> list[tuple[float, float, float]]:
    """Return [(cx, cy, area), ...] candidate blob centres ranked by
    circularity² × area (best first)."""
    h, w = gray.shape
    sw, sh = max(64, w // 6), max(64, h // 6)
    small = cv2.resize(gray, (sw, sh))
    small = cv2.GaussianBlur(small, (5, 5), 0)
    _, bin_ = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    bin_ = cv2.morphologyEx(bin_, cv2.MORPH_CLOSE, ker)
    bin_ = cv2.morphologyEx(bin_, cv2.MORPH_OPEN, ker)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(bin_, 8)
    sx, sy = w / sw, h / sh
    out = []
    for i in range(1, n):
        area_s = int(stats[i, cv2.CC_STAT_AREA])
        if area_s < 0.001 * small.size:
            continue
        cnts, _ = cv2.findContours((labels == i).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        perim = cv2.arcLength(c, True)
        if perim < 1:
            continue
        circ = 4 * math.pi * area_s / (perim * perim)
        if circ < 0.15:
            continue
        m = cv2.moments((labels == i).astype(np.uint8))
        if m["m00"] == 0:
            continue
        bcx = (m["m10"] / m["m00"]) * sx
        bcy = (m["m01"] / m["m00"]) * sy
        bd_r = 0.5 * max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]) * max(sx, sy)
        out.append((float(bcx), float(bcy), float(bd_r), float(circ), float(area_s)))
    out.sort(key=lambda t: -(t[3] ** 2 * t[4]))
    return out[:k_shortlist]


def find_bullseye_by_ring_search(gray: np.ndarray) -> dict | None:
    """Scan candidates and pick the one whose radial profile best fits an
    arithmetic ring pattern. Used when the default localizer locks onto a
    non-target blob (e.g. image 29's logo).
    """
    candidates = _candidate_centres(gray)
    if not candidates:
        return None

    scored = []
    for cx, cy, bd_r, circ, _ in candidates:
        h, w = gray.shape
        max_r = int(min(cx, cy, w - cx, h - cy) - 2)
        if max_r < 5 * bd_r:
            # The disc shouldn't be more than ~20% of the search area.
            continue
        score = _quick_ring_score(gray, cx, cy, max_r=max_r)
        if score["score"] >= 3:
            scored.append({
                "cx": cx, "cy": cy, "r_disc_px": bd_r, "circ": circ,
                "s_px": score["s_px"], "r_bw_px": score["r_bw_px"],
                "score": score["score"], "n_peaks": score["n_peaks"],
            })

    if not scored:
        return None

    # Pick highest ring score; tie-break by circularity.
    scored.sort(key=lambda c: (c["score"], c["circ"]), reverse=True)
    return scored[0]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def crop_to_target(gray: np.ndarray, expand_rings: float = 1.20) -> tuple[np.ndarray, tuple[int, int, int, int], dict]:
    """Locate the target + crop with margin.

    Strategy:
      1. Try blob_detect.crop_to_target + bd_calibrate. Accept if calibration
         succeeds AND the black-disc isn't anomalously large AND peaks_aligned
         >= 4 (decent ring fit).
      2. Otherwise, run a candidate-based ring-pattern search to find the
         actual target.

    The crop extends ~expand_rings × r_ring1 from the bullseye so the
    subsequent warp doesn't push holes out of frame.

    Returns (crop, bbox, init).
    """
    h, w = gray.shape

    # ----- Stage A: try the default path -----
    crop_default, bbox_default = bd_crop_to_target(gray)
    cal_default = bd_calibrate(crop_default)

    accept_default = False
    if cal_default.get("ok"):
        peaks = cal_default.get("peaks_aligned", 0)
        s = float(cal_default.get("s_px", 0))
        r_bw = float(cal_default.get("r_bw_px", 0))
        semi_a = float(cal_default.get("semi_a", 0))
        semi_b = float(cal_default.get("semi_b", semi_a))
        crop_h, crop_w = crop_default.shape
        max_dim = max(crop_w, crop_h)

        # Accept if ring fit is strong AND the spacing is plausible.
        # peaks_aligned >= 5 means at least 5 ring edges fit an arithmetic
        # progression — this is a strong concentric-structure signal.
        # The ellipse fit on the black disc can be wrong (e.g. image 29
        # picks up surrounding dark stuff → huge semi_a), so we DON'T gate
        # on semi_a being small; we just clamp it later when needed.
        if peaks >= 5 and s > 0 and r_bw > 0 and r_bw < 0.5 * max_dim:
            accept_default = True

    if accept_default:
        cx_src = float(cal_default["cx"]) + bbox_default[0]
        cy_src = float(cal_default["cy"]) + bbox_default[1]
        # Preserve the ellipse anisotropy ratio + major_dir — these are the
        # only things the affine warp init actually uses. If the absolute
        # semi-axis values are clearly wrong (e.g. fitting to the outer ring-1
        # instead of the black disc, or to surrounding dark stuff), rescale
        # them to be near r_bw but keep the ratio.
        semi_a_raw = float(cal_default.get("semi_a", 0))
        semi_b_raw = float(cal_default.get("semi_b", semi_a_raw))
        r_bw_default = float(cal_default["r_bw_px"])
        if semi_a_raw <= 0 or semi_b_raw <= 0:
            semi_a, semi_b = r_bw_default, r_bw_default
            major_dir = np.array([1.0, 0.0])
            aniso = 1.0
        else:
            aniso = semi_a_raw / max(semi_b_raw, 1e-6)
            major_dir = cal_default.get("major_dir", np.array([1.0, 0.0]))
            # Rescale preserving aniso: semi_b = r_bw, semi_a = r_bw * aniso.
            # Cap aniso at 3.0 to reject extreme outliers.
            aniso = float(min(max(aniso, 1.0), 3.0))
            semi_b = r_bw_default
            semi_a = r_bw_default * aniso
        init = {
            "cx_src": cx_src, "cy_src": cy_src,
            "cx_crop": float(cal_default["cx"]),
            "cy_crop": float(cal_default["cy"]),
            "r_disc_px": float(semi_a),
            "s_px_init": float(cal_default["s_px"]),
            "r_bw_px_init": float(cal_default["r_bw_px"]),
            "score": int(cal_default.get("peaks_aligned", 0)),
            "source": "blob_detect_default",
            "anisotropy_init": aniso,
            "major_dir_init": major_dir,
            "semi_a_init": semi_a,
            "semi_b_init": semi_b,
        }
        # Recrop with our wider margin. Don't recalibrate — we trust the
        # default-path calibration. Just translate.
        return _recrop_with_margin(gray, init, expand_rings, recalibrate=False)

    # ----- Stage B: ring-pattern search -----
    cand = find_bullseye_by_ring_search(gray)
    if cand is None:
        # Last resort: take the image centre.
        cand = {
            "cx": w / 2.0, "cy": h / 2.0, "r_disc_px": float(min(w, h) / 8),
            "s_px": 0.0, "r_bw_px": 0.0, "score": 0, "circ": 0.0,
        }

    init = {
        "cx_src": float(cand["cx"]), "cy_src": float(cand["cy"]),
        "cx_crop": float(cand["cx"]),
        "cy_crop": float(cand["cy"]),
        "r_disc_px": float(cand["r_disc_px"]),
        "s_px_init": float(cand.get("s_px", 0)),
        "r_bw_px_init": float(cand.get("r_bw_px", 0)),
        "score": int(cand.get("score", 0)),
        "source": "ring_search",
    }
    # Ring-search path: recalibrate on the new crop to refine.
    return _recrop_with_margin(gray, init, expand_rings, recalibrate=True)


def _recrop_with_margin(gray: np.ndarray, init: dict, expand_rings: float,
                         recalibrate: bool = True) -> tuple[np.ndarray, tuple[int, int, int, int], dict]:
    """Crop a window around init['cx_src','cy_src'] sized to fit ~expand_rings
    × ring1_outer + margin.

    If `recalibrate` is True, run bd_calibrate on the new crop. This is
    appropriate when we got `init` from a less-trustworthy source (e.g. ring
    search). When `init` came from a calibration we trust (e.g. the default
    blob_detect path that already worked), set recalibrate=False to preserve
    the trusted calibration — just translate the coords.
    """
    h, w = gray.shape
    cx_src = init["cx_src"]
    cy_src = init["cy_src"]

    s = init.get("s_px_init", 0) or 0
    r_bw = init.get("r_bw_px_init", 0) or 0
    if s > 0 and r_bw > 0:
        r_ring1 = r_bw + 9.0 * s
    else:
        r_ring1 = 9.0 * max(init.get("r_disc_px", 1.0), 1.0)

    half = int(math.ceil(expand_rings * r_ring1 + max(r_ring1 * 0.10, 20.0)))
    half = max(half, int(4.0 * max(init.get("r_disc_px", 1.0), 1.0)))
    half = min(half, max(w, h))

    x0 = int(max(0, cx_src - half))
    y0 = int(max(0, cy_src - half))
    x1 = int(min(w, cx_src + half))
    y1 = int(min(h, cy_src + half))

    crop = gray[y0:y1, x0:x1]
    bbox = (x0, y0, x1 - x0, y1 - y0)

    if recalibrate:
        recut_cal = bd_calibrate(crop)
        if recut_cal.get("ok") and recut_cal.get("peaks_aligned", 0) >= 3:
            # Sanity: the new bullseye should be close to the predicted one
            # (within ~1 ring1 radius). If it jumped to a different target
            # (multiple targets in a tall image like 12.jpg), reject.
            new_cx_src = float(recut_cal["cx"]) + x0
            new_cy_src = float(recut_cal["cy"]) + y0
            if math.hypot(new_cx_src - cx_src, new_cy_src - cy_src) < 0.5 * r_ring1:
                init = {
                    **init,
                    "cx_src": new_cx_src, "cy_src": new_cy_src,
                    "cx_crop": float(recut_cal["cx"]),
                    "cy_crop": float(recut_cal["cy"]),
                    "s_px_init": float(recut_cal["s_px"]),
                    "r_bw_px_init": float(recut_cal["r_bw_px"]),
                    "score": int(recut_cal.get("peaks_aligned", 0)),
                    "anisotropy_init": float(recut_cal.get("anisotropy", 1.0)),
                }
            else:
                recalibrate = False  # fall through to translate

    if not recalibrate:
        # Just translate the existing calibration into the new crop frame.
        init = {
            **init,
            "cx_crop": cx_src - x0,
            "cy_crop": cy_src - y0,
        }

    init["bbox"] = bbox
    return crop, bbox, init
