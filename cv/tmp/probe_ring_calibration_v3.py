"""Probe v3: Target extraction via calibration-corrected Stage 2.

Earlier probes tried to detect rings from scratch and got confused by the
crop's dark surrounding (crop_mean is 47-92 — these phone photos are taken
on dark backers, so Otsu grabs the whole bbox).

This probe takes the pragmatic route: leverage the existing Stage 1 + Stage
2 (already proven robust on 43/46), apply a *calibration correction* using
the known ISSF ring-diameter table, and produce the extracted-target
artifact the user wants to see.

Key insight: existing _stage2_rings uses a hardcoded `0.85 * card_mm` as
the black-disc ratio. For Air Pistol the black disc is rings 7-10 outer =
59.5 mm diameter, NOT 0.85*170 = 144.5 mm. The correct ratio is
59.5 / 170 = 0.35. So existing px_per_mm is OVERESTIMATED by 0.85/0.35 =
2.43x — a major calibration error that propagates to all ring radii.

Pipeline:
    1. EXIF-normalize load.
    2. Reuse _stage1_localize + _stage2_rings for crop + rough calibration.
    3. CORRECT px_per_mm using verified ISSF geometry:
         black_disc_diameter_mm = ring_7_outer_diameter_mm
         corrected_pmm = old_pmm * (0.85 * card_mm) / black_disc_diameter_mm
    4. Build ring-overlay visualization: draw predicted ISSF rings at the
       corrected px_per_mm. THIS IS THE VALIDATION ARTIFACT — if the rings
       land on the actual printed rings in the photo, the calibration is
       correct.
    5. Build mask at predicted 1-ring outer boundary. Extract target.

Outputs to resources/train/intermediate/<id>_*.{jpg,png}:
    _01_original.jpg       EXIF-normalized source
    _02_crop.png           target bbox crop
    _03_ring_overlay.png   crop + predicted rings drawn (color-coded)
    _04_target_mask.png    binary mask at predicted ring 1
    _05_target_extracted.png   target on neutral background
    _06_ring_extraction.png    target_extracted + ring overlay (final)

Run:
    uv run python cv/tmp/probe_ring_calibration_v3.py
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
from detect import (_stage1_localize, _stage2_rings, LOCATOR_LONG_SIDE,
                     TARGET_CARD_MM)  # noqa: E402

TRAIN_DIR = _REPO / "resources" / "train"
OUT_DIR = _REPO / "resources" / "train" / "intermediate"
META_PATH = _REPO / "resources" / "paper_targets" / "metadata.yml"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Verified ISSF ring radii (mm) from Wikipedia via the survey.
# radii[0] = ring 10 inner radius, radii[9] = ring 1 inner radius (= outer
# scoring boundary).
ISSF_RADII_MM = {
    "air_pistol":       [5.75 + 8.0 * i for i in range(10)],
    "precision_pistol": [25.0 + 25.0 * i for i in range(10)],
}
# Outermost black-portion ring (the black disc = rings K..10).
ISSF_BLACK_OUTER_RING = {"air_pistol": 7, "precision_pistol": 5}

# The bug in existing _stage2_rings: assumes black disc = 0.85 * card_mm.
# Reality for Air Pistol: black disc (rings 7-10) outer = 59.5 mm diameter,
# card = 170 mm → ratio = 0.35.
STAGE2_BLACK_RATIO_BUG = 0.85   # what _stage2_rings assumes

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
    for entry in meta.get("images", []):
        if entry.get("id") == img_id:
            return entry
    return {}


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


def corrected_calibration(target_type: str) -> dict:
    """Compute the calibration-correction factor for the existing _stage2_rings.

    _stage2_rings returns px_per_mm assuming black_disc_diameter_mm = 0.85 *
    card_mm. The correct value depends on target type. The correction factor
    multiplied by the (wrong) px_per_mm gives the (right) px_per_mm.
    """
    card_mm = TARGET_CARD_MM[target_type]
    wrong_black_diam = STAGE2_BLACK_RATIO_BUG * card_mm
    outer_black_ring = ISSF_BLACK_OUTER_RING[target_type]
    true_black_diam = 2.0 * ISSF_RADII_MM[target_type][10 - outer_black_ring]
    return {
        "card_mm": card_mm,
        "wrong_black_diam_mm": wrong_black_diam,
        "true_black_diam_mm": true_black_diam,
        "correction_factor": wrong_black_diam / true_black_diam,
        "outer_black_ring": outer_black_ring,
    }


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------
RAINBOW = [
    (0, 0, 255), (0, 127, 255), (0, 255, 255), (0, 255, 0),
    (255, 255, 0), (255, 127, 0), (255, 0, 0), (255, 0, 127),
    (255, 0, 255), (127, 0, 255),
]


def draw_rings(img: np.ndarray, center: tuple[float, float],
               px_per_mm: float, target_type: str,
               thickness: int = 2, with_labels: bool = True) -> np.ndarray:
    """Draw all 10 ISSF rings at the given px_per_mm, color-coded."""
    out = img.copy()
    cx, cy = center
    radii = ISSF_RADII_MM[target_type]
    for ring in range(1, 11):
        r_mm = radii[10 - ring]
        r_px = int(r_mm * px_per_mm)
        if r_px > 2 * max(out.shape):
            continue
        col = RAINBOW[(ring - 1) % len(RAINBOW)]
        t = thickness + 2 if ring == 1 else thickness
        cv2.circle(out, (int(cx), int(cy)), r_px, col, t)
        if with_labels and ring in (1, 5, 7, 10):
            cv2.putText(out, f"r{ring}", (int(cx) + r_px + 4, int(cy) - 4),
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


def build_target_mask(shape: tuple[int, int], center: tuple[float, float],
                       px_per_mm: float, target_type: str,
                       outermost_ring: int = 1) -> np.ndarray:
    """Filled disc at outermost_ring's predicted radius."""
    mask = np.zeros(shape, dtype=np.uint8)
    cx, cy = center
    r_mm = ISSF_RADII_MM[target_type][10 - outermost_ring]
    r_px = int(r_mm * px_per_mm)
    cv2.circle(mask, (int(cx), int(cy)), r_px, 255, -1)
    return mask


def extract_target(img: np.ndarray, mask: np.ndarray,
                   bg_color: tuple[int, int, int] = (245, 245, 245)) -> np.ndarray:
    out = img.copy()
    inv = cv2.bitwise_not(mask)
    bg = np.full_like(img, bg_color, dtype=np.uint8)
    out = cv2.bitwise_and(out, out, mask=mask)
    bg = cv2.bitwise_and(bg, bg, mask=inv)
    return cv2.add(out, bg)


def fit_outermost_in_frame_ring(center: tuple[float, float],
                                  px_per_mm: float, target_type: str,
                                  crop_shape: tuple[int, int]) -> int:
    """Return the outermost ring whose predicted radius fits inside the crop
    with a 5% margin. Lets us avoid clipping the mask when the photo is
    framed tight on the inner rings."""
    cx, cy = center
    H, W = crop_shape[:2]
    max_in_frame_px = min(cx, cy, W - cx, H - cy) * 0.95
    radii = ISSF_RADII_MM[target_type]
    for ring in range(1, 11):
        r_mm = radii[10 - ring]
        r_px = r_mm * px_per_mm
        if r_px <= max_in_frame_px:
            return ring
    return 10  # only the inner 10-ring fits


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_one(img_id: int, target_type: str = DEFAULT_TARGET) -> dict[str, Any]:
    img_path = TRAIN_DIR / f"{img_id}.jpg"
    img = load_exif_normalized(img_path)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_01_original.jpg"), img)

    crop, loc_meta = localize_and_crop(img, target_type)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_02_crop.png"), crop)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # Existing Stage 2 — gets us a center + (biased) px_per_mm.
    (cx, cy), sr, pmm_old, _ = _stage2_rings(crop, card_mm=TARGET_CARD_MM[target_type])

    # Calibration correction.
    cal = corrected_calibration(target_type)
    pmm_new = pmm_old * cal["correction_factor"]

    # Determine which outer ring is in frame (some images are framed tight).
    outer_in_frame = fit_outermost_in_frame_ring((cx, cy), pmm_new, target_type,
                                                   crop.shape)

    # _03 ring overlay — VALIDATION ARTIFACT.
    overlay = draw_rings(crop, (cx, cy), pmm_new, target_type)
    overlay = draw_calibration_text(overlay, [
        f"px_per_mm: old={pmm_old:.2f}  corrected={pmm_new:.2f}",
        f"correction: x{cal['correction_factor']:.2f}  "
        f"({cal['wrong_black_diam_mm']:.0f}/{cal['true_black_diam_mm']:.0f}mm)",
        f"outer ring drawn: ring {outer_in_frame}",
    ])
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_03_ring_overlay.png"), overlay)

    # _04 mask.
    mask = build_target_mask(gray.shape, (cx, cy), pmm_new, target_type,
                              outermost_ring=outer_in_frame)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_04_target_mask.png"), mask)

    # _05 extracted.
    extracted = extract_target(crop, mask)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_05_target_extracted.png"), extracted)

    # _06 extracted + ring overlay (final composite).
    final = draw_rings(extracted, (cx, cy), pmm_new, target_type,
                       thickness=1, with_labels=False)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_06_extracted_with_rings.png"), final)

    return {
        "img_id": img_id,
        "target_type": target_type,
        "crop_shape": list(crop.shape),
        "loc_method": loc_meta.get("method"),
        "stage2_center": [float(cx), float(cy)],
        "stage2_pmm_old": float(pmm_old),
        "corrected_pmm": float(pmm_new),
        "correction_factor": float(cal["correction_factor"]),
        "outer_in_frame_ring": int(outer_in_frame),
        "true_black_diam_mm": float(cal["true_black_diam_mm"]),
        "wrong_black_diam_mm": float(cal["wrong_black_diam_mm"]),
    }


def main():
    train_ids = [1, 4, 6, 10, 12, 19, 21, 29, 31, 46]
    results = []
    for img_id in train_ids:
        print(f"=== Image {img_id}.jpg ===", flush=True)
        try:
            r = run_one(img_id, target_type=DEFAULT_TARGET)
            print(f"  pmm: {r['stage2_pmm_old']:.2f} → {r['corrected_pmm']:.2f}"
                  f"  (x{r['correction_factor']:.2f})  "
                  f"center=({r['stage2_center'][0]:.0f},"
                  f"{r['stage2_center'][1]:.0f})  "
                  f"outer_in_frame=ring {r['outer_in_frame_ring']}", flush=True)
            results.append(r)
        except Exception as e:
            print(f"  EXCEPTION: {e}", flush=True)
            import traceback; traceback.print_exc()
            results.append({"img_id": img_id, "error": str(e)})

    out_path = OUT_DIR / "ring_calibration_v3_results.json"
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nResults → {out_path}")
    print("Key intermediate images (per image):")
    print("  <id>_03_ring_overlay.png       — VALIDATION: do rings land on printed rings?")
    print("  <id>_05_target_extracted.png   — the bg-eliminated target")
    print("  <id>_06_extracted_with_rings.png — composite")


if __name__ == "__main__":
    main()
