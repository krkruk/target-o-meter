"""Debug probe for image 46: GT vs predictions in crop coords.

Run: uv run python -m cv.tmp.probe_46
"""
from __future__ import annotations
from pathlib import Path
import cv2
import numpy as np

from cv.blob_detect import crop_to_target, calibrate, to_gray, detect_holes
from cv.gt import load_bgr, magenta_centers


def run():
    img_id = 46
    train = Path("resources/train")
    bgr = load_bgr(train / f"{img_id}.jpg")
    gray = to_gray(bgr)
    crop, bbox = crop_to_target(gray)
    x0, y0, w, h = bbox
    cal = calibrate(crop)
    holes = detect_holes(crop, cal)
    print(f"crop bbox={bbox} crop.shape={crop.shape}")
    print(f"cal: ok={cal['ok']} s={cal['s_px']:.1f} r_bw={cal['r_bw_px']:.1f} r_bull={cal['r_bull_px']:.1f}")
    print(f"pred holes (crop coords): {len(holes)}")
    for x, y, r in holes:
        print(f"  ({x:.0f},{y:.0f}) r={r:.1f}")

    # Load GT in full-img coords, translate to crop coords.
    bgr_mark = load_bgr(train / f"{img_id}_marked.jpg")
    gt_full, _ = magenta_centers(bgr_mark)
    gt_crop = [(x - x0, y - y0) for (x, y) in gt_full]
    print(f"\nGT centers (crop coords): {len(gt_crop)}")
    for x, y in gt_crop:
        print(f"  ({x:.0f},{y:.0f})")

    # Visualize: crop in gray, GT in GREEN, predictions in MAGENTA/RED.
    viz = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    for x, y in gt_crop:
        cv2.circle(viz, (int(x), int(y)), 26, (0, 255, 0), 3)
        cv2.circle(viz, (int(x), int(y)), 4, (0, 255, 0), -1)
    for x, y, r in holes:
        cv2.circle(viz, (int(x), int(y)), int(r), (0, 0, 255), 2)
        cv2.circle(viz, (int(x), int(y)), 3, (0, 0, 255), -1)
    out = Path("resources/train/intermediate_blob/46_probe.png")
    cv2.imwrite(str(out), viz)
    print(f"\nWrote {out}")

    # Distance matrix: GT (rows) vs predictions (cols)
    print("\nDistance from each GT to nearest prediction:")
    for gx, gy in gt_crop:
        ds = sorted([(float(np.hypot(gx - px, gy - py)), i) for i, (px, py, _) in enumerate(holes)])
        for d, i in ds[:3]:
            mark = "  <-- MATCH" if d < 25 else ""
            print(f"  GT({gx:.0f},{gy:.0f}) -> pred #{i} ({holes[i][0]:.0f},{holes[i][1]:.0f}) d={d:.1f}{mark}")


if __name__ == "__main__":
    run()
