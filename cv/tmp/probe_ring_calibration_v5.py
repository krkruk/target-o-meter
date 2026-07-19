"""Probe v5: HoughCircles-anchored bullseye + iterative pmm refinement.

Probe v4 still used Stage 2's blob-centroid bullseye, which is biased by
shot clusters (up to 49 px off on image 31 — 33% of ring 10's radius,
explaining the user's "ring 10 covers only 2/3 of real area" feedback).

This probe replaces the bullseye estimate with a direct HoughCircles
detection of the black-disc boundary on a Sobel edge map. The black disc
boundary is the strongest circular edge in the image, so HoughCircles
finds it robustly. Once we have (bullseye, black_disc_radius), px_per_mm
follows directly: pmm = black_disc_radius / 29.75.

Pipeline:
    1. EXIF-normalize load.
    2. Initial estimate via Stage 1 + Stage 2 + 0.85→0.35 correction.
    3. Re-crop to predicted ring 1 + margin from full-res original.
    4. HoughCircles on Sobel map → precise black disc center + radius.
    5. Compute pmm from black disc radius.
    6. Iterative refinement via radial-edge profile (only adjusts pmm).
    7. Predict all 10 ring positions.
    8. Build mask at ring 1 outer + 25 mm margin.
    9. Render overlay: SOLID if detected in frame, DASHED if extrapolated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

_HERE = Path(__file__).resolve().parent
_CV_DIR = _HERE.parent
_REPO = _CV_DIR.parent
sys.path.insert(0, str(_CV_DIR))
from detect import _stage1_localize, _stage2_rings, LOCATOR_LONG_SIDE, TARGET_CARD_MM  # noqa: E402

TRAIN_DIR = _REPO / "resources" / "train"
OUT_DIR = _REPO / "resources" / "train" / "intermediate_v5"
META_PATH = _REPO / "resources" / "paper_targets" / "metadata.yml"
OUT_DIR.mkdir(parents=True, exist_ok=True)

ISSF_RADII_MM = {
    "air_pistol":       [5.75 + 8.0 * i for i in range(10)],
    "precision_pistol": [25.0 + 25.0 * i for i in range(10)],
}
ISSF_BLACK_OUTER_RING = {"air_pistol": 7, "precision_pistol": 5}
STAGE2_RATIO_BUG = 0.85
EXTRACTION_MARGIN_MM = 25.0
DEFAULT_TARGET = "air_pistol"


def load_exif_normalized(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
    return np.asarray(im)[:, :, ::-1].copy()


def metadata_for(img_id: int) -> dict:
    import yaml
    with open(META_PATH) as fh:
        meta = yaml.safe_load(fh)
    return meta.get(f"{img_id}.jpg", {})


# ---------------------------------------------------------------------------
# Stage A — initial estimate (Stage 2 + correction, in full-res coords)
# ---------------------------------------------------------------------------
def initial_estimate(img: np.ndarray, target_type: str) -> dict:
    h0, w0 = img.shape[:2]
    long_side = max(h0, w0)
    scale = LOCATOR_LONG_SIDE / long_side if long_side > LOCATOR_LONG_SIDE else 1.0
    locator = cv2.resize(img, (int(w0 * scale), int(h0 * scale)),
                         interpolation=cv2.INTER_AREA)
    bbox, _, fail = _stage1_localize(locator, target_type)
    if fail:
        return {"cx": w0 / 2.0, "cy": h0 / 2.0, "pmm": 10.0, "scale": scale}
    x, y, bw, bh = bbox
    sx_full = w0 / locator.shape[1]
    sy_full = h0 / locator.shape[0]
    crop_loc = locator[y:y + bh, x:x + bw]
    (cx_loc, cy_loc), _, pmm_loc, _ = _stage2_rings(crop_loc,
        card_mm=TARGET_CARD_MM[target_type])
    card_mm = TARGET_CARD_MM[target_type]
    black_outer = ISSF_BLACK_OUTER_RING[target_type]
    true_black_diam = 2.0 * ISSF_RADII_MM[target_type][10 - black_outer]
    correction = (STAGE2_RATIO_BUG * card_mm) / true_black_diam
    pmm_full = pmm_loc * correction / scale
    cx_full = (cx_loc + x) * sx_full
    cy_full = (cy_loc + y) * sy_full
    return {"cx": float(cx_full), "cy": float(cy_full),
            "pmm": float(pmm_full), "scale": float(scale)}


# ---------------------------------------------------------------------------
# Stage B — re-crop to predicted full target
# ---------------------------------------------------------------------------
def crop_full_target(img: np.ndarray, est: dict, target_type: str) -> dict:
    h0, w0 = img.shape[:2]
    cx, cy, pmm = est["cx"], est["cy"], est["pmm"]
    ring1_r_mm = ISSF_RADII_MM[target_type][-1]
    half_mm = ring1_r_mm + EXTRACTION_MARGIN_MM
    half_px = int(half_mm * pmm * 1.05)
    x0 = max(0, int(cx - half_px))
    x1 = min(w0, int(cx + half_px))
    y0 = max(0, int(cy - half_px))
    y1 = min(h0, int(cy + half_px))
    return {
        "crop": img[y0:y1, x0:x1],
        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        "cx_crop": float(cx - x0), "cy_crop": float(cy - y0),
        "pmm": float(pmm),
    }


# ---------------------------------------------------------------------------
# Stage C — HoughCircles-anchored black disc detection
# ---------------------------------------------------------------------------
def detect_black_disc(gray: np.ndarray, est: dict,
                       target_type: str) -> dict:
    """Find the black disc boundary (the strongest circular edge in image)
    via HoughCircles on a Sobel-magnitude map.

    Returns refined {cx, cy, black_disc_radius_px, pmm, offset_from_est}.
    """
    cx0, cy0 = est["cx_crop"], est["cy_crop"]
    pmm0 = est["pmm"]
    black_r_mm = ISSF_RADII_MM[target_type][10 - ISSF_BLACK_OUTER_RING[target_type]]
    expected_r = black_r_mm * pmm0
    H, W = gray.shape
    max_r = min(cx0, cy0, W - cx0, H - cy0) * 0.95

    # Sobel magnitude map.
    blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
    gx = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    if mag.max() < 1e-6:
        return {"found": False, "cx": cx0, "cy": cy0, "pmm": pmm0}
    mag_u8 = ((mag / mag.max()) * 255).astype(np.uint8)

    # HoughCircles with tight radius range around expected black disc.
    r_min = max(10, int(0.7 * expected_r))
    r_max = min(int(max_r), int(1.3 * expected_r))
    if r_max <= r_min + 5:
        return {"found": False, "cx": cx0, "cy": cy0, "pmm": pmm0,
                "reason": "radius range too narrow"}
    circles = cv2.HoughCircles(
        mag_u8, cv2.HOUGH_GRADIENT_ALT, dp=1.5,
        minDist=max(int(0.5 * expected_r), r_min + 1),
        param1=80, param2=0.70,
        minRadius=r_min, maxRadius=r_max,
    )
    if circles is None:
        return {"found": False, "cx": cx0, "cy": cy0, "pmm": pmm0,
                "reason": "no circles detected"}

    # Pick the circle whose center is closest to the Stage 2 estimate
    # (avoids picking up a stray strong edge elsewhere).
    best = None
    best_d = float("inf")
    for c in circles[0]:
        cx, cy, r = float(c[0]), float(c[1]), float(c[2])
        d = math.hypot(cx - cx0, cy - cy0)
        if d < best_d:
            best_d = d
            best = (cx, cy, r)

    if best is None:
        return {"found": False, "cx": cx0, "cy": cy0, "pmm": pmm0,
                "reason": "no suitable circle"}
    cx, cy, r = best
    pmm = r / black_r_mm
    return {
        "found": True,
        "cx": float(cx), "cy": float(cy),
        "black_disc_radius_px": float(r),
        "black_disc_radius_mm": float(black_r_mm),
        "pmm": float(pmm),
        "offset_from_est_px": float(best_d),
        "edge_map": mag_u8,
    }


# ---------------------------------------------------------------------------
# Stage D — iterative pmm refinement on radial |Sobel| profile
# ---------------------------------------------------------------------------
def radial_edge_profile(gray: np.ndarray, center: tuple[float, float],
                         max_radius_px: float, n_bins: int = 720) -> np.ndarray:
    cx, cy = center
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (0, 0), 1.5)
    gx = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    yy, xx = np.mgrid[0:h, 0:w]
    dx = xx - cx
    dy = yy - cy
    r_px = np.sqrt(dx * dx + dy * dy)
    r_safe = np.maximum(r_px, 1.0)
    radial_grad = np.abs((gx * dx + gy * dy) / r_safe)
    mask = r_px < max_radius_px
    r_int = np.clip((r_px * n_bins / max_radius_px).astype(np.int32), 0, n_bins - 1)
    flat = radial_grad.ravel()[mask.ravel()]
    flat_r = r_int.ravel()[mask.ravel()]
    num = np.bincount(flat_r, weights=flat, minlength=n_bins)
    cnt = np.maximum(np.bincount(flat_r, minlength=n_bins), 1)
    return num / cnt


def refine_pmm(gray: np.ndarray, calib: dict, target_type: str,
                max_iters: int = 5) -> dict:
    """ICP-style pmm refinement. Center is FIXED (HoughCircles already gave
    us a precise bullseye). Each iteration: predict ring radii at current
    pmm; for each ring whose predicted radius is in frame, search ±15 px
    window for the radial-edge peak; weighted-least-squares update of pmm.
    """
    cx, cy, pmm = calib["cx"], calib["cy"], calib["pmm"]
    h, w = gray.shape
    max_r = min(cx, cy, w - cx, h - cy) * 0.97
    radii_mm = ISSF_RADII_MM[target_type]
    n_bins = 720
    history = []
    for it in range(max_iters):
        profile = radial_edge_profile(gray, (cx, cy), max_r, n_bins)
        observations = []
        for ring in range(1, 11):
            r_mm = radii_mm[10 - ring]
            r_pred = r_mm * pmm
            if r_pred >= max_r - 20:
                continue
            bin_pred = int(r_pred * n_bins / max_r)
            win_bins = max(3, int(15 * n_bins / max_r))
            lo, hi = max(0, bin_pred - win_bins), min(n_bins, bin_pred + win_bins + 1)
            window = profile[lo:hi]
            if len(window) < 3:
                continue
            local_max_idx = lo + int(np.argmax(window))
            local_max_val = float(profile[local_max_idx])
            r_obs = local_max_idx * max_r / n_bins
            ctx_lo = max(0, bin_pred - 4 * win_bins)
            ctx_hi = min(n_bins, bin_pred + 4 * win_bins + 1)
            ctx_median = float(np.median(profile[ctx_lo:ctx_hi]))
            prominence = max(0.0, local_max_val - ctx_median)
            observations.append({
                "ring": ring, "r_pred": float(r_pred), "r_obs": float(r_obs),
                "weight": float(prominence),
            })
        if not observations:
            break
        num = sum(o["weight"] * o["r_obs"] * radii_mm[10 - o["ring"]]
                  for o in observations)
        den = sum(o["weight"] * radii_mm[10 - o["ring"]] ** 2
                  for o in observations)
        if den < 1e-9:
            break
        pmm_new = num / den
        delta = abs(pmm_new - pmm) / max(pmm, 1e-6)
        history.append({"iter": it + 1, "pmm": pmm_new, "delta_rel": delta,
                        "n_obs": len(observations), "obs": observations})
        pmm = pmm_new
        if delta < 0.005:
            break

    in_frame, extrap = [], []
    for ring in range(1, 11):
        r_mm = radii_mm[10 - ring]
        if r_mm * pmm < max_r - 5:
            in_frame.append(ring)
        else:
            extrap.append(ring)

    return {**calib,
            "pmm": float(pmm),
            "pmm_before_refine": float(calib["pmm"]),
            "max_in_frame_r_px": float(max_r),
            "in_frame_rings": in_frame,
            "extrapolated_rings": extrap,
            "refine_history": history,
            "n_iters": len(history)}


# ---------------------------------------------------------------------------
# Stage E — mask + extract
# ---------------------------------------------------------------------------
def build_extraction_mask(shape: tuple[int, int], calib: dict,
                           target_type: str) -> np.ndarray:
    cx, cy, pmm = calib["cx"], calib["cy"], calib["pmm"]
    radii = ISSF_RADII_MM[target_type]
    ring1_r_mm = radii[-1]
    outer_r_mm = ring1_r_mm + EXTRACTION_MARGIN_MM
    outer_r_px = int(outer_r_mm * pmm)
    mask = np.zeros(shape, dtype=np.uint8)
    cv2.circle(mask, (int(cx), int(cy)), outer_r_px, 255, -1)
    return mask


def extract_target(img: np.ndarray, mask: np.ndarray,
                   bg_color: tuple[int, int, int] = (245, 245, 245)) -> np.ndarray:
    out = img.copy()
    inv = cv2.bitwise_not(mask)
    bg = np.full_like(img, bg_color, dtype=np.uint8)
    out = cv2.bitwise_and(out, out, mask=mask)
    bg = cv2.bitwise_and(bg, bg, mask=inv)
    return cv2.add(out, bg)


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------
RAINBOW = [
    (0, 0, 255), (0, 127, 255), (0, 255, 255), (0, 255, 0),
    (255, 255, 0), (255, 127, 0), (255, 0, 0), (255, 0, 127),
    (255, 0, 255), (127, 0, 255),
]


def draw_synthetic_rings(img: np.ndarray, calib: dict, target_type: str,
                          thickness_solid: int = 2,
                          thickness_dashed: int = 1) -> np.ndarray:
    out = img.copy()
    cx, cy, pmm = calib["cx"], calib["cy"], calib["pmm"]
    radii = ISSF_RADII_MM[target_type]
    in_frame = set(calib["in_frame_rings"])
    for ring in range(1, 11):
        r_mm = radii[10 - ring]
        r_px = int(r_mm * pmm)
        col = RAINBOW[(ring - 1) % len(RAINBOW)]
        if ring in in_frame:
            cv2.circle(out, (int(cx), int(cy)), r_px, col, thickness_solid)
        else:
            n_dashes = 36
            for i in range(n_dashes):
                if i % 2 == 0:
                    continue
                a0 = 2 * np.pi * i / n_dashes
                a1 = 2 * np.pi * (i + 1) / n_dashes
                p0 = (int(cx + r_px * np.cos(a0)),
                      int(cy + r_px * np.sin(a0)))
                p1 = (int(cx + r_px * np.cos(a1)),
                      int(cy + r_px * np.sin(a1)))
                cv2.line(out, p0, p1, col, thickness_dashed)
        cv2.putText(out, str(ring),
                    (int(cx + r_px * np.cos(-np.pi / 2)) + 3,
                     int(cy + r_px * np.sin(-np.pi / 2)) - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
    cv2.drawMarker(out, (int(cx), int(cy)), (255, 255, 255),
                   cv2.MARKER_CROSS, 25, 2)
    return out


def draw_text(img: np.ndarray, lines: list[str]) -> np.ndarray:
    out = img.copy()
    y = 30
    for ln in lines:
        cv2.putText(out, ln, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 255, 0), 2)
        y += 25
    return out


import math  # noqa: E402  (used in detect_black_disc)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_one(img_id: int, target_type: str = DEFAULT_TARGET) -> dict[str, Any]:
    img_path = TRAIN_DIR / f"{img_id}.jpg"
    img = load_exif_normalized(img_path)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_01_original.jpg"), img)

    est0 = initial_estimate(img, target_type)
    full = crop_full_target(img, est0, target_type)
    crop = full["crop"]
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_02_full_crop.png"), crop)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # Stage C — HoughCircles-anchored black disc.
    est_for_detect = {"cx_crop": full["cx_crop"], "cy_crop": full["cy_crop"],
                      "pmm": full["pmm"]}
    disc = detect_black_disc(gray, est_for_detect, target_type)
    if disc.get("edge_map") is not None:
        cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_03_sobel_edge_map.png"),
                    disc["edge_map"])

    # Stage D — refine pmm via ICP.
    if disc["found"]:
        calib_in = {
            "cx": disc["cx"], "cy": disc["cy"], "pmm": disc["pmm"],
            "black_disc_radius_px": disc["black_disc_radius_px"],
        }
    else:
        # Fall back to initial estimate.
        calib_in = {"cx": full["cx_crop"], "cy": full["cy_crop"],
                    "pmm": full["pmm"], "black_disc_radius_px": None}
    calib = refine_pmm(gray, calib_in, target_type)

    # Stage E — mask + extract.
    mask = build_extraction_mask(gray.shape, calib, target_type)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_04_target_mask.png"), mask)
    extracted = extract_target(crop, mask)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_05_target_extracted.png"), extracted)

    # Stage F — overlay (validation artifact).
    overlay = draw_synthetic_rings(crop, calib, target_type)
    overlay = draw_text(overlay, [
        f"pmm: initial={est0['pmm']:.2f}  HoughCircles={disc.get('pmm', float('nan')):.2f}  refined={calib['pmm']:.2f}",
        f"bullseye offset from Stage 2: {disc.get('offset_from_est_px', -1):.0f} px",
        f"in-frame rings: {sorted(calib['in_frame_rings'])}",
        f"extrapolated rings: {sorted(calib['extrapolated_rings'])}",
    ])
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_06_ring_overlay.png"), overlay)

    final = draw_synthetic_rings(extracted, calib, target_type,
                                  thickness_solid=1, thickness_dashed=1)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_07_extracted_with_rings.png"), final)

    return {
        "img_id": img_id,
        "stage2_pmm_initial": est0["pmm"],
        "hough_pmm": disc.get("pmm"),
        "hough_offset_from_stage2_px": disc.get("offset_from_est_px"),
        "refined_pmm": calib["pmm"],
        "bullseye_crop": [calib["cx"], calib["cy"]],
        "in_frame_rings": calib["in_frame_rings"],
        "extrapolated_rings": calib["extrapolated_rings"],
        "n_iters": calib["n_iters"],
        "crop_size": list(crop.shape[:2]),
    }


def main():
    train_ids = [1, 4, 6, 10, 12, 19, 21, 29, 31, 46]
    results = []
    print(f"{'id':>3}  {'pmm_init':>8}  {'pmm_hough':>9}  {'pmm_refined':>11}  "
          f"{'offset':>6}  {'iters':>5}  {'crop':>10}  {'in_frame':>22}  {'extrap':>22}")
    for img_id in train_ids:
        try:
            r = run_one(img_id)
            results.append(r)
            print(
                f"{img_id:>3}  {r['stage2_pmm_initial']:>8.2f}  "
                f"{(r['hough_pmm'] or 0):>9.2f}  {r['refined_pmm']:>11.2f}  "
                f"{(r['hough_offset_from_stage2_px'] or 0):>6.0f}  {r['n_iters']:>5}  "
                f"{r['crop_size'][1]}x{r['crop_size'][0]:>4}  "
                f"{str(r['in_frame_rings']):>22}  "
                f"{str(r['extrapolated_rings']):>22}",
                flush=True,
            )
        except Exception as e:
            print(f"{img_id}: EXCEPTION: {e}", flush=True)
            import traceback; traceback.print_exc()

    out_path = OUT_DIR / "ring_calibration_v5_results.json"
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nResults → {out_path}")
    print(f"Intermediates → {OUT_DIR}/<id>_01..07*.png")
    print("Key validation image: <id>_06_ring_overlay.png")


if __name__ == "__main__":
    main()
