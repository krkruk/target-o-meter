"""Probe v2: ISSF target calibration via black-disc anchor + radial-profile
ring detection.

The earlier contour-based probe confused the black disc (filled blob) with
ring strokes and gave wildly inconsistent px_per_mm. This version uses the
strongest signal in the image — the BLACK DISC boundary (rings 7-10 form a
solid black region on Air Pistol targets; rings 5-10 on Precision) — to
anchor calibration, then propagates outward via known ISSF ring geometry.

Pipeline:
    1. EXIF-normalize load.
    2. Reuse cv.detect._stage1_localize for bbox crop.
    3. Find black disc: Otsu threshold → largest dark blob → fitEllipseAMS.
       This gives (bullseye, black_disc_axes, black_disc_angle).
    4. Match black disc to ISSF geometry:
         Air Pistol: black disc = rings 7-10, outer radius = 29.75mm
         Precision:  black disc = rings 5-10, outer radius = 50+25*5 = 175mm? no
                     actually precision black disc = rings 5-10 → outer radius = 25+25*5 = 150mm
       → derive px_per_mm.
    5. OPTIONAL refine via radial profile: look for ring-stroke minima at
       predicted ISSF radii (ring 1 = 77.75mm, etc.); if found, fit a
       correction factor.
    6. Build mask: filled ellipse at ring 1 outer radius (or ring 6 if the
       outer rings are not in frame).
    7. Extract target on neutral background.

Outputs to resources/train/intermediate/<id>_<stage>.png
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
from detect import _stage1_localize, LOCATOR_LONG_SIDE  # noqa: E402

TRAIN_DIR = _REPO / "resources" / "train"
OUT_DIR = _REPO / "resources" / "train" / "intermediate"
META_PATH = _REPO / "resources" / "paper_targets" / "metadata.yml"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Verified ISSF geometry (mm). Rings 10..1, radii from bullseye.
ISSF_RADII_MM = {
    "air_pistol":     [5.75 + 8.0 * i for i in range(10)],  # ring 10..1
    "precision_pistol": [25.0 + 25.0 * i for i in range(10)],
}
# Outermost black-portion ring (inclusive): rings 7..10 on air pistol,
# rings 5..10 on precision pistol per ISSF spec (survey §1.1 / §1.2).
ISSF_BLACK_OUTER_RING = {"air_pistol": 7, "precision_pistol": 5}
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
    for entry in meta.get("images", []):
        if entry.get("id") == img_id:
            return entry
    return {}


# ---------------------------------------------------------------------------
# Stage A — localize + crop (reuse existing)
# ---------------------------------------------------------------------------
def localize_and_crop(img: np.ndarray, target_type: str) -> tuple[np.ndarray, dict]:
    h0, w0 = img.shape[:2]
    long_side = max(h0, w0)
    scale = LOCATOR_LONG_SIDE / long_side if long_side > LOCATOR_LONG_SIDE else 1.0
    locator = cv2.resize(img, (int(w0 * scale), int(h0 * scale)),
                         interpolation=cv2.INTER_AREA)
    bbox, meta, fail = _stage1_localize(locator, target_type)
    if fail:
        return img, {"method": "raw_fallback"}
    x, y, bw, bh = bbox
    sx, sy = w0 / locator.shape[1], h0 / locator.shape[0]
    bx0, by0 = int(x * sx), int(y * sy)
    bx1, by1 = int((x + bw) * sx), int((y + bh) * sy)
    return img[by0:by1, bx0:bx1], {"method": "bbox_crop",
                                    "bbox_orig": [bx0, by0, bx1 - bx0, by1 - by0]}


# ---------------------------------------------------------------------------
# Stage B — find black disc (anchor for calibration)
# ---------------------------------------------------------------------------
def find_black_disc(gray: np.ndarray, target_type: str) -> dict:
    """Locate the ISSF black disc (rings 7-10 on Air Pistol, 5-10 on Precision).

    Returns {center, axes, angle, area, px_per_mm, outer_ring}.

    The black disc is the largest connected dark region. We use Otsu to
    threshold, morphology to clean, then fitEllipseAMS on the largest
    component. The disc's outer boundary = ring 7 (Air Pistol) or ring 5
    (Precision), giving us a hard metric anchor.
    """
    h, w = gray.shape
    # Strong blur before Otsu so ring lines (also dark) don't fragment the disc.
    blur = cv2.GaussianBlur(gray, (0, 0), sigmaX=3.0)
    _, binv = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # Close small gaps (ring strokes inside the disc).
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    binv = cv2.morphologyEx(binv, cv2.MORPH_CLOSE, k, iterations=2)
    # Open to remove small noise outside the disc.
    k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binv = cv2.morphologyEx(binv, cv2.MORPH_OPEN, k_open, iterations=1)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(binv, connectivity=8)
    if n <= 1:
        return {"found": False}
    areas = stats[:, cv2.CC_STAT_AREA].copy()
    areas[0] = 0
    largest = int(np.argmax(areas))
    if areas[largest] < 0.01 * h * w:
        return {"found": False}

    # Build a mask of just the largest blob.
    mask = (labels == largest).astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return {"found": False}
    c = max(contours, key=cv2.contourArea)
    if len(c) < 5:
        return {"found": False}

    (cx, cy), (major, minor), angle = cv2.fitEllipseAMS(c)
    if major < minor:
        major, minor = minor, major
    # Sanity: should be roughly circular (target on table, mostly top-down).
    ratio = minor / major if major > 0 else 0.0
    if ratio < 0.5:
        return {"found": False, "reason": f"low ratio {ratio:.2f}"}

    outer_ring = ISSF_BLACK_OUTER_RING[target_type]   # ring 7 air, ring 5 prec
    black_radius_mm = ISSF_RADII_MM[target_type][10 - outer_ring]  # index for ring 7 → radii[3]
    # major axis corresponds to outer diameter of black disc
    px_per_mm = (major / 2.0) / black_radius_mm
    return {
        "found": True,
        "center": (float(cx), float(cy)),
        "axes": (float(major), float(minor)),
        "angle": float(angle),
        "ratio": float(ratio),
        "area": float(cv2.contourArea(c)),
        "outer_ring": int(outer_ring),
        "black_radius_mm": float(black_radius_mm),
        "px_per_mm": float(px_per_mm),
        "mask": mask,
    }


# ---------------------------------------------------------------------------
# Stage C — radial profile to refine outermost-in-frame ring
# ---------------------------------------------------------------------------
def radial_profile(gray: np.ndarray, center: tuple[float, float],
                   axes: tuple[float, float], angle: float,
                   max_radius_px: float, n_bins: int = 360) -> np.ndarray:
    """Compute mean intensity per elliptical-radius bin. The 'radius' here
    is normalized: 1.0 = on the ellipse boundary. We rescale to pixels by
    multiplying by the semi-major axis.

    Returns profile of length n_bins covering [0, max_radius_px].
    """
    h, w = gray.shape
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = center
    major, minor = axes
    if major <= 0 or minor <= 0:
        return np.zeros(n_bins)
    theta = np.deg2rad(angle)
    # Rotate point into ellipse-aligned frame.
    dx = xx - cx
    dy = yy - cy
    dxr =  dx * np.cos(theta) + dy * np.sin(theta)
    dyr = -dx * np.sin(theta) + dy * np.cos(theta)
    # Elliptical radius: on the ellipse boundary, this equals 1.
    a, b = major / 2.0, minor / 2.0
    r_norm = np.sqrt((dxr / a) ** 2 + (dyr / b) ** 2)
    r_px = r_norm * a   # convert back to major-axis pixels for indexing
    mask = r_px < max_radius_px
    r_int = np.clip((r_px * n_bins / max_radius_px).astype(np.int32), 0, n_bins - 1)
    flat_gray = gray.astype(np.float32).ravel()
    flat_r = r_int.ravel()
    flat_mask = mask.ravel()
    flat_gray_m = flat_gray[flat_mask]
    flat_r_m = flat_r[flat_mask]
    num = np.bincount(flat_r_m, weights=flat_gray_m, minlength=n_bins)
    cnt = np.bincount(flat_r_m, minlength=n_bins)
    cnt = np.maximum(cnt, 1)
    return num / cnt


def detect_rings_in_profile(profile: np.ndarray, px_per_mm: float,
                             target_type: str,
                             max_radius_px: float) -> list[dict]:
    """Look for ring-stroke minima at predicted ISSF ring radii. Returns list
    of {ring, predicted_r_px, observed_min_r_px, observed_min_intensity,
    snr_db} for each ring whose predicted location is within frame and whose
    local minimum is detectable.
    """
    n_bins = len(profile)
    radii = ISSF_RADII_MM[target_type]  # rings 10..1
    found = []
    for k in range(10, 0, -1):  # 10 → 1
        r_mm = radii[10 - k]
        r_px_pred = r_mm * px_per_mm
        if r_px_pred >= max_radius_px:
            continue
        bin_pred = int(r_px_pred * n_bins / max_radius_px)
        # Search ±15px window for the local minimum.
        win_px = 15
        win_bins = max(3, int(win_px * n_bins / max_radius_px))
        lo = max(0, bin_pred - win_bins)
        hi = min(n_bins, bin_pred + win_bins + 1)
        window = profile[lo:hi]
        if len(window) < 3:
            continue
        local_min_idx = lo + int(np.argmin(window))
        local_min_val = float(profile[local_min_idx])
        observed_r_px = local_min_idx * max_radius_px / n_bins

        # SNR: contrast between local minimum and a slightly wider context.
        ctx_lo = max(0, bin_pred - 3 * win_bins)
        ctx_hi = min(n_bins, bin_pred + 3 * win_bins + 1)
        ctx = profile[ctx_lo:ctx_hi]
        ctx_mean = float(ctx.mean()) if len(ctx) > 0 else local_min_val
        snr = (ctx_mean - local_min_val) / max(np.std(ctx), 1.0)

        found.append({
            "ring": int(k),
            "predicted_r_px": float(r_px_pred),
            "observed_r_px": float(observed_r_px),
            "observed_min_intensity": local_min_val,
            "snr": float(snr),
        })
    return found


# ---------------------------------------------------------------------------
# Stage D — build target mask + extract
# ---------------------------------------------------------------------------
def build_target_mask(shape: tuple[int, int], disc: dict,
                       target_type: str, px_per_mm: float,
                       outermost_in_frame_ring: int) -> np.ndarray:
    """Fill an ellipse at the outermost-in-frame ring's predicted boundary."""
    cx, cy = disc["center"]
    major_a, minor_a = disc["axes"]
    angle = disc["angle"]
    # Predicted outer ring radius in mm:
    r_mm = ISSF_RADII_MM[target_type][10 - outermost_in_frame_ring]
    # Scale disc axes by (r_outer / r_black_disc):
    black_r_mm = ISSF_RADII_MM[target_type][10 - disc["outer_ring"]]
    scale = r_mm / black_r_mm
    major_out = major_a * scale
    minor_out = minor_a * scale
    mask = np.zeros(shape, dtype=np.uint8)
    cv2.ellipse(mask, (int(cx), int(cy)),
                (int(major_out / 2), int(minor_out / 2)),
                int(angle), 0, 360, 255, -1)
    return mask


def extract_target(img: np.ndarray, mask: np.ndarray,
                   bg_color: tuple[int, int, int] = (240, 240, 240)) -> np.ndarray:
    out = img.copy()
    inv = cv2.bitwise_not(mask)
    bg = np.full_like(img, bg_color, dtype=np.uint8)
    out = cv2.bitwise_and(out, out, mask=mask)
    bg = cv2.bitwise_and(bg, bg, mask=inv)
    return cv2.add(out, bg)


# ---------------------------------------------------------------------------
# Visualization helpers
# ---------------------------------------------------------------------------
RAINBOW = [
    (0, 0, 255), (0, 127, 255), (0, 255, 255), (0, 255, 0),
    (255, 255, 0), (255, 127, 0), (255, 0, 0), (255, 0, 127),
    (255, 0, 255), (127, 0, 255),
]


def draw_black_disc(img: np.ndarray, disc: dict) -> np.ndarray:
    out = img.copy()
    cv2.ellipse(out, (int(disc["center"][0]), int(disc["center"][1])),
                (int(disc["axes"][0] / 2), int(disc["axes"][1] / 2)),
                int(disc["angle"]), 0, 360, (0, 255, 0), 3)
    cv2.drawMarker(out, (int(disc["center"][0]), int(disc["center"][1])),
                   (0, 255, 0), cv2.MARKER_CROSS, 30, 2)
    cv2.putText(out,
                f"px/mm={disc['px_per_mm']:.2f}  black_disc_ring={disc['outer_ring']}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    return out


def draw_predicted_rings(img: np.ndarray, disc: dict, target_type: str,
                          px_per_mm: float,
                          detected_rings: list[dict]) -> np.ndarray:
    out = img.copy()
    cx, cy = disc["center"]
    major, minor = disc["axes"]
    angle = disc["angle"]
    a, b = major / 2.0, minor / 2.0
    radii = ISSF_RADII_MM[target_type]
    black_r_mm = radii[10 - disc["outer_ring"]]
    for ring in range(1, 11):
        r_mm = radii[10 - ring]
        scale = r_mm / black_r_mm
        col = RAINBOW[(ring - 1) % len(RAINBOW)]
        thickness = 2 if ring != 1 else 4
        cv2.ellipse(out, (int(cx), int(cy)),
                    (int(a * scale), int(b * scale)),
                    int(angle), 0, 360, col, thickness)
    # Mark detected ring minima with white dots at predicted position.
    for d in detected_rings:
        r_px = d["observed_r_px"]
        # Approximate position along major axis for viz only.
        cv2.circle(out, (int(cx + r_px), int(cy)), 4, (255, 255, 255), -1)
        cv2.putText(out, str(d["ring"]), (int(cx + r_px) + 5, int(cy) - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return out


def draw_radial_profile(profile: np.ndarray, px_per_mm: float,
                          target_type: str, max_radius_px: float,
                          shape: tuple[int, int]) -> np.ndarray:
    """Render profile as a 100px-tall strip, with vertical lines at predicted
    ring radii."""
    n = len(profile)
    H, W = shape
    canvas_w = min(W, 800)
    canvas_h = 120
    canvas = np.full((canvas_h, canvas_w, 3), 255, dtype=np.uint8)
    # Normalize profile to canvas height.
    p = profile
    p_lo, p_hi = float(p.min()), float(p.max())
    if p_hi - p_lo < 1:
        return canvas
    norm = (p - p_lo) / (p_hi - p_lo)
    # Draw profile line.
    pts = []
    for i in range(0, n, max(1, n // canvas_w)):
        x = int(i * canvas_w / n)
        y = int((1.0 - norm[i]) * (canvas_h - 4)) + 2
        pts.append((x, y))
    cv2.polylines(canvas, [np.array(pts, dtype=np.int32)], False, (0, 0, 0), 1)

    # Vertical lines at predicted ring radii.
    radii = ISSF_RADII_MM[target_type]
    for k in range(10, 0, -1):
        r_mm = radii[10 - k]
        r_px = r_mm * px_per_mm
        if r_px >= max_radius_px:
            continue
        x = int(r_px * canvas_w / max_radius_px)
        col = RAINBOW[(k - 1) % len(RAINBOW)]
        cv2.line(canvas, (x, 0), (x, canvas_h - 1), col, 1)
        cv2.putText(canvas, str(k), (x + 2, canvas_h - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
    return canvas


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_one(img_id: int, target_type: str = DEFAULT_TARGET) -> dict[str, Any]:
    img_path = TRAIN_DIR / f"{img_id}.jpg"
    img = load_exif_normalized(img_path)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_01_original.jpg"), img)

    crop, loc_meta = localize_and_crop(img, target_type)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_02_crop.png"), crop)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # Stage B — black disc.
    disc = find_black_disc(gray, target_type)
    if not disc.get("found"):
        return {"img_id": img_id, "error": "black-disc not found",
                "reason": disc.get("reason", "unknown")}
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_03_black_disc_mask.png"), disc["mask"])
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_04_black_disc_overlay.png"),
                draw_black_disc(crop, disc))

    # Stage C — radial profile around the disc center.
    max_r = max(crop.shape) * 0.6
    profile = radial_profile(gray, disc["center"], disc["axes"],
                              disc["angle"], max_r)
    profile_img = draw_radial_profile(profile, disc["px_per_mm"],
                                       target_type, max_r, crop.shape[:2])
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_05_radial_profile.png"), profile_img)

    detected = detect_rings_in_profile(profile, disc["px_per_mm"],
                                        target_type, max_r)
    # Choose outermost ring IN FRAME: largest k for which observed_r_px < max_r
    # AND the ring's predicted radius is < (max_in_frame_radius).
    in_frame_rings = [d for d in detected
                       if d["predicted_r_px"] < min(crop.shape) * 0.55]
    in_frame_rings.sort(key=lambda d: d["ring"])
    if in_frame_rings:
        outermost_in_frame = in_frame_rings[0]["ring"]
    else:
        outermost_in_frame = disc["outer_ring"]  # fallback: just the black disc

    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_06_predicted_rings.png"),
                draw_predicted_rings(crop, disc, target_type,
                                      disc["px_per_mm"], detected))

    # Stage D — mask + extract at outermost-in-frame ring.
    mask = build_target_mask(gray.shape, disc, target_type,
                              disc["px_per_mm"], outermost_in_frame)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_07_target_mask.png"), mask)
    extracted = extract_target(crop, mask)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_08_target_extracted.png"), extracted)

    return {
        "img_id": img_id,
        "target_type": target_type,
        "crop_shape": list(crop.shape),
        "black_disc": {k: v for k, v in disc.items() if k != "mask"},
        "px_per_mm": disc["px_per_mm"],
        "outermost_in_frame_ring": outermost_in_frame,
        "detected_rings": detected,
        "n_rings_in_frame": len(in_frame_rings),
    }


def main():
    train_ids = [1, 4, 6, 10, 12, 19, 21, 29, 31, 46]
    results = []
    for img_id in train_ids:
        print(f"=== Image {img_id}.jpg ===", flush=True)
        try:
            r = run_one(img_id, target_type=DEFAULT_TARGET)
            if "error" in r:
                print(f"  ERROR: {r['error']} — {r.get('reason')}", flush=True)
            else:
                bd = r["black_disc"]
                print(f"  px/mm={r['px_per_mm']:.2f}  outer_in_frame=ring {r['outermost_in_frame_ring']}"
                      f"  rings_in_frame={r['n_rings_in_frame']}"
                      f"  disc_axis={bd['axes'][0]:.0f}x{bd['axes'][1]:.0f}px"
                      f"  ratio={bd['ratio']:.2f}", flush=True)
            results.append(r)
        except Exception as e:
            print(f"  EXCEPTION: {e}", flush=True)
            import traceback; traceback.print_exc()
            results.append({"img_id": img_id, "error": str(e)})

    out_path = OUT_DIR / "ring_calibration_v2_results.json"
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nResults → {out_path}")


if __name__ == "__main__":
    main()
