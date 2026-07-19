"""Probe 2: Pyramid + wavelet feature stack for hole detection on the
calibration-corrected, background-eliminated target.

Reads the extracted-target images from probe v3, applies each candidate
feature, and runs HoughCircles on each to compare detection quality vs the
existing baseline.

Features compared (all OpenCV-only, no new deps):
    A. local_std at bullet scale  (baseline from current cv/detect.py)
    B. DoG at bullet scale        (sigma1 = r_b/2, sigma2 = r_b)
    C. Gabor bank, 4 orientations (sigma=0.5*r_b, lambd=2*r_b)
    D. Laplacian pyramid level    (band-pass at scale ~ r_b)
    E. FUSED (union + NMS)        (uses all four above)

For each image × feature, reports:
    * n_pred  — hole count
    * n_true  — ground truth
    * jaccard — multiset score Jaccard
    * snr_local — peak/median ratio on a known hole ROI

Outputs to resources/train/intermediate/features/:
    <id>_<feature>.png              — feature map (display normalized)
    <id>_<feature>_detections.png   — feature map + HoughCircles detections
    feature_summary.json            — per-image × per-feature metrics

Run:
    uv run python cv/tmp/probe_feature_stack.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

_HERE = Path(__file__).resolve().parent
_CV_DIR = _HERE.parent
_REPO = _CV_DIR.parent
sys.path.insert(0, str(_CV_DIR))
from detect import (_stage1_localize, _stage2_rings, LOCATOR_LONG_SIDE,
                     TARGET_CARD_MM, CALIBER_DIAMETER_MM,
                     _stage5_score)  # noqa: E402
# Import probe v3 helpers — same directory.
sys.path.insert(0, str(_HERE))
from probe_ring_calibration_v3 import (  # noqa: E402
    ISSF_RADII_MM, ISSF_BLACK_OUTER_RING, STAGE2_BLACK_RATIO_BUG,
    corrected_calibration, build_target_mask, extract_target,
    fit_outermost_in_frame_ring,
)

TRAIN_DIR = _REPO / "resources" / "train"
OUT_DIR = _REPO / "resources" / "train" / "intermediate"
FEATURE_DIR = OUT_DIR / "features"
META_PATH = _REPO / "resources" / "paper_targets" / "metadata.yml"
FEATURE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_TARGET = "air_pistol"


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
    if key in meta:
        entry = meta[key]
        entry.setdefault("id", img_id)
        return entry
    return {"id": img_id, "hits": [], "caliber": "22lr"}


def multiset_jaccard(a: list[int], b: list[int]) -> float:
    ca, cb = Counter(a), Counter(b)
    inter = sum((ca & cb).values())
    union = sum((ca | cb).values())
    if union == 0:
        return 1.0 if inter == 0 else 0.0
    return inter / union


# ---------------------------------------------------------------------------
# Localize + calibrate + extract (uses probe v3 logic)
# ---------------------------------------------------------------------------
def prepare_target(img: np.ndarray, target_type: str) -> dict:
    """Return {crop, gray_extracted, center, pmm, mask, outer_in_frame_ring,
    bullet_radius_px, caliber_info}."""
    h0, w0 = img.shape[:2]
    long_side = max(h0, w0)
    scale = LOCATOR_LONG_SIDE / long_side if long_side > LOCATOR_LONG_SIDE else 1.0
    locator = cv2.resize(img, (int(w0 * scale), int(h0 * scale)),
                         interpolation=cv2.INTER_AREA)
    bbox, _, fail = _stage1_localize(locator, target_type)
    if fail:
        x, y, bw, bh = 0, 0, w0, h0
    else:
        x, y, bw, bh = bbox
    sx, sy = w0 / locator.shape[1], h0 / locator.shape[0]
    bx0, by0 = int(x * sx), int(y * sy)
    bx1, by1 = int((x + bw) * sx), int((y + bh) * sy)
    crop = img[by0:by1, bx0:bx1]

    (cx, cy), _, pmm_old, _ = _stage2_rings(crop, card_mm=TARGET_CARD_MM[target_type])
    cal = corrected_calibration(target_type)
    pmm = pmm_old * cal["correction_factor"]
    outer = fit_outermost_in_frame_ring((cx, cy), pmm, target_type, crop.shape)
    mask = build_target_mask(crop.shape[:2], (cx, cy), pmm, target_type, outer)
    extracted = extract_target(crop, mask)
    gray_ext = cv2.cvtColor(extracted, cv2.COLOR_BGR2GRAY)

    return {
        "crop": crop, "extracted": extracted, "gray_extracted": gray_ext,
        "mask": mask, "center": (float(cx), float(cy)),
        "pmm": float(pmm), "outer_in_frame_ring": int(outer),
    }


# ---------------------------------------------------------------------------
# Feature maps (all return float32 same shape as input gray)
# ---------------------------------------------------------------------------
def feat_local_std(g: np.ndarray, r_b: float) -> np.ndarray:
    """Local standard deviation with kernel scaled to ~1.5 * bullet radius,
    capped at 51 px (matches the baseline in cv/detect.py:322). Without the
    cap, large-caliber / high-pmm images produce huge kernels that wash out
    the texture signal."""
    k = max(15, min(51, int(1.5 * r_b)))
    if k % 2 == 0:
        k += 1
    f = g.astype(np.float32)
    mu = cv2.boxFilter(f, ddepth=cv2.CV_32F, ksize=(k, k), normalize=True)
    mu_sq = cv2.boxFilter(f * f, ddepth=cv2.CV_32F, ksize=(k, k), normalize=True)
    var = np.maximum(mu_sq - mu * mu, 0.0)
    return np.sqrt(var)


def feat_dog(g: np.ndarray, r_b: float) -> np.ndarray:
    """Difference of Gaussians at bullet scale. Matched-filter for a dark
    disk: sigma1 ~ r_b/2 (inner), sigma2 ~ r_b (outer). Returns |DoG|."""
    f = g.astype(np.float32)
    s1 = max(0.8, r_b / 2.0)
    s2 = max(1.6, r_b)
    return np.abs(cv2.GaussianBlur(f, (0, 0), s1) - cv2.GaussianBlur(f, (0, 0), s2))


def feat_gabor(g: np.ndarray, r_b: float,
               orientations: tuple[int, ...] = (0, 45, 90, 135)) -> np.ndarray:
    """Rotation-invariant Gabor texture energy, summed over 4 orientations.
    sigma and lambda scaled to bullet radius."""
    f = g.astype(np.float32)
    sigma = max(1.0, 0.5 * r_b)
    lambd = max(2.0, 2.0 * r_b)
    ksize = int(6 * sigma) | 1
    accum = np.zeros_like(f)
    for theta_deg in orientations:
        kern = cv2.getGaborKernel(
            (ksize, ksize), sigma=sigma, theta=np.deg2rad(theta_deg),
            lambd=lambd, gamma=0.5, psi=0, ktype=cv2.CV_32F,
        )
        resp = cv2.filter2D(f, ddepth=cv2.CV_32F, kernel=kern)
        accum += np.abs(resp)
    return accum


def feat_laplacian_band(g: np.ndarray, r_b: float) -> np.ndarray:
    """Laplacian-pyramid band-pass at the level matching r_b. Approximated
    here as DoG with sigma1=sigma_inner, sigma2=2*sigma_inner where
    sigma_inner chosen so the peak response is at spatial frequency ~1/r_b."""
    f = g.astype(np.float32)
    s_inner = max(0.8, r_b / 2.5)
    s_outer = 2.0 * s_inner
    blur_in = cv2.GaussianBlur(f, (0, 0), s_inner)
    blur_out = cv2.GaussianBlur(f, (0, 0), s_outer)
    return np.abs(blur_in - blur_out)


# ---------------------------------------------------------------------------
# Detection on feature maps
# ---------------------------------------------------------------------------
def normalize_to_u8(fmap: np.ndarray) -> np.ndarray:
    lo, hi = float(fmap.min()), float(fmap.max())
    if hi - lo < 1e-6:
        return np.zeros_like(fmap, dtype=np.uint8)
    arr = ((fmap - lo) / (hi - lo) * 255.0).astype(np.uint8)
    # CLAHE spreads the long-tailed distribution (most pixels ~low, holes ~high).
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(arr)


def detect_circles(fmap: np.ndarray, r_b: float,
                   param2: float = 0.80) -> np.ndarray | None:
    """HoughCircles (HOUGH_GRADIENT_ALT) on a feature map. Radius bounded to
    [0.7, 1.3] * r_b. minDist = 1.5 * r_b to avoid merging adjacent holes."""
    u8 = normalize_to_u8(fmap)
    min_r = max(3, int(0.7 * r_b))
    max_r = max(min_r + 2, int(1.3 * r_b))
    min_dist = max(int(1.5 * r_b), min_r + 1)
    return cv2.HoughCircles(
        u8, cv2.HOUGH_GRADIENT_ALT, dp=1.5,
        minDist=min_dist, param1=80, param2=param2,
        minRadius=min_r, maxRadius=max_r,
    )


def nms_circles(circles_per_feature: list[np.ndarray | None],
                min_dist: float) -> list[tuple[float, float, float]]:
    """Candidate-union + NMS. Each entry in circles_per_feature is a (N, 3)
    array of (x, y, r). Dedup by min_dist; on conflict keep the detection
    from the feature that produced fewer false-positive-looking circles
    (use radius closest to expected as a proxy)."""
    # Each detection tagged with source feature for tie-breaking.
    all_c = []
    for feat_idx, c in enumerate(circles_per_feature):
        if c is None:
            continue
        for row in c[0]:
            all_c.append((float(row[0]), float(row[1]), float(row[2]), feat_idx))
    # Sort by source: prefer detections from features that fire less
    # (less noise) — but here we just keep them in arrival order; the
    # cross-feature dedup is what matters.
    kept: list[tuple[float, float, float]] = []
    for cx, cy, r, _src in all_c:
        if all(math.hypot(cx - kx, cy - ky) > min_dist
               for (kx, ky, _) in kept):
            kept.append((cx, cy, r))
    return kept


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------
def save_feature_png(fmap: np.ndarray, path: Path) -> None:
    cv2.imwrite(str(path), normalize_to_u8(fmap))


def draw_detections(img: np.ndarray, centers: list[tuple[float, float, float]],
                    center: tuple[float, float], pmm: float,
                    target_type: str) -> np.ndarray:
    out = img.copy()
    cx, cy = center
    # Draw ISSF rings faintly.
    for ring in (1, 5, 7, 10):
        r_mm = ISSF_RADII_MM[target_type][10 - ring]
        r_px = int(r_mm * pmm)
        cv2.circle(out, (int(cx), int(cy)), r_px, (0, 200, 0), 1)
    # Draw detections.
    for (x, y, r) in centers:
        cv2.circle(out, (int(x), int(y)), int(r), (0, 0, 255), 2)
        cv2.circle(out, (int(x), int(y)), 2, (0, 255, 255), -1)
    return out


# ---------------------------------------------------------------------------
# Per-image runner
# ---------------------------------------------------------------------------
def run_one(img_id: int, target_type: str = DEFAULT_TARGET) -> dict[str, Any]:
    img_path = TRAIN_DIR / f"{img_id}.jpg"
    img = load_exif_normalized(img_path)
    prep = prepare_target(img, target_type)
    crop, gray_ext, mask = prep["crop"], prep["gray_extracted"], prep["mask"]
    center, pmm = prep["center"], prep["pmm"]

    # Compute features on the UNMASKED crop. Masking before feature
    # computation creates a false spike at the mask boundary (gray→0
    # transition) that dominates the feature map. We zero out feature
    # values OUTSIDE the target mask AFTER computation instead.
    gray_full = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    meta = metadata_for(img_id)
    caliber = meta.get("caliber", "22lr")
    if isinstance(caliber, list):
        caliber = caliber[0]
    bullet_d_mm = CALIBER_DIAMETER_MM.get(caliber, 5.7)
    bullet_radius_px = (bullet_d_mm / 2.0) * pmm
    true_hits = list(meta.get("hits", []))
    n_true = len(true_hits)

    # Compute each feature.
    feats = {
        "local_std": feat_local_std(gray_full, bullet_radius_px),
        "dog":       feat_dog(gray_full, bullet_radius_px),
        "gabor":     feat_gabor(gray_full, bullet_radius_px),
        "laplacian": feat_laplacian_band(gray_full, bullet_radius_px),
    }
    # Zero out feature values OUTSIDE the target mask.
    for name in feats:
        feats[name] = np.where(mask > 0, feats[name], 0.0)
    for name, fmap in feats.items():
        save_feature_png(fmap, FEATURE_DIR / f"{img_id:02d}_{name}.png")

    # HoughCircles on each.
    detections: dict[str, list[tuple[float, float, float]]] = {}
    for name, fmap in feats.items():
        circles = detect_circles(fmap, bullet_radius_px)
        if circles is None:
            detections[name] = []
        else:
            detections[name] = [(float(c[0]), float(c[1]), float(c[2]))
                                 for c in circles[0]]
        # Per-feature detections viz.
        viz = draw_detections(prep["extracted"], detections[name],
                              center, pmm, target_type)
        cv2.putText(viz, f"{name}: {len(detections[name])} hits",
                    (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.imwrite(str(FEATURE_DIR / f"{img_id:02d}_{name}_detections.png"), viz)

    # FUSION: candidate-union + NMS. Use a smaller min_dist than individual
    # HoughCircles (0.7 * r_b) so adjacent overlapping holes survive — the
    # cross-feature union already filters most false positives.
    fused = nms_circles(
        [np.array([[c] for c in detections[n]]) if detections[n] else None
         for n in feats],
        min_dist=0.7 * bullet_radius_px,
    )
    fused_viz = draw_detections(prep["extracted"], fused, center, pmm, target_type)
    cv2.putText(fused_viz, f"FUSED: {len(fused)} hits",
                (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    cv2.imwrite(str(FEATURE_DIR / f"{img_id:02d}_fused_detections.png"), fused_viz)

    # Score each feature's predictions using Stage 5 line-break rule.
    bullseye = center
    black_r_mm = ISSF_RADII_MM[target_type][10 - ISSF_BLACK_OUTER_RING[target_type]]
    scoring_radius_px = ISSF_RADII_MM[target_type][9] * pmm  # ring 1 outer
    per_feature_metrics: dict[str, dict[str, Any]] = {}
    for name, dets in list(detections.items()) + [("fused", fused)]:
        centers_only = [(x, y) for (x, y, _) in dets]
        scores = _stage5_score(centers_only, bullseye, scoring_radius_px,
                                bullet_radius_px)
        jac = multiset_jaccard(scores, true_hits)
        per_feature_metrics[name] = {
            "n_pred": len(dets), "n_true": n_true,
            "count_err": abs(len(dets) - n_true),
            "jaccard": jac,
            "scores": scores,
        }

    return {
        "img_id": img_id,
        "caliber": caliber,
        "bullet_d_mm": bullet_d_mm,
        "bullet_radius_px": float(bullet_radius_px),
        "pmm": float(pmm),
        "n_true": n_true,
        "true_hits": true_hits,
        "per_feature": per_feature_metrics,
    }


def main():
    train_ids = [1, 4, 6, 10, 12, 19, 21, 29, 31, 46]
    results = []
    print(f"{'id':>3}  {'cal':>8}  {'rb_px':>6}  "
          f"{'local_std':>10}  {'dog':>10}  {'gabor':>10}  "
          f"{'lap':>10}  {'fused':>10}  (n_pred/jac)");
    for img_id in train_ids:
        try:
            r = run_one(img_id, target_type=DEFAULT_TARGET)
            results.append(r)
            row = (f"{img_id:>3}  {r['caliber']:>8}  "
                   f"{r['bullet_radius_px']:>6.1f}  ")
            for name in ("local_std", "dog", "gabor", "laplacian", "fused"):
                m = r["per_feature"][name]
                row += f"{m['n_pred']:>2}/{m['jaccard']:.2f}  "
            row += f"  | n_true={r['n_true']}"
            print(row, flush=True)
        except Exception as e:
            print(f"{img_id}: EXCEPTION: {e}", flush=True)
            import traceback; traceback.print_exc()

    out_path = FEATURE_DIR / "feature_summary.json"
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2)

    # Aggregate.
    print("\n=== Aggregate (10 train images) ===")
    for name in ("local_std", "dog", "gabor", "laplacian", "fused"):
        jacs = [r["per_feature"][name]["jaccard"] for r in results
                if "per_feature" in r]
        cnts = [r["per_feature"][name]["count_err"] for r in results
                if "per_feature" in r]
        if jacs:
            print(f"  {name:>12}: mean_jac={sum(jacs)/len(jacs):.3f}  "
                  f"perfect_count={sum(1 for c in cnts if c == 0)}/{len(cnts)}")
    print(f"\nResults → {out_path}")


if __name__ == "__main__":
    main()
