"""Magenta ground-truth parser for resources/train/*_marked.jpg.

The user manually marked each true bullet hole with a small magenta DOT at its
centre. This module extracts those dots and returns per-image ground-truth
hole centres (no radii — the dots encode position only).

IMPORTANT: magenta is eval-only. The detection algorithm itself is pure
grayscale and must never depend on this colour.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageOps
from pathlib import Path


def load_bgr(path: str | Path) -> np.ndarray:
    """Load an image EXIF-normalised to upright orientation, as BGR uint8."""
    pil = Image.open(path)
    pil = ImageOps.exif_transpose(pil)
    rgb = np.array(pil.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def extract_magenta_mask(bgr: np.ndarray) -> np.ndarray:
    """Binary mask of magenta pixels.

    Magenta = high R, high B, low G (balanced between red and blue, clearly
    below the green channel so white/grey paper is excluded).
    """
    r = bgr[:, :, 2].astype(np.int16)
    g = bgr[:, :, 1].astype(np.int16)
    b = bgr[:, :, 0].astype(np.int16)
    magenta = (
        (r >= 150)
        & (b >= 140)
        & (g <= 110)
        & (np.minimum(r, b) - g >= 40)
        & (np.abs(r - b) <= 90)
    )
    return magenta.astype(np.uint8) * 255


# The magenta brush is a fixed-size disk. Estimated from the data:
# unit-dot area ≈ 1605 px → radius ≈ 22.6 px. Used to split merged dots.
DOT_RADIUS_PX = 22.6


def _split_component(comp_mask: np.ndarray, dot_r: float) -> list[tuple[float, float]]:
    """Split one magenta component (possibly several overlapping dots) into
    centres via distanceTransform peaks — one peak per overlapping disk."""
    dist = cv2.distanceTransform(comp_mask, cv2.DIST_L2, 5)
    # Local maxima: pixels that survive a dilation whose kernel is ~0.8×dot_r.
    kr = max(3, int(0.8 * dot_r))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * kr + 1, 2 * kr + 1))
    dilated = cv2.dilate(dist, kernel)
    peaks = (dist >= dilated) & (dist > 0.5 * dot_r)
    ys, xs = np.where(peaks)
    if len(xs) == 0:
        # Degenerate: fall back to centroid.
        m = cv2.moments(comp_mask)
        if m["m00"] > 0:
            return [(m["m10"] / m["m00"], m["m01"] / m["m00"])]
        return []
    # Sub-pixel refine each peak by centroiding the dot within a dot_r window.
    h, w = comp_mask.shape
    centers: list[tuple[float, float]] = []
    for x, y in zip(xs, ys):
        x0, x1 = max(0, x - int(dot_r)), min(w, x + int(dot_r) + 1)
        y0, y1 = max(0, y - int(dot_r)), min(h, y + int(dot_r) + 1)
        m = cv2.moments(comp_mask[y0:y1, x0:x1])
        if m["m00"] > 0:
            centers.append((m["m10"] / m["m00"] + x0, m["m01"] / m["m00"] + y0))
        else:
            centers.append((float(x), float(y)))
    return centers


def magenta_centers(
    bgr: np.ndarray,
    min_area: int = 5,
    max_area_frac: float = 0.004,
    dot_r: float = DOT_RADIUS_PX,
) -> tuple[list[tuple[float, float]], np.ndarray]:
    """Return (centers, label_viz) for magenta dots.

    Touching/overlapping dots (common in dense clusters) are split via
    distanceTransform peaks, so each true hole yields one centre.
    """
    mask = extract_magenta_mask(bgr)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    max_area = max(8, int(mask.size * max_area_frac))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    centers: list[tuple[float, float]] = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area or area > max_area:
            continue
        comp = (labels == i).astype(np.uint8)
        centers.extend(_split_component(comp, dot_r))

    viz = bgr.copy()
    for x, y in centers:
        cv2.circle(viz, (int(x), int(y)), 8, (0, 255, 0), 2)
    return centers, viz


# Expected hit counts from resources/paper_targets/metadata.yml (train subset).
EXPECTED = {
    1: 10, 4: 10, 6: 10, 10: 10, 12: 13, 19: 10, 21: 5, 29: 5, 31: 14, 46: 5,
}


def main() -> None:
    train = Path("resources/train")
    out = Path("resources/train/intermediate_blob/gt")
    out.mkdir(parents=True, exist_ok=True)

    print(f"{'id':>3} {'got':>4} {'exp':>4} {'ok':>3}")
    all_ok = True
    for img_id in sorted(EXPECTED):
        marked = train / f"{img_id}_marked.jpg"
        if not marked.exists():
            print(f"{img_id:>3}  MISSING")
            continue
        bgr = load_bgr(marked)
        centers, viz = magenta_centers(bgr)
        exp = EXPECTED[img_id]
        ok = len(centers) == exp
        all_ok &= ok
        print(f"{img_id:>3} {len(centers):>4} {exp:>4} {'✓' if ok else '✗':>3}")
        cv2.imwrite(str(out / f"{img_id:02d}_gt_centers.png"), viz)
        np.save(str(out / f"{img_id:02d}_gt.npy"), np.array(centers, dtype=np.float64))

    print("\nALL OK" if all_ok else "\nMISMATCH — tune thresholds")


if __name__ == "__main__":
    main()
