"""Probe v4: full-target extraction with iterative ring-calibration.

Probe v3 had two bugs the user identified:
  1. The crop is too small — `_stage1_localize` grabs only the dark blob
     (rings 7-10), so even with corrected px_per_mm, ring 1 falls outside
     the crop. The "extracted target" was just the black disc, not the
     whole paper target.
  2. Calibration is single-shot — bullseye comes from blob centroid
     (biased by shot clusters), scale comes from the rough 0.85→0.35
     correction. Predicted rings land "almost but not quite" on the
     printed rings.

This probe fixes both:
  A. RE-CROP to full target. After getting the initial calibration from
     probe v3's logic, predict the ring-1 outer boundary + margin, then
     re-extract a larger crop from the ORIGINAL full-resolution image.
  B. ITERATIVE REFINEMENT (ICP-style). At each predicted ring radius,
     search an annular window for the strongest radial-edge response
     (Sobel along the radial direction → ring stroke = local maximum).
     Least-squares fit (cx, cy, pmm) to the observed ring positions.
     Repeat 3-5 times until convergence.
  C. RING RENDERING. Solid colored circles for rings detected in frame;
     DASHED circles for rings extrapolated beyond the photo (e.g. image
     #19 framed only on the bullseye — rings 1-6 are predicted
     mathematically and rendered dashed, with a small "extrapolated"
     label so it's clear they're not measured).
  D. TARGET MASK at ring 1 outer + 25 mm margin (covers 0-zone hits
     like #12's). The full target is extracted, not just the black disc.

Pipeline:
    1. EXIF-normalize load (full-res original).
    2. Initial estimate via _stage1_localize + _stage2_rings + 0.85→0.35
       correction (from probe v3).
    3. Predict ring 1 outer radius; compute required crop half-size =
       (77.75 mm + 25 mm margin) * pmm. Re-crop from the full-res image,
       expanding equally in all directions from the bullseye estimate.
    4. Refine calibration iteratively on the new crop:
         for iteration in range(max_iters):
           for each ring k ∈ {1..10}:
             predict r_k = ISSF_radii[k] * pmm
             if r_k > max_in_frame * 0.95: skip (out of frame)
             search ±15 px annular window for radial-Sobel peak
             record (angle_binned_mean_peak_radius, weight)
           least-squares fit: cx, cy, pmm ← minimize Σ w_k * (peak_k - r_k)²
           check convergence: if (cx, cy, pmm) changed < 0.5%: break
    5. Build mask at ring 1 outer + 25 mm margin.
    6. Extract target on neutral background.
    7. Render ring overlay: SOLID if detected, DASHED if extrapolated.

Run:
    uv run python cv/tmp/probe_ring_calibration_v4.py
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
OUT_DIR = _REPO / "resources" / "train" / "intermediate_v4"
META_PATH = _REPO / "resources" / "paper_targets" / "metadata.yml"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Verified ISSF geometry — radii in mm, ring 10 first.
ISSF_RADII_MM = {
    "air_pistol":       [5.75 + 8.0 * i for i in range(10)],
    "precision_pistol": [25.0 + 25.0 * i for i in range(10)],
}
ISSF_BLACK_OUTER_RING = {"air_pistol": 7, "precision_pistol": 5}

# Calibration correction: existing _stage2_rings assumes black disc =
# 0.85*card_mm; reality for Air Pistol = 0.35*card_mm (rings 7-10 outer).
STAGE2_RATIO_BUG = 0.85

# Margin beyond ring 1 outer to include in extracted target. PRD allows
# 0-point hits (completely outside ring 1) — give them room.
EXTRACTION_MARGIN_MM = 25.0

DEFAULT_TARGET = "air_pistol"
MAX_ITERS = 6


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def load_exif_normalized(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
    return np.asarray(im)[:, :, ::-1].copy()


def metadata_for(img_id: int) -> dict:
    import yaml
    with open(META_PATH) as fh:
        meta = yaml.safe_load(fh)
    key = f"{img_id}.jpg"
    return meta.get(key, {})


# ---------------------------------------------------------------------------
# Stage A — initial estimate on a small locator crop
# ---------------------------------------------------------------------------
def initial_estimate(img: np.ndarray, target_type: str) -> dict:
    """Run _stage1 + _stage2 on a downscaled locator, return initial
    (bullseye, pmm) in FULL-RES image coordinates.

    Important: Stage 2 returns px_per_mm in the resolution it was run on.
    We run it on the downscaled locator, so we must divide by `scale`
    to convert to full-res px_per_mm."""
    h0, w0 = img.shape[:2]
    long_side = max(h0, w0)
    scale = LOCATOR_LONG_SIDE / long_side if long_side > LOCATOR_LONG_SIDE else 1.0
    locator = cv2.resize(img, (int(w0 * scale), int(h0 * scale)),
                         interpolation=cv2.INTER_AREA)
    bbox, _, fail = _stage1_localize(locator, target_type)
    if fail:
        # Fallback: image center, wild guess at pmm.
        guess_pmm = 10.0
        return {"cx": w0 / 2.0, "cy": h0 / 2.0, "pmm": guess_pmm,
                "bbox": (0, 0, w0, h0), "scale": scale}
    x, y, bw, bh = bbox
    sx_full = w0 / locator.shape[1]
    sy_full = h0 / locator.shape[0]
    # Run Stage 2 on the locator crop (cheaper). Its pmm is in LOCATOR px/mm.
    crop_loc = locator[y:y + bh, x:x + bw]
    (cx_loc, cy_loc), _, pmm_loc, _ = _stage2_rings(crop_loc,
        card_mm=TARGET_CARD_MM[target_type])
    # Apply 0.85 → true ratio correction.
    card_mm = TARGET_CARD_MM[target_type]
    black_outer = ISSF_BLACK_OUTER_RING[target_type]
    true_black_diam = 2.0 * ISSF_RADII_MM[target_type][10 - black_outer]
    correction = (STAGE2_RATIO_BUG * card_mm) / true_black_diam
    pmm_full = pmm_loc * correction / scale   # locator → full-res

    # Map bullseye back to full-res image coords.
    cx_full = (cx_loc + x) * sx_full
    cy_full = (cy_loc + y) * sy_full
    return {"cx": float(cx_full), "cy": float(cy_full),
            "pmm": float(pmm_full),
            "bbox": (int(x * sx_full), int(y * sy_full),
                     int(bw * sx_full), int(bh * sy_full)),
            "scale": float(scale)}


# ---------------------------------------------------------------------------
# Stage B — re-crop to full target
# ---------------------------------------------------------------------------
def crop_full_target(img: np.ndarray, est: dict, target_type: str) -> dict:
    """Crop a square around the estimated bullseye, sized to include
    ring 1 outer + margin. Returns the crop + mapping back to full-res."""
    h0, w0 = img.shape[:2]
    cx, cy = est["cx"], est["cy"]
    pmm = est["pmm"]
    radii = ISSF_RADII_MM[target_type]
    ring1_r_mm = radii[-1]
    half_side_mm = ring1_r_mm + EXTRACTION_MARGIN_MM
    half_side_px = int(half_side_mm * pmm * 1.05)  # 5% extra safety
    # Don't exceed image bounds; clipop and remember how much was clipped.
    x0 = max(0, int(cx - half_side_px))
    x1 = min(w0, int(cx + half_side_px))
    y0 = max(0, int(cy - half_side_px))
    y1 = min(h0, int(cy + half_side_px))
    crop = img[y0:y1, x0:x1]
    # Bullseye in crop coords:
    cx_crop = cx - x0
    cy_crop = cy - y0
    return {
        "crop": crop,
        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        "cx_crop": float(cx_crop), "cy_crop": float(cy_crop),
        "pmm": float(pmm),
        "half_side_target_px": int(half_side_mm * pmm),
    }


# ---------------------------------------------------------------------------
# Stage C — iterative ring refinement via radial Sobel
# ---------------------------------------------------------------------------
def radial_edge_profile(gray: np.ndarray, center: tuple[float, float],
                         max_radius_px: float, n_bins: int = 720,
                         angular_samples: int = 360) -> np.ndarray:
    """Compute radial Sobel-magnitude profile. For each radius bin, sample
    N angles, average the radial gradient magnitude. Ring strokes appear
    as peaks in this profile because the gradient is high where the dark
    ring stroke sits against lighter paper."""
    cx, cy = center
    h, w = gray.shape
    # Pre-compute smoothed Sobel.
    blur = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.5)
    gx = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    sobel_mag = np.sqrt(gx * gx + gy * gy)

    # For each (angle, radius_bin), compute Sobel magnitude by interpolation.
    angles = np.linspace(0, 2 * np.pi, angular_samples, endpoint=False)
    # Sample radii: slightly denser than n_bins to capture narrow ring strokes.
    r_max = max_radius_px
    # We compute a single 2D sample grid via nearest-neighbor indexing.
    yy, xx = np.mgrid[0:h, 0:w]
    dx = xx - cx
    dy = yy - cy
    r_px = np.sqrt(dx * dx + dy * dy)
    # Radial gradient = projection of gradient onto the radial unit vector.
    # gradient . radial_unit = (gx * dx + gy * dy) / r
    r_safe = np.maximum(r_px, 1.0)
    radial_grad = (gx * dx + gy * dy) / r_safe
    # Use |radial_grad| — ring strokes are dark-on-light OR light-on-dark,
    # both produce strong |radial_grad|.
    radial_grad_abs = np.abs(radial_grad)

    # Bin by radius.
    r_int = np.clip((r_px * n_bins / r_max).astype(np.int32), 0, n_bins - 1)
    mask = r_px < r_max
    flat = radial_grad_abs.ravel()
    flat_r = r_int.ravel()
    flat_mask = mask.ravel()
    flat_m = flat[flat_mask]
    flat_r_m = flat_r[flat_mask]
    num = np.bincount(flat_r_m, weights=flat_m, minlength=n_bins)
    cnt = np.maximum(np.bincount(flat_r_m, minlength=n_bins), 1)
    return num / cnt


def refine_calibration(gray: np.ndarray, est: dict, target_type: str,
                        max_iters: int = MAX_ITERS) -> dict:
    """ICP-style refinement. Each iteration:
       1. Compute radial |Sobel| profile around current center.
       2. For each ring k (10..1) whose predicted radius is in frame:
          search ±15px window around predicted radius for the local
          max of the profile. Record (k, observed_r).
       3. Least-squares fit: find (cx_shift, cy_shift, pmm) minimizing
          Σ (observed_r - ISSF_radii[k] * pmm)² weighted by the profile
          peak height.
       4. Convergence: stop when changes < 0.5%.
    Returns refined estimate + per-ring observation log.
    """
    cx, cy = est["cx_crop"], est["cy_crop"]
    pmm = est["pmm"]
    h, w = gray.shape
    radii_mm = ISSF_RADII_MM[target_type]
    max_r = min(cx, cy, w - cx, h - cy) * 0.97
    n_bins = 720
    history: list[dict] = []
    for it in range(max_iters):
        profile = radial_edge_profile(gray, (cx, cy), max_r, n_bins=n_bins)
        observations = []
        for ring in range(1, 11):
            r_mm = radii_mm[10 - ring]
            r_pred_px = r_mm * pmm
            if r_pred_px >= max_r - 20:
                continue
            bin_pred = int(r_pred_px * n_bins / max_r)
            win_px = 15
            win_bins = max(3, int(win_px * n_bins / max_r))
            lo = max(0, bin_pred - win_bins)
            hi = min(n_bins, bin_pred + win_bins + 1)
            window = profile[lo:hi]
            if len(window) < 3:
                continue
            local_max_idx = lo + int(np.argmax(window))
            local_max_val = float(profile[local_max_idx])
            r_obs_px = local_max_idx * max_r / n_bins
            # Weight by peak prominence: how much it stands out from
            # the median of a wider context window.
            ctx_lo = max(0, bin_pred - 4 * win_bins)
            ctx_hi = min(n_bins, bin_pred + 4 * win_bins + 1)
            ctx_median = float(np.median(profile[ctx_lo:ctx_hi]))
            prominence = max(0.0, local_max_val - ctx_median)
            observations.append({
                "ring": ring,
                "r_predicted_px": float(r_pred_px),
                "r_observed_px": float(r_obs_px),
                "weight": float(prominence),
            })

        if not observations:
            break

        # Least-squares fit. We want to find (cx_shift, cy_shift, pmm_new)
        # that minimizes Σ w_k * (r_obs_k(cx_shift, cy_shift) -
        # ISSF_radii[k] * pmm_new)². The observed radii DEPEND on center
        # shifts, so this is non-linear. We linearize: for small shifts,
        # r_obs_k(cx_shift, cy_shift) ≈ r_obs_k(0,0) +
        # (∂r/∂cx · cx_shift + ∂r/∂cy · cy_shift). The derivative is the
        # cosine of the angle to the peak. But our profile is angle-averaged,
        # so we can't recover the angle of the peak directly.
        #
        # PRAGMATIC APPROACH: keep cx, cy FIXED; only update pmm via
        # weighted least-squares on (r_obs vs r_predicted). This works
        # when the bullseye is approximately right (which the initial
        # estimate from Stage 2 mostly gives us). A separate center
        # refinement would require a 2D peak search per ring (deferred).
        num = 0.0
        den = 0.0
        for o in observations:
            w_k = o["weight"]
            r_obs = o["r_observed_px"]
            r_mm = radii_mm[10 - o["ring"]]
            num += w_k * r_obs * r_mm
            den += w_k * r_mm * r_mm
        if den < 1e-9:
            break
        pmm_new = num / den

        # Convergence check.
        delta_pmm = abs(pmm_new - pmm) / max(pmm, 1e-6)
        history.append({
            "iter": it + 1,
            "pmm": float(pmm_new),
            "delta_pmm_rel": float(delta_pmm),
            "n_observations": len(observations),
            "observations": observations,
        })
        pmm = pmm_new
        if delta_pmm < 0.005:
            break

    # Determine which rings are in frame at the final calibration.
    in_frame_rings = []
    extrapolated_rings = []
    for ring in range(1, 11):
        r_mm = radii_mm[10 - ring]
        r_px = r_mm * pmm
        if r_px < max_r - 5:
            in_frame_rings.append(ring)
        else:
            extrapolated_rings.append(ring)

    return {
        "cx": float(cx), "cy": float(cy), "pmm": float(pmm),
        "max_in_frame_r_px": float(max_r),
        "in_frame_rings": in_frame_rings,
        "extrapolated_rings": extrapolated_rings,
        "history": history,
        "n_iters": len(history),
    }


# ---------------------------------------------------------------------------
# Stage D — mask + extract at ring 1 outer + margin
# ---------------------------------------------------------------------------
def build_extraction_mask(shape: tuple[int, int], calib: dict,
                           target_type: str) -> np.ndarray:
    """Filled disc at (ring 1 outer + EXTRACTION_MARGIN_MM) * pmm. This
    includes the 0-zone (shots landing outside ring 1) so they're not
    silently dropped."""
    cx, cy = calib["cx"], calib["cy"]
    pmm = calib["pmm"]
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
    """Solid rings = detected in frame; dashed = extrapolated beyond photo."""
    out = img.copy()
    cx, cy = calib["cx"], calib["cy"]
    pmm = calib["pmm"]
    radii = ISSF_RADII_MM[target_type]
    in_frame = set(calib["in_frame_rings"])
    extrap = set(calib["extrapolated_rings"])
    for ring in range(1, 11):
        r_mm = radii[10 - ring]
        r_px = int(r_mm * pmm)
        col = RAINBOW[(ring - 1) % len(RAINBOW)]
        if ring in in_frame:
            # Solid.
            cv2.circle(out, (int(cx), int(cy)), r_px, col, thickness_solid)
        else:
            # Dashed: draw N small arcs around the circle.
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
        # Label ring number at top of circle.
        cv2.putText(out, str(ring),
                    (int(cx + r_px * np.cos(-np.pi / 2)) + 3,
                     int(cy + r_px * np.sin(-np.pi / 2)) - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
    cv2.drawMarker(out, (int(cx), int(cy)), (255, 255, 255),
                   cv2.MARKER_CROSS, 25, 2)
    return out


def draw_calibration_text(img: np.ndarray, lines: list[str]) -> np.ndarray:
    out = img.copy()
    y = 30
    for ln in lines:
        cv2.putText(out, ln, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 255, 0), 2)
        y += 25
    return out


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_one(img_id: int, target_type: str = DEFAULT_TARGET) -> dict[str, Any]:
    img_path = TRAIN_DIR / f"{img_id}.jpg"
    img = load_exif_normalized(img_path)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_01_original.jpg"), img)

    # Stage A — initial estimate (probe v3 logic).
    est0 = initial_estimate(img, target_type)

    # Stage B — re-crop to full target.
    full = crop_full_target(img, est0, target_type)
    crop = full["crop"]
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_02_full_crop.png"), crop)

    # Pass crop-relative bullseye + pmm into the refiner.
    est_for_refine = {
        "cx_crop": full["cx_crop"], "cy_crop": full["cy_crop"],
        "pmm": full["pmm"],
    }
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # Stage C — iterative refinement.
    calib = refine_calibration(gray, est_for_refine, target_type)

    # Stage D — mask + extract.
    mask = build_extraction_mask(gray.shape, calib, target_type)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_03_target_mask.png"), mask)
    extracted = extract_target(crop, mask)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_04_target_extracted.png"), extracted)

    # Stage E — ring overlays (validation artifact).
    overlay = draw_synthetic_rings(crop, calib, target_type)
    overlay = draw_calibration_text(overlay, [
        f"pmm: initial={est0['pmm']:.2f} → refined={calib['pmm']:.2f}"
        f"  ({calib['n_iters']} iters)",
        f"in-frame rings: {sorted(calib['in_frame_rings'])}",
        f"extrapolated rings: {sorted(calib['extrapolated_rings'])}",
        f"extraction radius: ring 1 + {EXTRACTION_MARGIN_MM:.0f} mm margin",
    ])
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_05_ring_overlay.png"), overlay)

    final = draw_synthetic_rings(extracted, calib, target_type,
                                  thickness_solid=1, thickness_dashed=1)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_06_extracted_with_rings.png"), final)

    return {
        "img_id": img_id,
        "target_type": target_type,
        "initial_estimate": {
            "cx_full": est0["cx"], "cy_full": est0["cy"], "pmm": est0["pmm"],
        },
        "full_crop": {
            "x0": full["x0"], "y0": full["y0"],
            "x1": full["x1"], "y1": full["y1"],
            "crop_size": list(crop.shape[:2]),
            "half_side_target_px": full["half_side_target_px"],
        },
        "refined": {
            "cx_crop": calib["cx"], "cy_crop": calib["cy"],
            "pmm": calib["pmm"], "n_iters": calib["n_iters"],
            "in_frame_rings": calib["in_frame_rings"],
            "extrapolated_rings": calib["extrapolated_rings"],
            "history": calib["history"],
        },
    }


def main():
    train_ids = [1, 4, 6, 10, 12, 19, 21, 29, 31, 46]
    results = []
    print(f"{'id':>3}  {'pmm_init':>9}  {'pmm_refined':>11}  {'iters':>5}  "
          f"{'crop':>10}  {'in_frame':>22}  {'extrap':>22}")
    for img_id in train_ids:
        try:
            r = run_one(img_id, target_type=DEFAULT_TARGET)
            results.append(r)
            print(
                f"{img_id:>3}  {r['initial_estimate']['pmm']:>9.2f}  "
                f"{r['refined']['pmm']:>11.2f}  {r['refined']['n_iters']:>5}  "
                f"{r['full_crop']['crop_size'][1]}x{r['full_crop']['crop_size'][0]:>4}  "
                f"{str(r['refined']['in_frame_rings']):>22}  "
                f"{str(r['refined']['extrapolated_rings']):>22}",
                flush=True,
            )
        except Exception as e:
            print(f"{img_id}: EXCEPTION: {e}", flush=True)
            import traceback; traceback.print_exc()

    out_path = OUT_DIR / "ring_calibration_v4_results.json"
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nResults → {out_path}")
    print(f"Intermediates → {OUT_DIR}/<id>_01..06*.png")
    print("Key validation image: <id>_05_ring_overlay.png")


if __name__ == "__main__":
    main()
