"""Calibrate bullet radius per image by probing matched-filter response at GT."""
from __future__ import annotations
import math
from pathlib import Path
import cv2
import numpy as np

from cv.blob_detect import to_gray, crop_to_target, calibrate
from cv.gt import load_bgr, magenta_centers
from cv.tmp.probe_46_mf import matched_filter


def probe_image(img_id, radii_px):
    train = Path("resources/train")
    bgr = load_bgr(train / f"{img_id}.jpg")
    gray = to_gray(bgr)
    crop, bbox = crop_to_target(gray)
    x0, y0, _, _ = bbox
    cal = calibrate(crop)
    s = cal["s_px"]

    bgr_mark = load_bgr(train / f"{img_id}_marked.jpg")
    gt_full, _ = magenta_centers(bgr_mark)
    gt_crop = [(x - x0, y - y0) for (x, y) in gt_full]

    print(f"\nimg {img_id}: s={s:.0f} cx={cal['cx']:.0f} cy={cal['cy']:.0f} aniso={cal['anisotropy']:.2f} n_gt={len(gt_crop)}")

    responses_at_r = {}
    for r in radii_px:
        responses_at_r[r] = matched_filter(crop, r)

    best_per_gt = []
    for i, (gx, gy) in enumerate(gt_crop):
        best_r, best_v = None, -1
        for r in radii_px:
            ix, iy = int(gx), int(gy)
            if 0 <= ix < crop.shape[1] and 0 <= iy < crop.shape[0]:
                v = float(responses_at_r[r][iy, ix])
                if v > best_v:
                    best_v, best_r = v, r
        best_per_gt.append((best_r, best_v))

    rs = [r for r, _ in best_per_gt]
    ratios = [r / s for r in rs]
    if rs:
        print(f"  Best r per GT (px): min={min(rs)} max={max(rs)} median={sorted(rs)[len(rs)//2]}")
        print(f"  Best r per GT (s):  min={min(ratios):.2f} max={max(ratios):.2f} median={sorted(ratios)[len(ratios)//2]:.2f}")
        print(f"  Best r values: {sorted(rs)}")

    print(f"  Per-r mean response at GT:")
    for r in radii_px:
        vals = []
        for gx, gy in gt_crop:
            ix, iy = int(gx), int(gy)
            if 0 <= ix < crop.shape[1] and 0 <= iy < crop.shape[0]:
                vals.append(float(responses_at_r[r][iy, ix]))
        if vals:
            print(f"    r={r:>3} ({r/s:>4.2f}s): mean={np.mean(vals):>8.0f} min={min(vals):>8.0f} max={max(vals):>8.0f}")


def main():
    IDS = [1, 4, 6, 10, 12, 19, 21, 29, 31, 46]
    for img_id in IDS:
        bgr = load_bgr(Path("resources/train") / f"{img_id}.jpg")
        gray = to_gray(bgr)
        crop, bbox = crop_to_target(gray)
        cal = calibrate(crop)
        s = cal["s_px"]
        ratios = [0.05, 0.08, 0.10, 0.13, 0.16, 0.20, 0.25, 0.30, 0.40, 0.50]
        radii = sorted({max(3, int(round(r * s))) for r in ratios})
        try:
            probe_image(img_id, radii)
        except Exception as e:
            print(f"img {img_id}: ERROR {e}")


if __name__ == "__main__":
    main()
