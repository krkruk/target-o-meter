"""Probe: ISSF concentric-ring detection + target extraction.

Pipeline:
    1. EXIF-normalize load (PIL).
    2. Reuse cv.detect._stage1_localize for bbox crop.
    3. Adaptive threshold (small block, ring-stroke tuned).
    4. RETR_TREE contours → fitEllipseAMS per contour.
    5. Filter ellipses by axis-ratio consistency (shared perspective).
    6. Assign ring indices via the equidistant-radius prior.
    7. Derive bullseye + px_per_mm + outermost-ring ellipse.
    8. Build target mask (1-ring filled). Extract target on neutral bg.
    9. Optional: perspective rectify to canonical Air Pistol (170 mm).

Outputs land in resources/train/intermediate/<id>_<stage>.png.
A summary JSON is written alongside.

Run:
    uv run python cv/tmp/probe_ring_calibration.py
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
from detect import _stage1_localize, LOCATOR_LONG_SIDE, TARGET_CARD_MM  # noqa: E402

TRAIN_DIR = _REPO / "resources" / "train"
OUT_DIR = _REPO / "resources" / "train" / "intermediate"
META_PATH = _REPO / "resources" / "paper_targets" / "metadata.yml"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ISSF ring geometry (radii in mm). Verified from Wikipedia via survey.
# Air Pistol: 10-ring radius = 5.75 mm, +8 mm per lower ring.
# Precision: 10-ring radius = 25 mm, +25 mm per lower ring.
ISSF_RADII_MM = {
    "air_pistol": [5.75 + 8.0 * i for i in range(10)],         # rings 10..1
    "precision_pistol": [25.0 + 25.0 * i for i in range(10)],
}
DEFAULT_TARGET = "air_pistol"


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------
def load_exif_normalized(path: Path) -> np.ndarray:
    """Load JPEG respecting EXIF orientation. Returns BGR ndarray for cv2."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
    arr = np.asarray(im)[:, :, ::-1].copy()  # RGB → BGR
    return arr


def metadata_caliber(img_id: int) -> str | list[str]:
    """Read caliber for an image id from metadata.yml. Lazy import."""
    import yaml
    with open(META_PATH) as fh:
        meta = yaml.safe_load(fh)
    for entry in meta.get("images", []):
        if entry.get("id") == img_id:
            return entry.get("caliber", "22lr")
    return "22lr"


# ---------------------------------------------------------------------------
# Stage A — localize + crop (reuse existing helpers)
# ---------------------------------------------------------------------------
def localize_and_crop(img: np.ndarray, target_type: str) -> tuple[np.ndarray, dict]:
    h0, w0 = img.shape[:2]
    long_side = max(h0, w0)
    scale = LOCATOR_LONG_SIDE / long_side if long_side > LOCATOR_LONG_SIDE else 1.0
    locator = cv2.resize(img, (int(w0 * scale), int(h0 * scale)),
                         interpolation=cv2.INTER_AREA)
    bbox, meta, fail = _stage1_localize(locator, target_type)
    if fail:
        return img, {"method": "raw_fallback", "scale": scale, "bbox": None}
    x, y, bw, bh = bbox
    sx, sy = w0 / locator.shape[1], h0 / locator.shape[0]
    bx0, by0 = int(x * sx), int(y * sy)
    bx1, by1 = int((x + bw) * sx), int((y + bh) * sy)
    crop = img[by0:by1, bx0:bx1]
    return crop, {
        "method": "bbox_crop", "scale": scale,
        "bbox_orig": [bx0, by0, bx1 - bx0, by1 - bh0 if False else by1 - by0],
        "aspect": meta.get("aspect"), "fill": meta.get("fill"),
    }


# ---------------------------------------------------------------------------
# Stage B — adaptive threshold tuned for ring strokes
# ---------------------------------------------------------------------------
def ring_stroke_threshold(gray: np.ndarray) -> np.ndarray:
    """Adaptive threshold inverted so dark ring strokes = 255.

    blockSize is tuned to ring stroke width: phone photos at ~10 px/mm give
    ~3 px strokes, so blockSize ~ 2*3+1 = 7. We auto-pick from image size to
    stay robust across resolutions.
    """
    h, w = gray.shape
    # estimate stroke width: assume ~10 px/mm at typical phone scale; ISSF
    # ring stroke is ~0.3 mm → ~3 px. blockSize = 2*3+1 = 7. Round up to be
    # safe against higher-resolution crops.
    block = max(7, (max(h, w) // 200) | 1)
    if block < 7:
        block = 7
    binv = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV,
        blockSize=block, C=3,
    )
    # Light denoise: 1x1 open removes single-pixel speckle without breaking
    # thin ring strokes.
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
    binv = cv2.morphologyEx(binv, cv2.MORPH_OPEN, k, iterations=1)
    return binv


# ---------------------------------------------------------------------------
# Stage C — RETR_TREE contours + fitEllipseAMS
# ---------------------------------------------------------------------------
def extract_ring_ellipses(binv: np.ndarray, min_points: int = 30) -> list[dict]:
    """Return list of {center, axes, angle, area, depth, n_pts} for ellipses
    fit to tree-nested contours. Filters:
      - >=min_points
      - axis ratio in [0.45, 1.0] (allow perspective foreshortening up to ~2:1)
      - major axis at least 1% of image short side
      - area/perimeter > 5 (rejects thin spurious contours that wrap around
        the target once but have almost no width — paper edges, shadows,
        long fiber tears — these inflate major-axis estimates).
    """
    h, w = binv.shape
    min_major = 0.01 * min(h, w)
    contours, hierarchy = cv2.findContours(binv, cv2.RETR_TREE,
                                            cv2.CHAIN_APPROX_NONE)
    if hierarchy is None:
        return []
    hierarchy = hierarchy[0]

    ellipses = []
    for i, c in enumerate(contours):
        if len(c) < max(5, min_points):
            continue
        perim = float(cv2.arcLength(c, True))
        if perim < 1.0:
            continue
        area = float(cv2.contourArea(c))
        # Reject thin/spurious contours: real rings have area/perimeter > ~10
        # (annulus with stroke_width ~3px → ratio ~3; filled blob → much higher).
        # A paper-edge contour wrapping the target has ratio < 2.
        if area / perim < 5.0:
            continue
        rect = cv2.fitEllipseAMS(c)
        (cx, cy), (major, minor), angle = rect
        if major < minor:
            major, minor = minor, major
        if major < min_major:
            continue
        ratio = minor / major if major > 0 else 0.0
        if ratio < 0.45 or ratio > 1.0:
            continue
        ellipses.append({
            "center": (float(cx), float(cy)),
            "axes": (float(major), float(minor)),
            "angle": float(angle),
            "ratio": float(ratio),
            "area": area,
            "perimeter": perim,
            "n_pts": int(len(c)),
            "depth": int(_contour_depth(hierarchy, i)),
        })
    return ellipses


def _contour_depth(hierarchy: np.ndarray, idx: int) -> int:
    """Count nesting depth by walking parent chain."""
    d = 0
    cur = idx
    while cur >= 0:
        parent = int(hierarchy[cur][3])
        if parent < 0:
            break
        d += 1
        cur = parent
        if d > 20:  # safety
            break
    return d


# ---------------------------------------------------------------------------
# Stage D — assign ring numbers via equidistant-radius prior
# ---------------------------------------------------------------------------
def assign_ring_numbers(ellipses: list[dict], target_type: str) -> list[dict]:
    """For each ellipse, find the ring index k ∈ {1..10} and px_per_mm that
    best fits the equidistant-radius prior, given a *common* center estimate.

    Strategy:
      1. Estimate common center as the median of all ellipse centers weighted
         by area (large rings dominate).
      2. For each candidate "outermost detected ring" k_outer ∈ {1..10}:
           px_per_mm_candidate = outermost_major / (2 * ISSF_radii[k_outer-1])
         Then for every other ellipse, predict its expected major axis under
         each ring index k_inner ∈ {1..10} and pick the k with smallest
         residual. Count inliers (residual < 10% of predicted).
      3. Keep the (k_outer, px_per_mm) with the most inliers.

    Returns ellipses annotated with assigned 'ring' (1..10) or None.
    """
    if not ellipses:
        return ellipses
    radii = ISSF_RADII_MM[target_type]  # rings 10..1, len 10

    # Common-center estimate (area-weighted median).
    areas = np.array([e["area"] for e in ellipses], dtype=float)
    weights = areas / (areas.sum() + 1e-9)
    cxs = np.array([e["center"][0] for e in ellipses])
    cys = np.array([e["center"][1] for e in ellipses])
    cx_est = float(np.sum(weights * cxs))
    cy_est = float(np.sum(weights * cys))

    majors = np.array([e["axes"][0] for e in ellipses], dtype=float)
    # Sort ellipses by major axis descending (outermost first).
    order = np.argsort(-majors)

    best = {"score": -1, "px_per_mm": None, "k_outer": None, "assign": None}
    for k_outer in range(1, 11):  # outermost detected ring = k_outer (1..10)
        outer_idx = int(order[0])
        outer_major = float(majors[outer_idx])
        expected_outer_radius_mm = radii[k_outer - 1]   # radii is indexed 10..1
        px_per_mm = outer_major / (2.0 * expected_outer_radius_mm)
        if px_per_mm <= 0 or px_per_mm > 200:
            continue

        # For each ellipse, find the ring index whose predicted major best matches.
        assign = [None] * len(ellipses)
        inliers = 0
        for i, e in enumerate(ellipses):
            major_i = e["axes"][0]
            best_k, best_resid = None, float("inf")
            for k in range(1, 11):
                pred = 2.0 * radii[k - 1] * px_per_mm
                resid = abs(major_i - pred) / max(pred, 1.0)
                if resid < best_resid:
                    best_resid = resid
                    best_k = k
            if best_resid < 0.10:
                assign[i] = best_k
                inliers += 1
            elif best_resid < 0.20:
                assign[i] = best_k  # tentative

        # Score: #inliers, with small bonus for higher k_outer (more rings detected).
        score = inliers
        if score > best["score"]:
            best = {
                "score": int(inliers), "px_per_mm": float(px_per_mm),
                "k_outer": int(k_outer), "assign": assign,
                "cx": cx_est, "cy": cy_est,
            }

    if best["assign"] is None:
        for e in ellipses:
            e["ring"] = None
        return ellipses

    for i, e in enumerate(ellipses):
        e["ring"] = best["assign"][i]
        e["expected_major"] = (2.0 * radii[(e["ring"] or 1) - 1] * best["px_per_mm"]
                                if e["ring"] else None)
    # Stash calibration on first ellipse for caller access.
    ellipses[0]["_calibration"] = {
        "cx": best["cx"], "cy": best["cy"],
        "px_per_mm": best["px_per_mm"],
        "k_outer": best["k_outer"], "inliers": best["score"],
        "n_ellipses": len(ellipses),
    }
    return ellipses


# ---------------------------------------------------------------------------
# Stage E — build target mask + extract
# ---------------------------------------------------------------------------
def build_ring_mask(shape: tuple[int, int], ellipses: list[dict]) -> np.ndarray | None:
    """Fill the outermost assigned ring (k=1) as the target mask. If no ring
    was assigned k=1, use the largest major axis among assigned rings."""
    if not ellipses or not ellipses[0].get("_calibration"):
        return None
    assigned = [e for e in ellipses if e.get("ring") is not None]
    if not assigned:
        return None
    outer = min(assigned, key=lambda e: e["ring"])  # ring 1 = outermost
    mask = np.zeros(shape, dtype=np.uint8)
    (cx, cy) = outer["center"]
    (major, minor) = outer["axes"]
    cv2.ellipse(mask, (int(cx), int(cy)), (int(major / 2), int(minor / 2)),
                int(outer["angle"]), 0, 360, 255, -1)
    return mask


def extract_target(img: np.ndarray, mask: np.ndarray,
                   bg_color: tuple[int, int, int] = (240, 240, 240)) -> np.ndarray:
    """Apply mask; replace masked-out pixels with bg_color."""
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


def draw_ellipses(img: np.ndarray, ellipses: list[dict],
                  color_by_ring: bool = True) -> np.ndarray:
    out = img.copy()
    for e in ellipses:
        (cx, cy) = e["center"]
        (major, minor) = e["axes"]
        ring = e.get("ring")
        if color_by_ring and ring:
            col = RAINBOW[(ring - 1) % len(RAINBOW)]
        else:
            col = (0, 255, 255)  # yellow = unassigned
        cv2.ellipse(out, (int(cx), int(cy)), (int(major / 2), int(minor / 2)),
                    int(e["angle"]), 0, 360, col, 2)
        if ring:
            cv2.putText(out, str(ring), (int(cx) - 6, int(cy) + 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
        else:
            cv2.circle(out, (int(cx), int(cy)), 3, col, -1)
    return out


def draw_center_and_calibration(img: np.ndarray, calib: dict | None) -> np.ndarray:
    out = img.copy()
    if not calib:
        return out
    cx, cy = int(calib["cx"]), int(calib["cy"])
    cv2.drawMarker(out, (cx, cy), (0, 255, 0), cv2.MARKER_CROSS, 30, 2)
    cv2.putText(out, f"px/mm={calib['px_per_mm']:.2f} k_out={calib['k_outer']}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    return out


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_one(img_id: int, target_type: str = DEFAULT_TARGET) -> dict[str, Any]:
    img_path = TRAIN_DIR / f"{img_id}.jpg"
    img = load_exif_normalized(img_path)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_01_original.jpg"), img)

    # Stage A — crop.
    crop, loc_meta = localize_and_crop(img, target_type)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_02_crop.png"), crop)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # Stage B — threshold.
    binv = ring_stroke_threshold(gray)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_03_threshold.png"), binv)

    # Stage C — contours + ellipses.
    ellipses = extract_ring_ellipses(binv)
    # Pre-assignment view: all ellipses yellow.
    pre = draw_ellipses(crop, ellipses, color_by_ring=False)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_04a_all_ellipses.png"), pre)

    # Stage D — assign ring numbers.
    ellipses = assign_ring_numbers(ellipses, target_type)
    calib = ellipses[0].get("_calibration") if ellipses else None
    post = draw_ellipses(crop, ellipses, color_by_ring=True)
    post = draw_center_and_calibration(post, calib)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_04b_rings_assigned.png"), post)

    # Stage E — mask + extract.
    mask = build_ring_mask(gray.shape, ellipses)
    if mask is not None:
        cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_05_target_mask.png"), mask)
        masked_crop = cv2.bitwise_and(crop, crop, mask=mask)
        cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_06_masked_crop.png"), masked_crop)
        extracted = extract_target(crop, mask)
        cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_07_target_extracted.png"), extracted)
    else:
        cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_05_target_mask.png"),
                    np.zeros_like(gray))

    return {
        "img_id": img_id,
        "target_type": target_type,
        "crop_shape": list(crop.shape),
        "loc_method": loc_meta.get("method"),
        "loc_aspect": loc_meta.get("aspect"),
        "n_ellipses_total": len(ellipses),
        "n_ellipses_assigned": sum(1 for e in ellipses if e.get("ring")),
        "calibration": calib,
    }


def main():
    train_ids = [1, 4, 6, 10, 12, 19, 21, 29, 31, 46]
    results = []
    for img_id in train_ids:
        print(f"=== Image {img_id}.jpg ===", flush=True)
        try:
            r = run_one(img_id, target_type=DEFAULT_TARGET)
            cal = r.get("calibration") or {}
            print(f"  ellipses: {r['n_ellipses_total']} total, "
                  f"{r['n_ellipses_assigned']} assigned  |  "
                  f"px/mm={cal.get('px_per_mm')}, k_outer={cal.get('k_outer')}, "
                  f"inliers={cal.get('inliers')}", flush=True)
            results.append(r)
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
            results.append({"img_id": img_id, "error": str(e)})

    out_path = OUT_DIR / "ring_calibration_results.json"
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nResults → {out_path}")
    print(f"Intermediates → {OUT_DIR}/<id>_01..07*.png")


if __name__ == "__main__":
    main()
