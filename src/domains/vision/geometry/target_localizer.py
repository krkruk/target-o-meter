"""Stage 2 localize — the multiring black-disc-contrast detector that rejects
printed logos (the img-29 fix).

Ported verbatim from ``cv/approaches/multiring/localize.py`` (422 LOC at
commit 76f6fc4). The math is lifted as-is into class methods and module
private helpers; only the structure (free functions → ``TargetLocalizer``
class + helpers) changes.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from src.domains.vision.geometry.image_grayscaler import _sobel_mag


def _blob_centroids(
    binary: np.ndarray, min_area_frac: float = 0.002,
) -> list[tuple[float, float, float]]:
    """Dark-blob centroids above a minimum area, sorted by area desc.
    Ported verbatim from cv/approaches/multiring/localize.py:44-56."""
    n, labels, stats, cents = cv2.connectedComponentsWithStats(binary, 8)
    h, w = binary.shape
    min_area = min_area_frac * h * w
    out: list[tuple[float, float, float]] = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        out.append((float(cents[i][0]), float(cents[i][1]), float(area)))
    out.sort(key=lambda t: -t[2])
    return out


def _radial_ring_score(
    mag: np.ndarray, cx: float, cy: float, max_r: int,
) -> dict:
    """Score how well an arithmetic ring series explains the radial Sobel
    peaks around (cx, cy) in the input magnitude map.

    Ported verbatim from cv/approaches/multiring/localize.py:59-108.
    """
    max_r = max(20, min(max_r, int(min(mag.shape) / 2) - 2))
    pol = cv2.warpPolar(mag, (max_r, 360), (cx, cy), max_r,
                        cv2.INTER_LINEAR | cv2.WARP_POLAR_LINEAR)
    gp = cv2.GaussianBlur(pol.mean(axis=0).reshape(-1, 1), (1, 9), 0).ravel()

    peaks = [
        r for r in range(4, max_r - 4)
        if gp[r] == gp[max(0, r - 5):r + 6].max() and gp[r] > gp.mean()
    ]
    if len(peaks) < 2:
        return {"s_px": 0.0, "r_bw_px": 0.0, "r_bull_px": 0.0,
                "score": 0, "n_peaks": len(peaks), "peaks": peaks}

    best = {"s_px": 0.0, "r_bw_px": 0.0, "r_bull_px": 0.0,
            "score": 0, "n_peaks": len(peaks), "peaks": peaks}
    # Try every pair (r_bw, r_ring1) where r_ring1 = r_bw + 6·s.
    for i, r_bw in enumerate(peaks[:-1]):
        for r_outer in peaks[i + 1:]:
            s = (r_outer - r_bw) / 6.0       # 6 ring steps from ring 7 outer to ring 1 outer
            if s < 2 or s > max_r / 4:
                continue
            sc = 0
            for k in range(0, 11):
                r_k = r_outer - k * s
                if r_k < 3 or r_k >= max_r - 3:
                    continue
                if min(abs(r_k - p) for p in peaks) < max(1.5, 0.15 * s):
                    sc += 1
            if sc > best["score"]:
                best = {
                    "s_px": float(s),
                    "r_bw_px": float(r_bw),
                    "r_bull_px": float(r_bw - 3 * s),
                    "score": int(sc),
                    "n_peaks": len(peaks),
                    "peaks": peaks,
                }
    return best


def _black_disc_density(
    gray_small: np.ndarray, cx: float, cy: float, r: float,
) -> float:
    """Black-disc contrast: ratio of mean darkness in inner 50% disc vs outer
    annulus. Real ISSF targets have a high-contrast black centre; logos have
    uniform darkness.

    Ported verbatim from cv/approaches/multiring/localize.py:111-137.
    """
    h, w = gray_small.shape
    if r < 5:
        return 0.0
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.hypot(xx - cx, yy - cy)
    inner = (d <= 0.5 * r)
    outer = (d > 0.5 * r) & (d <= r)
    if inner.sum() < 10 or outer.sum() < 10:
        return 0.0
    inner_mean = float(gray_small[inner].mean())
    outer_mean = float(gray_small[outer].mean())
    if outer_mean < 1:
        return 0.0
    return (outer_mean - inner_mean) / outer_mean


def _find_concentric_circles_cluster(
    mag_small: np.ndarray, gray_small: np.ndarray,
) -> list[dict]:
    """Find clusters of concentric circles via multi-band HoughCircles.

    Ported verbatim from cv/approaches/multiring/localize.py:143-242.
    """
    h, w = mag_small.shape
    short = min(h, w)
    bands = [
        (0.04, 0.10, 50, 30),
        (0.08, 0.16, 60, 36),
        (0.14, 0.24, 70, 42),
        (0.22, 0.36, 80, 50),
        (0.32, 0.48, 90, 55),
    ]
    found: list[tuple[float, float, float]] = []
    for lo, hi, p1, p2 in bands:
        min_r = max(3, int(lo * short))
        max_r = max(min_r + 2, int(hi * short))
        circles = cv2.HoughCircles(
            mag_small, cv2.HOUGH_GRADIENT, dp=1.0,
            minDist=max(5, int(0.5 * min_r)),
            param1=p1, param2=p2,
            minRadius=min_r, maxRadius=max_r,
        )
        if circles is not None:
            for c in circles[0]:
                found.append((float(c[0]), float(c[1]), float(c[2])))
    if not found:
        return []

    # Greedy clustering by center proximity (relative to radius).
    found.sort(key=lambda c: -c[2])        # largest first
    clusters: list[dict] = []
    for cx, cy, r in found:
        placed = False
        for cl in clusters:
            d = math.hypot(cx - cl["cx"], cy - cl["cy"])
            if d <= 0.30 * max(r, cl["max_r"]):
                n = cl["n"]
                cl["cx"] = (cl["cx"] * n + cx) / (n + 1)
                cl["cy"] = (cl["cy"] * n + cy) / (n + 1)
                cl["radii"].append(r)
                cl["max_r"] = max(cl["max_r"], r)
                cl["n"] += 1
                cl["_centers"].append((cx, cy))
                placed = True
                break
        if not placed:
            clusters.append({
                "cx": cx, "cy": cy, "n": 1, "max_r": r,
                "radii": [r],
                "_centers": [(cx, cy)],
            })

    # Score each cluster.
    results = []
    for cl in clusters:
        if cl["n"] < 2:
            continue
        centers = np.array(cl["_centers"])
        radii = np.array(cl["radii"])
        center_std = float(np.sqrt(centers.var(axis=0).sum()))
        mean_r = float(radii.mean())
        concentricity = max(0.0, 1.0 - center_std / max(mean_r, 1.0))
        radii_sorted = sorted(radii)
        distinct = [radii_sorted[0]]
        for r in radii_sorted[1:]:
            if r - distinct[-1] > 0.10 * cl["max_r"]:
                distinct.append(r)
        n_distinct = len(distinct)
        bd = _black_disc_density(gray_small, cl["cx"], cl["cy"], cl["max_r"])
        area_score = float(math.pi * cl["max_r"] ** 2)
        score = (n_distinct
                 * (0.4 + 0.6 * concentricity)
                 * math.log1p(area_score / 1000.0)
                 * (0.5 + 1.5 * max(0.0, bd)))
        results.append({
            "cx": cl["cx"], "cy": cl["cy"],
            "n_radii": n_distinct,
            "max_r": cl["max_r"],
            "concentricity": concentricity,
            "black_density": bd,
            "score": score,
            "radii": distinct,
        })
    results.sort(key=lambda c: -c["score"])
    return results


class TargetLocalizer:
    """Multi-ring localization — ``multiring/localize.py`` ported.

    The key robustness property: it picks the candidate neighbourhood with the
    strongest *concentric ring structure*, not merely the largest dark blob.
    That rejects printed logos and text (image 29's failure mode).

    ``crop_to_target`` returns ``(crop, bbox, init)`` where ``init`` is the
    legacy dict (``cx_crop, cy_crop, s_px_init, r_bw_px_init, r_bull_px_init``)
    that downstream stages consume. The dict is intentionally preserved —
    typing it is out of scope for this port.
    """

    @staticmethod
    def find_bullseye_candidate(gray: np.ndarray, k_shortlist: int = 8) -> dict:
        """Locate the target by combining HoughCircles ring clustering with the
        radial ring-pattern score.

        Ported verbatim from cv/approaches/multiring/localize.py:248-372.
        """
        # Local import to avoid a hard dependency cycle at module load time —
        # ``black_disc_calibrator`` imports ``calibration`` but not us.
        from src.domains.vision.geometry.black_disc_calibrator import _blackdisc_center

        h, w = gray.shape
        sw, sh = max(64, w // 6), max(64, h // 6)
        small = cv2.resize(gray, (sw, sh))
        mag_full = _sobel_mag(gray)
        mag_small = cv2.resize(mag_full, (sw, sh))
        sx, sy = w / sw, h / sh

        # ---- Stage A: HoughCircles cluster ----
        clusters = _find_concentric_circles_cluster(mag_small, small)
        if clusters:
            best_cl = clusters[0]
            cx_full = best_cl["cx"] * sx
            cy_full = best_cl["cy"] * sy
            max_r = int(min(best_cl["cx"], best_cl["cy"],
                            sw - best_cl["cx"], sh - best_cl["cy"]) - 2)
            if max_r > 20:
                ring = _radial_ring_score(mag_small, best_cl["cx"], best_cl["cy"], max_r)
            else:
                ring = {"s_px": 0.0, "r_bw_px": 0.0, "r_bull_px": 0.0, "score": 0}
            scale = max(sx, sy)
            s_px = ring["s_px"] * scale
            r_disc_px = best_cl["max_r"] * scale
            return {
                "cx": float(cx_full), "cy": float(cy_full),
                "r_disc_px": float(r_disc_px),
                "s_px": float(s_px),
                "r_bw_px": float(ring["r_bw_px"] * scale),
                "r_bull_px": float(ring["r_bull_px"] * scale),
                "score": int(best_cl["n_radii"]),
                "cluster_score": float(best_cl["score"]),
                "n_radii": int(best_cl["n_radii"]),
                "concentricity": float(best_cl["concentricity"]),
                "black_density": float(best_cl["black_density"]),
                "circ": float(best_cl["concentricity"]),
                "source": "hough_cluster+ring_score",
            }

        # ---- Fallback 1: blob + ring score ----
        small_b = cv2.GaussianBlur(small, (5, 5), 0)
        _, bin_ = cv2.threshold(small_b, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        bin_ = cv2.morphologyEx(bin_, cv2.MORPH_CLOSE, ker)
        bin_ = cv2.morphologyEx(bin_, cv2.MORPH_OPEN, ker)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(bin_, 8)

        candidates = []
        for i in range(1, n):
            area_s = int(stats[i, cv2.CC_STAT_AREA])
            if area_s < 0.005 * small.size:
                continue
            cnts, _ = cv2.findContours((labels == i).astype(np.uint8),
                                       cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            c = max(cnts, key=cv2.contourArea)
            perim = cv2.arcLength(c, True)
            if perim < 1:
                continue
            circ = 4 * math.pi * area_s / (perim * perim)
            if circ < 0.20:
                continue
            m = cv2.moments((labels == i).astype(np.uint8))
            if m["m00"] == 0:
                continue
            bcx_s = m["m10"] / m["m00"]
            bcy_s = m["m01"] / m["m00"]
            bd_r_s = 0.5 * max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT])
            candidates.append({
                "cx_s": bcx_s, "cy_s": bcy_s,
                "cx": bcx_s * sx, "cy": bcy_s * sy,
                "r_disc_px": bd_r_s * max(sx, sy),
                "circ": circ, "area_s": area_s,
                "bd": _black_disc_density(small_b, bcx_s, bcy_s, bd_r_s),
            })

        if candidates:
            candidates.sort(key=lambda c: -(c["area_s"] * (0.3 + c["bd"])), )
            shortlist = candidates[:k_shortlist]
            for cand in shortlist:
                max_r = int(min(cand["cx_s"], cand["cy_s"],
                                sw - cand["cx_s"], sh - cand["cy_s"]) - 2)
                if max_r > 20:
                    cand["ring"] = _radial_ring_score(mag_small, cand["cx_s"], cand["cy_s"], max_r)
                else:
                    cand["ring"] = {"s_px": 0.0, "r_bw_px": 0.0, "r_bull_px": 0.0, "score": 0}
            shortlist.sort(key=lambda c: (c["ring"]["score"],
                                          c["area_s"] * (0.3 + max(0.0, c["bd"]))),
                           reverse=True)
            best = shortlist[0]
            scale = max(sx, sy)
            return {
                "cx": best["cx"], "cy": best["cy"],
                "r_disc_px": best["r_disc_px"],
                "s_px": best["ring"]["s_px"] * scale,
                "r_bw_px": best["ring"]["r_bw_px"] * scale,
                "r_bull_px": best["ring"]["r_bull_px"] * scale,
                "score": best["ring"]["score"],
                "circ": best["circ"],
                "source": "blob+ring_score",
            }

        # ---- Fallback 2: blackdisc_center ----
        bcx, bcy, *_ = _blackdisc_center(gray)
        return {"cx": float(bcx), "cy": float(bcy), "r_disc_px": float(min(w, h) / 8),
                "s_px": 0.0, "r_bw_px": 0.0, "r_bull_px": 0.0,
                "score": 0, "source": "fallback_blackdisc", "circ": 0.0}

    @staticmethod
    def crop_to_target(
        gray: np.ndarray,
        expand_rings: float = 1.30,
    ) -> tuple[np.ndarray, tuple[int, int, int, int], dict]:
        """Localize the target + crop a generous square around it.

        Returns ``(crop, bbox, init)``. bbox is ``(x0, y0, w, h)`` in source px.
        init carries ``cx_crop``/``cy_crop`` (bullseye init in crop px) plus
        s_px/r_bw initial estimates from the ring scan.

        Ported verbatim from cv/approaches/multiring/localize.py:375-422.
        """
        h, w = gray.shape
        cand = TargetLocalizer.find_bullseye_candidate(gray)
        cx, cy = cand["cx"], cand["cy"]

        if cand["s_px"] > 0 and cand["r_bw_px"] > 0:
            r_ring1 = cand["r_bw_px"] + 6.0 * cand["s_px"]
            half = int(math.ceil(expand_rings * r_ring1))
        elif cand["r_disc_px"] > 0:
            half = int(math.ceil(expand_rings * cand["r_disc_px"] * 2.2))
        else:
            half = int(min(w, h) / 3)

        half = max(half, int(3.0 * cand["r_disc_px"]))         # safety floor
        half = min(half, max(w, h))                             # safety ceiling

        x0 = int(max(0, cx - half))
        y0 = int(max(0, cy - half))
        x1 = int(min(w, cx + half))
        y1 = int(min(h, cy + half))
        crop = gray[y0:y1, x0:x1]
        bbox = (x0, y0, x1 - x0, y1 - y0)
        init = {
            "cx_src": float(cx), "cy_src": float(cy),
            "cx_crop": float(cx - x0), "cy_crop": float(cy - y0),
            "r_disc_px": float(cand["r_disc_px"]),
            "s_px_init": float(cand["s_px"]),
            "r_bw_px_init": float(cand["r_bw_px"]),
            "r_bull_px_init": float(cand["r_bull_px"]),
            "score": int(cand.get("score", 0)),
            "circ": float(cand.get("circ", 0.0)),
            "source": cand["source"],
        }
        return crop, bbox, init
