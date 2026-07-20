"""Probe multiple hole-detection configurations on image 46.

Strategy: compare GT vs candidates from different detector setups, render
side-by-side montages so we can see what's happening.
"""
from __future__ import annotations
from pathlib import Path
import math
import cv2
import numpy as np

from cv.blob_detect import to_gray, crop_to_target, calibrate, _sobel_mag, _local_std
from cv.gt import load_bgr, magenta_centers


def overlay(crop, cal, candidates, gt_crop, title):
    viz = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    # GT as big green ring
    for x, y in gt_crop:
        cv2.circle(viz, (int(x), int(y)), 30, (0, 255, 0), 3)
        cv2.circle(viz, (int(x), int(y)), 4, (0, 255, 0), -1)
    # Candidates as red dots / cyan rings sized by radius
    for x, y, r in candidates:
        cv2.circle(viz, (int(x), int(y)), max(3, int(r)), (255, 0, 0), 2)
        cv2.circle(viz, (int(x), int(y)), 3, (0, 0, 255), -1)
    cv2.putText(viz, title, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
    return viz


def run():
    img_id = 46
    train = Path("resources/train")
    bgr = load_bgr(train / f"{img_id}.jpg")
    gray = to_gray(bgr)
    crop, bbox = crop_to_target(gray)
    x0, y0, w, h = bbox
    cal = calibrate(crop)
    s = cal["s_px"]
    cx, cy = cal["cx"], cal["cy"]
    print(f"s={s:.1f} cx={cx:.1f} cy={cy:.1f}")

    # GT in crop coords
    bgr_mark = load_bgr(train / f"{img_id}_marked.jpg")
    gt_full, _ = magenta_centers(bgr_mark)
    gt_crop = [(x - x0, y - y0) for (x, y) in gt_full]
    print(f"GT (crop): {gt_crop}")

    r1 = cal["r_bull_px"] + 9 * s  # ring-1 outer
    print(f"r1 (target extent) = {r1:.1f}")

    results = []

    # ---- Config A: current default ----
    params = cv2.SimpleBlobDetector_Params()
    params.minThreshold, params.maxThreshold, params.thresholdStep = 8, 200, 8
    params.filterByColor = True; params.blobColor = 0
    params.filterByArea = True
    params.minArea = math.pi * (0.04 * s) ** 2
    params.maxArea = math.pi * (0.6 * s) ** 2
    params.filterByCircularity = False
    params.filterByConvexity = False
    params.filterByInertia = False
    params.minDistBetweenBlobs = 3
    g = cv2.GaussianBlur(crop, (0, 0), 2)
    kps = [kp for kp in cv2.SimpleBlobDetector_create(params).detect(g)
           if math.hypot(kp.pt[0] - cx, kp.pt[1] - cy) < r1]
    cand_a = [(kp.pt[0], kp.pt[1], kp.size / 2) for kp in kps]
    results.append(("A default", overlay(crop, cal, cand_a, gt_crop, f"A default n={len(cand_a)}")))

    # ---- Config B: tighter on size (auto-caliber ~14px). Holes look ~14-18 px ----
    # Observed true matches: r=18.2, 18.4, 10.5. Looking at GT, holes should be one
    # calibre. Bullet holes for 9x19 (~9mm dia) at this scale (s=101 px = 1 ring)
    # ring spacing for 10m air pistol target = ~30 mm in real life? Actually for
    # 10m air pistol the ring spacing is 8mm. So 101 px = 8 mm => 1 mm = 12.6 px.
    # 9mm bullet => 9 * 12.6 = ~113 px diameter => 56 px radius. That can't be right.
    # For 4.5 mm air pistol: 4.5 * 12.6 = 57 px dia = 28 px radius. Too big.
    # Let's check actual matches: matched hole at (764, 1175) had r=18.2 — that's
    # 18 px radius = ~1.4 mm. Too small for a real bullet. Looks like SimpleBlobDetector
    # is detecting the dark CENTER of each hole, not the full hole edge.
    # Let's allow bigger.
    params2 = cv2.SimpleBlobDetector_Params()
    params2.minThreshold, params2.maxThreshold, params2.thresholdStep = 5, 100, 5
    params2.filterByColor = True; params2.blobColor = 0
    params2.filterByArea = True
    params2.minArea = math.pi * (0.10 * s) ** 2  # bigger min
    params2.maxArea = math.pi * (0.55 * s) ** 2
    params2.filterByCircularity = True; params2.minCircularity = 0.4
    params2.filterByConvexity = True; params2.minConvexity = 0.7
    params2.filterByInertia = True; params2.minInertiaRatio = 0.3
    params2.minDistBetweenBlobs = 10
    kps = [kp for kp in cv2.SimpleBlobDetector_create(params2).detect(g)
           if math.hypot(kp.pt[0] - cx, kp.pt[1] - cy) < r1]
    cand_b = [(kp.pt[0], kp.pt[1], kp.size / 2) for kp in kps]
    results.append(("B tight+circ", overlay(crop, cal, cand_b, gt_crop, f"B tight+circ n={len(cand_b)}")))

    # ---- Config C: HoughCircles on grayscale directly ----
    h, w = crop.shape
    short = min(h, w)
    # try a range of params
    found = []
    for r_lo, r_hi, p2 in [(0.05, 0.20, 25), (0.08, 0.18, 22), (0.06, 0.16, 28)]:
        cs = cv2.HoughCircles(crop, cv2.HOUGH_GRADIENT, dp=1.0,
                              minDist=int(0.3 * s),
                              param1=200, param2=p2,
                              minRadius=int(r_lo * short),
                              maxRadius=int(r_hi * short))
        if cs is not None:
            for c in cs[0]:
                if math.hypot(c[0] - cx, c[1] - cy) < r1:
                    found.append((float(c[0]), float(c[1]), float(c[2])))
    # cluster
    found.sort(key=lambda c: -c[2])
    clustered = []
    for x, y, r in found:
        for i, (ccx, ccy, cr) in enumerate(clustered):
            if math.hypot(x - ccx, y - ccy) < 0.5 * s and abs(r - cr) / r < 0.3:
                clustered[i] = ((ccx + x) / 2, (ccy + y) / 2, (cr + r) / 2)
                break
        else:
            clustered.append((x, y, r))
    results.append(("C HoughCircles", overlay(crop, cal, clustered, gt_crop, f"C Hough n={len(clustered)}")))

    # ---- Config D: HoughCircles on local-std texture map ----
    lst = _local_std(crop, 15)
    lst_n = cv2.normalize(lst, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    found = []
    for r_lo, r_hi, p2 in [(0.05, 0.20, 25), (0.08, 0.18, 22), (0.06, 0.16, 28)]:
        cs = cv2.HoughCircles(lst_n, cv2.HOUGH_GRADIENT, dp=1.0,
                              minDist=int(0.3 * s),
                              param1=200, param2=p2,
                              minRadius=int(r_lo * short),
                              maxRadius=int(r_hi * short))
        if cs is not None:
            for c in cs[0]:
                if math.hypot(c[0] - cx, c[1] - cy) < r1:
                    found.append((float(c[0]), float(c[1]), float(c[2])))
    clustered2 = []
    found.sort(key=lambda c: -c[2])
    for x, y, r in found:
        for i, (ccx, ccy, cr) in enumerate(clustered2):
            if math.hypot(x - ccx, y - ccy) < 0.5 * s and abs(r - cr) / r < 0.3:
                clustered2[i] = ((ccx + x) / 2, (ccy + y) / 2, (cr + r) / 2)
                break
        else:
            clustered2.append((x, y, r))
    results.append(("D Hough on lst", overlay(crop, cal, clustered2, gt_crop, f"D Hough on lst n={len(clustered2)}")))

    # ---- Config E: HoughCircles on inverted grayscale (holes are darker, become
    # bright peaks after inversion — sometimes HoughCircles likes that)
    inv = cv2.bitwise_not(crop)
    found = []
    for r_lo, r_hi, p2 in [(0.05, 0.20, 25), (0.08, 0.18, 22)]:
        cs = cv2.HoughCircles(inv, cv2.HOUGH_GRADIENT, dp=1.0,
                              minDist=int(0.3 * s),
                              param1=200, param2=p2,
                              minRadius=int(r_lo * short),
                              maxRadius=int(r_hi * short))
        if cs is not None:
            for c in cs[0]:
                if math.hypot(c[0] - cx, c[1] - cy) < r1:
                    found.append((float(c[0]), float(c[1]), float(c[2])))
    clustered3 = []
    found.sort(key=lambda c: -c[2])
    for x, y, r in found:
        for i, (ccx, ccy, cr) in enumerate(clustered3):
            if math.hypot(x - ccx, y - ccy) < 0.5 * s and abs(r - cr) / r < 0.3:
                clustered3[i] = ((ccx + x) / 2, (ccy + y) / 2, (cr + r) / 2)
                break
        else:
            clustered3.append((x, y, r))
    results.append(("E Hough on inv", overlay(crop, cal, clustered3, gt_crop, f"E Hough inv n={len(clustered3)}")))

    # Build montage
    small = [(name, cv2.resize(im, (im.shape[1] // 3, im.shape[0] // 3))) for name, im in results]
    cols = 2
    rows = (len(small) + cols - 1) // cols
    H, W = small[0][1].shape[:2]
    montage = np.full((H * rows + 20 * (rows + 1), W * cols + 20 * (cols + 1), 3), 30, np.uint8)
    for i, (name, im) in enumerate(small):
        r, c = i // cols, i % cols
        y = 20 + r * (H + 20)
        x = 20 + c * (W + 20)
        montage[y:y + H, x:x + W] = im
    cv2.imwrite(str(Path("resources/train/intermediate_blob/46_configs.png")), montage)
    print("Wrote montage")

    # Also save the local-std map
    cv2.imwrite(str(Path("resources/train/intermediate_blob/46_localstd.png")), lst_n)


if __name__ == "__main__":
    run()
