"""Probe the black-disc detector on image 46 to find why calibration is off."""
from __future__ import annotations
from pathlib import Path
import cv2
import numpy as np
import math

from cv.blob_detect import to_gray, crop_to_target, blackdisc_center, detect_black_disc, _sobel_mag
from cv.gt import load_bgr, magenta_centers


def run():
    img_id = 46
    train = Path("resources/train")
    bgr = load_bgr(train / f"{img_id}.jpg")
    gray = to_gray(bgr)
    crop, bbox = crop_to_target(gray)
    x0, y0, w, h = bbox
    print(f"crop shape={crop.shape} bbox={bbox}")

    # What does blackdisc_center return?
    cx, cy, aniso, major_dir, semi_a, semi_b = blackdisc_center(crop)
    print(f"blackdisc_center: cx={cx:.1f} cy={cy:.1f} aniso={aniso:.3f}")
    print(f"  semi_a={semi_a:.1f} semi_b={semi_b:.1f}")

    # Try the more detailed detect_black_disc
    bd = detect_black_disc(crop)
    if bd:
        print(f"detect_black_disc: cx={bd['cx']:.1f} cy={bd['cy']:.1f} inscribed_r={bd['inscribed_r_px']:.1f}")

    # GT centroid
    bgr_mark = load_bgr(train / f"{img_id}_marked.jpg")
    gt_full, _ = magenta_centers(bgr_mark)
    gt_crop = np.array([(x - x0, y - y0) for (x, y) in gt_full])
    gt_centroid = gt_crop.mean(axis=0)
    print(f"GT centroid (crop): ({gt_centroid[0]:.1f}, {gt_centroid[1]:.1f})")
    print(f"GT points: {gt_crop.tolist()}")

    # Visualize: show crop with black-disc centre (RED), GT centroid (GREEN),
    # GT points (green dots), and the inscribed-r circle.
    viz = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    if bd:
        cv2.circle(viz, (int(bd['cx']), int(bd['cy'])), int(bd['inscribed_r_px']), (0, 0, 255), 3)
        cv2.circle(viz, (int(bd['cx']), int(bd['cy'])), 10, (0, 0, 255), -1)
    cv2.circle(viz, (int(gt_centroid[0]), int(gt_centroid[1])), 10, (0, 255, 0), -1)
    for x, y in gt_crop:
        cv2.circle(viz, (int(x), int(y)), 20, (0, 255, 0), 2)
    cv2.imwrite(str(Path("resources/train/intermediate_blob/46_blackdisc_probe.png")), viz)

    # Save the binary mask of dark blobs to see what was found
    g = cv2.GaussianBlur(crop, (0, 0), 3)
    b = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
                              max(51, (max(crop.shape) // 16) | 1), C=5)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, k)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(b, 8)
    print(f"\nDark blob components (top 5 by area):")
    blob_info = [(i, int(stats[i, cv2.CC_STAT_AREA]), tuple(cents[i])) for i in range(1, n)]
    blob_info.sort(key=lambda x: -x[1])
    for i, area, c in blob_info[:5]:
        print(f"  label#{i} area={area} centroid=({c[0]:.1f}, {c[1]:.1f})")

    # Save mask
    cv2.imwrite(str(Path("resources/train/intermediate_blob/46_darkmask.png")), b)


if __name__ == "__main__":
    run()
