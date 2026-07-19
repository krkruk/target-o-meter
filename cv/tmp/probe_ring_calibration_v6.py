"""Probe v6: Ring 1 anchoring via wide-radius HoughCircles.

Probe v5 anchored on the black disc (ring 7 outer), but its initial pmm
estimate from Stage 2 was wrong (Stage 2 picks an arbitrary dark blob —
could be ring 3 silhouette, not rings 7-10 disc). Result: HoughCircles
locked onto the wrong ring.

The cleaner approach: don't trust Stage 2's scale at all. Run
HoughCircles with a WIDE radius range on a Sobel edge map. ISSF rings are
spaced well-enough apart (8 mm × pmm = ~85 px at phone scale) that
HoughCircles resolves them as distinct concentric circles. The LARGEST
detected circle is ring 1 (the outermost scoring ring, biggest physical
feature). From ring 1's radius, pmm = r_detected / 77.75 mm.

Pipeline:
    1. EXIF-normalize load.
    2. Stage 1 (light): bbox crop via _stage1_localize, scaled back to
       full-res. We don't trust Stage 2 at all — only Stage 1's bbox
       for a rough region of interest.
    3. Inside the bbox, run HoughCircles with WIDE radius range on a
       Sobel-magnitude map. Collect all detected concentric circles.
    4. The largest detected circle = ring 1. Derive pmm and bullseye.
    5. Sanity check: do other detected circles match ISSF ring radii at
       the derived pmm? (Each detected circle's r_mm should be close to
       one of [5.75, 13.75, 21.75, ..., 77.75].)
    6. Predict all 10 ring positions. Mask + extract at ring 1 + margin.
    7. Render overlay: SOLID for rings whose predicted radius is
       confirmed by a HoughCircles detection (in-frame and detected) or
       in-frame but not detected; DASHED for rings outside the photo.
"""
from __future__ import annotations

import json
import math
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
OUT_DIR = _REPO / "resources" / "train" / "intermediate_v6"
META_PATH = _REPO / "resources" / "paper_targets" / "metadata.yml"
OUT_DIR.mkdir(parents=True, exist_ok=True)

ISSF_RADII_MM = {
    "air_pistol":       [5.75 + 8.0 * i for i in range(10)],     # rings 10..1
    "precision_pistol": [25.0 + 25.0 * i for i in range(10)],
}
EXTRACTION_MARGIN_MM = 25.0
DEFAULT_TARGET = "air_pistol"


def load_exif_normalized(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
    return np.asarray(im)[:, :, ::-1].copy()


# ---------------------------------------------------------------------------
# Stage A — bbox crop via Stage 1 (don't use Stage 2 at all)
# ---------------------------------------------------------------------------
def bbox_crop(img: np.ndarray) -> tuple[np.ndarray, dict]:
    h0, w0 = img.shape[:2]
    long_side = max(h0, w0)
    scale = LOCATOR_LONG_SIDE / long_side if long_side > LOCATOR_LONG_SIDE else 1.0
    locator = cv2.resize(img, (int(w0 * scale), int(h0 * scale)),
                         interpolation=cv2.INTER_AREA)
    bbox, _, fail = _stage1_localize(locator, "air_pistol")
    if fail:
        return img, {"x0": 0, "y0": 0, "scale": scale, "failed": True}
    x, y, bw, bh = bbox
    sx_full = w0 / locator.shape[1]
    sy_full = h0 / locator.shape[0]
    # Expand the bbox a bit — Stage 1 tends to grab just the dark blob,
    # we want to include some surrounding context (rings 1-6 are outside
    # the dark area).
    expand = 0.20  # 20% each side
    bx0 = max(0, int((x - bw * expand) * sx_full))
    by0 = max(0, int((y - bh * expand) * sy_full))
    bx1 = min(w0, int((x + bw * (1 + expand)) * sx_full))
    by1 = min(h0, int((y + bh * (1 + expand)) * sy_full))
    return img[by0:by1, bx0:bx1], {"x0": bx0, "y0": by0, "scale": scale,
                                    "failed": False}


# ---------------------------------------------------------------------------
# Stage B — HoughCircles with WIDE radius range on Sobel
# ---------------------------------------------------------------------------
def detect_all_rings(gray: np.ndarray, target_type: str = DEFAULT_TARGET) -> dict:
    """Run HoughCircles on a Sobel-magnitude map with progressively wider
    radius ranges. Returns all detected circles, plus the best ring-1
    candidate (largest one) and the implied calibration."""
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
    gx = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    if mag.max() < 1e-6:
        return {"found": False, "reason": "zero magnitude"}
    mag_u8 = ((mag / mag.max()) * 255).astype(np.uint8)

    # Run HoughCircles with several minDist settings to catch nested rings.
    # HOUGH_GRADIENT_ALT tends to lock onto one strong circle per locality;
    # we lower minDist to allow concentric detections.
    short_side = min(h, w)
    r_min = max(5, int(0.005 * short_side))
    r_max = int(0.55 * short_side)
    all_circles: list[tuple[float, float, float]] = []
    # Multi-range HoughCircles. ISSF rings span a wide radius range
    # (ring 10 = 5.75mm, ring 1 = 77.75mm = 13.5x larger). A single
    # HoughCircles pass with param2 tuned for the strong black-disc edge
    # misses the thin outer ring strokes on white card. Run three passes
    # at different radius scales with appropriately tuned params.
    ranges = [
        (r_min, int(0.15 * short_side), 0.75),    # inner rings (black disc area)
        (int(0.10 * short_side), int(0.30 * short_side), 0.85),  # middle rings
        (int(0.20 * short_side), r_max, 0.90),    # outer rings (thin strokes)
    ]
    for r_lo, r_hi, p2 in ranges:
        if r_hi <= r_lo + 5:
            continue
        # Use a small min_dist to allow concentric detections; the clusterer
        # below merges duplicates.
        min_dist = max(r_lo + 1, int(0.05 * short_side))
        circles = cv2.HoughCircles(
            mag_u8, cv2.HOUGH_GRADIENT_ALT, dp=1.5,
            minDist=min_dist,
            param1=80, param2=p2,
            minRadius=r_lo, maxRadius=r_hi,
        )
        if circles is None:
            continue
        for c in circles[0]:
            all_circles.append((float(c[0]), float(c[1]), float(c[2])))

    if not all_circles:
        return {"found": False, "reason": "no circles",
                "edge_map": mag_u8}

    # Cluster: HoughCircles returns multiple detections of the same ring
    # at slightly different positions. Group circles whose centers are
    # within 10 px AND radii within 5% of each other. (Looser tolerance
    # merges adjacent ISSF rings — at pmm=11 they're 88 px apart, so a
    # 15% radius tolerance on r=700 = 105 px would merge rings 1+2.)
    all_circles.sort(key=lambda c: -c[2])  # largest first
    clustered: list[tuple[float, float, float]] = []
    for cx, cy, r in all_circles:
        merged = False
        for i, (ccx, ccy, cr) in enumerate(clustered):
            if (math.hypot(cx - ccx, cy - ccy) < max(10, 0.02 * r) and
                abs(r - cr) / max(r, cr) < 0.05):
                # Average in.
                clustered[i] = ((ccx + cx) / 2, (ccy + cy) / 2, (cr + r) / 2)
                merged = True
                break
        if not merged:
            clustered.append((cx, cy, r))

    # The LARGEST cluster is ring 1.
    clustered.sort(key=lambda c: -c[2])
    ring1 = clustered[0]
    cx1, cy1, r1 = ring1
    ring1_mm = ISSF_RADII_MM[target_type][-1]  # ring 1 = 77.75 mm
    pmm = r1 / ring1_mm

    # For each detected cluster, assign to the closest ISSF ring.
    assigned = []
    for cx, cy, r in clustered:
        r_mm_obs = r / pmm
        best_k = min(range(1, 11),
                     key=lambda k: abs(r_mm_obs - ISSF_RADII_MM[target_type][10 - k]))
        r_mm_pred = ISSF_RADII_MM[target_type][10 - best_k]
        residual = abs(r_mm_obs - r_mm_pred)
        assigned.append({
            "cx": cx, "cy": cy, "r_px": r, "r_mm_obs": r_mm_obs,
            "ring": best_k, "r_mm_pred": r_mm_pred,
            "residual_mm": residual,
        })

    return {
        "found": True,
        "all_clusters": clustered,
        "ring1": {"cx": cx1, "cy": cy1, "r_px": r1},
        "pmm": pmm,
        "bullseye": (cx1, cy1),  # ring 1 center == bullseye (concentric)
        "assigned_rings": assigned,
        "edge_map": mag_u8,
    }


# ---------------------------------------------------------------------------
# Stage C — mask + extract
# ---------------------------------------------------------------------------
def build_extraction_mask(shape: tuple[int, int], detect: dict,
                           target_type: str) -> np.ndarray:
    cx, cy = detect["bullseye"]
    pmm = detect["pmm"]
    ring1_mm = ISSF_RADII_MM[target_type][-1]
    outer_r_mm = ring1_mm + EXTRACTION_MARGIN_MM
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


def draw_rings(img: np.ndarray, detect: dict, target_type: str,
               thickness_solid: int = 2,
               thickness_dashed: int = 1) -> np.ndarray:
    out = img.copy()
    if not detect.get("found"):
        return out
    cx, cy = detect["bullseye"]
    pmm = detect["pmm"]
    radii = ISSF_RADII_MM[target_type]
    h, w = img.shape[:2]
    max_r = min(cx, cy, w - cx, h - cy)
    # Map ring → was it detected by HoughCircles?
    detected_rings = {a["ring"] for a in detect["assigned_rings"]}
    for ring in range(1, 11):
        r_mm = radii[10 - ring]
        r_px = int(r_mm * pmm)
        col = RAINBOW[(ring - 1) % len(RAINBOW)]
        in_frame = r_px < max_r - 5
        was_detected = ring in detected_rings
        if in_frame:
            if was_detected:
                # Solid + thicker: confirmed by HoughCircles.
                cv2.circle(out, (int(cx), int(cy)), r_px, col, thickness_solid + 1)
            else:
                # Solid thin: in frame but not detected (still predicted).
                cv2.circle(out, (int(cx), int(cy)), r_px, col, thickness_solid)
        else:
            # Dashed: extrapolated beyond photo.
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
                   cv2.MARKER_CROSS, 30, 2)
    return out


def draw_text(img: np.ndarray, lines: list[str]) -> np.ndarray:
    out = img.copy()
    y = 30
    for ln in lines:
        cv2.putText(out, ln, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 255, 0), 2)
        y += 25
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_one(img_id: int, target_type: str = DEFAULT_TARGET) -> dict[str, Any]:
    img_path = TRAIN_DIR / f"{img_id}.jpg"
    img = load_exif_normalized(img_path)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_01_original.jpg"), img)

    crop, crop_meta = bbox_crop(img)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_02_crop.png"), crop)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # Stage B — HoughCircles wide-radius.
    detect = detect_all_rings(gray, target_type)
    if detect.get("edge_map") is not None:
        cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_03_sobel_edges.png"),
                    detect["edge_map"])

    if not detect.get("found"):
        return {"img_id": img_id, "error": detect.get("reason", "unknown")}

    # Stage C — mask + extract.
    mask = build_extraction_mask(gray.shape, detect, target_type)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_04_target_mask.png"), mask)
    extracted = extract_target(crop, mask)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_05_target_extracted.png"), extracted)

    # Stage D — overlay.
    overlay = draw_rings(crop, detect, target_type)
    detected_rings = sorted({a["ring"] for a in detect["assigned_rings"]})
    overlay = draw_text(overlay, [
        f"pmm (from ring 1 anchor): {detect['pmm']:.2f}",
        f"ring 1 radius: {detect['ring1']['r_px']:.0f} px",
        f"HoughCircles detected rings: {detected_rings}",
        f"target card 170 mm at this pmm = {170.0 * detect['pmm']:.0f} px wide",
    ])
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_06_ring_overlay.png"), overlay)

    final = draw_rings(extracted, detect, target_type,
                       thickness_solid=1, thickness_dashed=1)
    cv2.imwrite(str(OUT_DIR / f"{img_id:02d}_07_extracted_with_rings.png"), final)

    return {
        "img_id": img_id,
        "crop_size": list(crop.shape[:2]),
        "pmm": detect["pmm"],
        "ring1_px": detect["ring1"]["r_px"],
        "bullseye": list(detect["bullseye"]),
        "detected_rings": detected_rings,
        "all_assigned": detect["assigned_rings"],
        "n_raw_circles": len(detect.get("all_clusters", [])),
    }


def main():
    train_ids = [1, 4, 6, 10, 12, 19, 21, 29, 31, 46]
    results = []
    print(f"{'id':>3}  {'crop':>11}  {'pmm':>6}  {'ring1_r':>7}  "
          f"{'170mm=':>7}  {'detected_rings':>30}")
    for img_id in train_ids:
        try:
            r = run_one(img_id)
            results.append(r)
            if "error" in r:
                print(f"{img_id:>3}  ERROR: {r['error']}")
                continue
            target_w = 170.0 * r["pmm"]
            print(
                f"{img_id:>3}  {r['crop_size'][1]}x{r['crop_size'][0]:>4}  "
                f"{r['pmm']:>6.2f}  {r['ring1_px']:>7.0f}  "
                f"{target_w:>5.0f}px  {str(r['detected_rings']):>30}",
                flush=True,
            )
        except Exception as e:
            print(f"{img_id}: EXCEPTION: {e}", flush=True)
            import traceback; traceback.print_exc()

    out_path = OUT_DIR / "ring_calibration_v6_results.json"
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nResults → {out_path}")
    print(f"Intermediates → {OUT_DIR}/<id>_01..07*.png")
    print("Key validation image: <id>_06_ring_overlay.png")


if __name__ == "__main__":
    main()
