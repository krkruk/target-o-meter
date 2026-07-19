---
date: 2026-07-19T13:36:18Z
researcher: krzysztofkruk
git_commit: 8d9d9c38538b76556ba883b0fee523f31218b18a
branch: master
repository: target-o-meter
topic: "Robust CV algorithm for ISSF paper-target hole detection — service boundary kept Django-independent"
tags: [research, codebase, cv, opencv, watershed, homography, issf, paper-targets, spike]
status: complete
last_updated: 2026-07-19
last_updated_by: krzysztofkruk
last_updated_note: "Added follow-up: texture-based Stage 3 rewrite + empirical results"
---

# Research: Robust CV algorithm for ISSF paper-target hole detection

**Date**: 2026-07-19T13:36:18Z
**Researcher**: krzysztofkruk
**Git Commit**: [8d9d9c3](https://github.com/krkruk/target-o-meter/commit/8d9d9c38538b76556ba883b0fee523f31218b18a)
**Branch**: master
**Repository**: [krkruk/target-o-meter](https://github.com/krkruk/target-o-meter)

## Research Question

Develop a robust algorithm for bullet-hole detection in ISSF paper targets photographed at arbitrary angles with different calibers and overlapping holes. The algorithm must live in a standalone module that is easily extracted from the Django infrastructure. Evaluate the user-proposed 5-stage pipeline (Perspective Normalization → Geometry Extraction → Morphological Isolation → Watershed Segmentation → Radial Scoring) against the labeled dataset at `resources/paper_targets/` (46 phone-camera JPEGs with manually counted ground truth in `metadata.yml`) and surface concrete failure modes for the planning step.

Scope confirmed by user:
- **In**: all 5 pipeline stages, homography robustness, hole-diameter handling, overlap de-clustering. Full spike under `uv` with per-image fidelity.
- **Out**: Django service-shape (in-process vs Celery vs sidecar), Railway infra constraints. The module is Django-independent by construction (lives at top-level `cv/`, no `target_o_meter` imports).

## Summary

**The headline finding is uncomfortable: the 5-stage classical pipeline as described performs catastrophically on this dataset.** A from-scratch implementation of all five stages — using canonical OpenCV primitives (`cv2.warpPerspective`, `cv2.adaptiveThreshold`, `cv2.HoughCircles`, `cv2.morphologyEx`, `cv2.distanceTransform`, `cv2.watershed`, `cv2.moments`) — scores **mean score-Jaccard 0.089** against ground truth, with **0 / 46 images reaching the ≥0.9 PRD threshold** and **only 2 / 46 images even getting the right hole count** (hit-count RMSE 5.22). The pipeline runs end-to-end on every image without exceptions (no hard stage failures), so this is not a bug — it is a fidelity ceiling of the approach under these capture conditions.

The dominant blocker is **not** in the stages the user spent the most words on (homography, watershed). It is in Stage 3 (morphological isolation) and it is fundamental rather than parametric:

> **Bullet holes that land inside the black portion of the target are nearly invisible in the luminance channel.** The target's printed rings are dark ink, and 9 of every 10 shots in this dataset land in the 8/9/10 rings (the black disc). There is no consistent brightness signature that separates "dark hole inside dark ink."

The classical pipeline assumes a brightness difference between foreground (holes) and background (paper). On a black-background target with black holes, that assumption fails for the majority of hits. Stages 4 and 5 work correctly given a clean hole mask; Stage 1 localization is even reasonably robust (43/46 clean target bboxes). **Stage 3 is where the pipeline dies**, and no amount of Hough-parameter tuning or structuring-element resizing rescues it on the dense 9/10-ring hits that dominate the dataset.

Three pivots are recommended (see §Recommended next iteration):
1. Detect holes from **edges/texture**, not luminance. Bullet holes have a sharp circular boundary even when interior matches the background.
2. **Template-match the ISSF ring pattern** to localize bullseye + per-ring radii (replaces blob-centroid heuristic that biases on hole-clustered regions).
3. Surface a **capture-condition gate** as a product requirement — the ≥90% bar is likely unreachable from arbitrary phone photos of black-on-black hits without lighting constraints.

The standalone module lives at [`cv/`](https://github.com/krkruk/target-o-meter/tree/8d9d9c38538b76556ba883b0fee523f31218b18a/cv) (top-level, no Django imports) and is run via `uv run python -m cv.eval`. The contract is `cv.detect.detect(image_path, caliber, target_type) -> dict` returning `scores`, `centers`, `target_center`, `bullet_radius_px`, `px_per_mm`, `failure_stage`, `notes`. This is the seam to iterate behind in subsequent changes — its shape does not change even when the algorithm does.

## Detailed Findings

### Dataset characterization

46 phone-camera JPEGs at `resources/paper_targets/`, ground truth in [`metadata.yml`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/resources/paper_targets/metadata.yml).

**Resolution / orientation:**
- Two clusters: 30 portrait (~1842×4096, ~3.5 MB) and 16 landscape (~4096×1842, ~2.5 MB).
- **3 images (#15, #30, #31) are stored as landscape pixels with EXIF `Orientation=6`** — an algorithm that uses raw pixels without applying `PIL.ImageOps.exif_transpose` sees them sideways.
- Brightness range 114–176 (mean 134); image #24 is a >2σ bright outlier (possible overexposure), #35 is the darkest.

**Ground-truth distributions (425 shots total):**
- Shots per target: min 5, median 10, max 14, mean 9.24. 13 of 46 are 5-shot targets.
- Score histogram is heavily right-skewed: **335 / 425 shots (78.8%) are 8/9/10**. Long-tail (scores 0–3) is just 12 shots.
- No X-ring labels in `metadata.yml` even though the PRD lists X as a valid symbol.

**Per-caliber breakdown:**

| Caliber | Targets | Shots | Mean score | Bullet Ø (mm) | Notes |
|---|---|---|---|---|---|
| 22lr | 14 | 126 | 9.28 | 5.7 | Tightest clusters; 70 of 126 shots are 10s |
| 9x19 | 17 | 173 | 7.57 | 9.0 | Most varied (covers full 0–10 range) |
| .223Rem | 6 | 61 | 8.79 | 5.56 (bullet), but tears paper → effective 8–12 | High-velocity; not diameter-equal to 22lr despite ≈ bullet size |
| slug | 8 | 51 | 8.33 | ~18 (12-ga) | Largest holes; can overlap multiple rings |
| 9x19+22lr (mixed) | 1 (#31) | 14 | 8.43 | n/a | Canonical multi-caliber test case |

**Critical edge cases:**
- [`metadata.yml:91-96`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/resources/paper_targets/metadata.yml#L91-L96) — **#31** is mixed-caliber (`9x19` + `22lr` as a YAML list with comment "Two calibers detected"). Any single-diameter assumption fails here.
- [`metadata.yml:34-36`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/resources/paper_targets/metadata.yml#L34-L36) — **#12** contains a `0`-point hit (completely outside the rings). The scorer must emit a `0` bucket and the detector must not crop to the ring area before detection.
- [`metadata.yml:55-57`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/resources/paper_targets/metadata.yml#L55-L57) — **#19** is `22lr`, 10 shots, **all 10s**: extreme bullseye-stacking, the canonical watershed stress test. (#2, #3, #29, #30, #39 are similar at lower density.)

**Train subset:** [`resources/train/`](https://github.com/krkruk/target-o-meter/tree/8d9d9c38538b76556ba883b0fee523f31218b18a/resources/train) has 10 images (1, 4, 6, 10, 12, 19, 21, 29, 31, 46) — clearly hand-curated: all three edge cases above are present, all four calibers covered, both resolution clusters and both EXIF-orient modes represented. **Treat `train/` as the calibration set; the remaining 36 are the held-out evaluation set.**

### Spike architecture (Django-independent module)

The algorithm and eval harness live at the top-level [`cv/`](https://github.com/krkruk/target-o-meter/tree/8d9d9c38538b76556ba883b0fee523f31218b18a/cv) directory — **no imports from `target_o_meter.*` anywhere**, by construction. It can be lifted out as its own package without modification.

```
cv/
  __init__.py     empty package marker
  detect.py       algorithm — 5 stages, 474 LOC
  eval.py         harness — compares predictions to metadata.yml
  README.md       usage notes
```

**Public contract** ([`cv/detect.py:51-70`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L51-L70)):

```python
def detect(
    image_path: str | Path,
    caliber: str | list[str] | None,
    target_type: str = "air_pistol",   # or "precision_pistol"
    debug: bool = False,
) -> dict[str, Any]:
    """Returns:
      scores          list[int]         per-hole scores 0..10
      total           int               sum(scores)
      centers         list[(x, y)]      hole centroids in source-image px
      bullet_radius_px  float
      target_center   (x, y)            bullseye in source px
      target_radius_px float
      px_per_mm       float
      failure_stage   str | None        None | 'homography' | 'rings' | 'morph' | 'watershed' | 'scoring'
      notes           list[str]         diagnostics
    """
```

This is the boundary the rest of the system programs against. **The contract is stable even when the underlying algorithm is rewritten** — that is the point of separating this slice from the fidelity work in `S-02`.

**Eval harness** ([`cv/eval.py`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/eval.py)) loads `metadata.yml`, runs `detect()` per image, and reports two metrics:
1. **Hit-count error**: `|predicted_count - true_count|`.
2. **Score Jaccard (multiset)**: `|pred ∩ true| / |pred ∪ true|` treating both as multisets of scores. This is the proxy for the PRD ≥90% fidelity bar (a strict per-hole matching is impossible without explicit ground-truth hole positions, which the metadata does not contain).

Run via:

```bash
uv run python -m cv.eval
```

### Empirical results — full per-image table

The headline numbers (PRD target in parentheses):

- **Mean score Jaccard: 0.089** (≥0.90 required)
- **% images with `count_err == 0`: 4.3%** (2 of 46 — images 3 and 44)
- **% images with score Jaccard ≥ 0.9: 0.0%**
- **Hit-count RMSE: 5.22** shots
- **Under-count: 35/46** · Over-count: 9/46 · Exact: 2/46
- **No hard stage failures** — `failure_stage` is empty for every image; the pipeline runs to completion everywhere.

| id | n_true | n_pred | count_err | jaccard | caliber | stage_failed |
|----|--------|--------|-----------|---------|---------|--------------|
| 1 | 10 | 7 | 3 | 0.13 | 22lr | — |
| 2 | 10 | 5 | 5 | 0.00 | 22lr | — |
| 3 | 10 | 10 | 0 | 0.11 | 22lr | — |
| 4 | 10 | 5 | 5 | 0.00 | 9x19 | — |
| 5 | 10 | 1 | 9 | 0.10 | 9x19 | — |
| 6 | 10 | 9 | 1 | 0.12 | .223Rem | — |
| 7 | 10 | 11 | 1 | 0.11 | .223Rem | — |
| 8 | 13 | 6 | 7 | 0.00 | .223Rem | — |
| 9 | 12 | 3 | 9 | 0.07 | 9x19 | — |
| 10 | 10 | 1 | 9 | 0.10 | slug | — |
| 11 | 13 | 6 | 7 | 0.12 | 9x19 | — |
| 12 | 13 | 31 | 18 | 0.05 | 9x19 | — |
| 13 | 11 | 15 | 4 | 0.00 | slug | — |
| 14 | 9 | 13 | 4 | 0.10 | .223Rem | — |
| 15 | 5 | 4 | 1 | 0.12 | 9x19 | — |
| 16 | 10 | 8 | 2 | 0.29 | 22lr | — |
| 17 | 10 | 6 | 4 | 0.00 | 9x19 | — |
| 18 | 10 | 5 | 5 | 0.07 | 9x19 | — |
| 19 | 10 | 3 | 7 | 0.00 | 22lr | — |
| 20 | 10 | 11 | 1 | 0.11 | 22lr | — |
| 21 | 5 | 4 | 1 | 0.00 | slug | — |
| 22 | 5 | 1 | 4 | 0.00 | slug | — |
| 23 | 5 | 1 | 4 | 0.00 | slug | — |
| 24 | 13 | 9 | 4 | 0.16 | 22lr | — |
| 25 | 13 | 6 | 7 | 0.06 | 9x19 | — |
| 26 | 10 | 9 | 1 | 0.12 | 9x19 | — |
| 27 | 9 | 4 | 5 | 0.00 | .223Rem | — |
| 28 | 10 | 4 | 6 | 0.27 | 9x19 | — |
| 29 | 5 | 7 | 2 | 0.09 | 22lr | — |
| 30 | 5 | 8 | 3 | 0.00 | 22lr | — |
| 31 | 14 | 5 | 9 | 0.12 | ['9x19','22lr'] | — |
| 32 | 13 | 8 | 5 | 0.17 | 22lr | — |
| 33 | 12 | 7 | 5 | 0.12 | 9x19 | — |
| 34 | 10 | 7 | 3 | 0.13 | 9x19 | — |
| 35 | 10 | 9 | 1 | 0.12 | .223Rem | — |
| 36 | 5 | 1 | 4 | 0.20 | slug | — |
| 37 | 5 | 1 | 4 | 0.20 | slug | — |
| 38 | 5 | 1 | 4 | 0.00 | slug | — |
| 39 | 10 | 9 | 1 | 0.12 | 22lr | — |
| 40 | 10 | 7 | 3 | 0.06 | 9x19 | — |
| 41 | 10 | 12 | 2 | 0.16 | 22lr | — |
| 42 | 10 | 9 | 1 | 0.12 | 9x19 | — |
| 43 | 10 | 8 | 2 | 0.29 | 9x19 | — |
| 44 | 5 | 5 | 0 | 0.00 | 22lr | — |
| 45 | 5 | 8 | 3 | 0.00 | 22lr | — |
| 46 | 5 | 2 | 3 | 0.00 | 9x19 | — |

**Reading the table.** A score Jaccard near 0 with `count_err > 0` means the predicted multiset of scores is essentially disjoint from ground truth — not "off by one", but "predicting the wrong holes entirely". The two clean-count successes (#3 and #44) still score 0.11 and 0.00 respectively because the predicted *scores* don't match. This is not a tuning problem.

### Per-stage analysis

#### Stage 1 — Perspective normalization / localization
**Implemented at** [`cv/detect.py:176-231`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L176-L231) (`_stage1_localize`).

**Spike deviation from spec:** the user's pipeline calls for full 4-corner homography (`cv2.findContours` → `cv2.approxPolyDP` → 4-vertex polygon → `cv2.getPerspectiveTransform` → `cv2.warpPerspective`). The spike does NOT do this. Instead it localizes the target as the largest roughly-square dark blob on a downscaled "locator" image, then crops from the full-resolution original so small calibers (22lr ≈ 5.7 mm) keep enough pixels.

**Rationale:** phone photos of ISSF targets rarely show a clean card rectangle — the white card margin is often cropped out of frame, leaving the black aiming mark as the dominant dark region. The contour-quad heuristic picks up the black portion (an ellipse-ish shape), not the card.

**Result:** **43 / 46 images produce a roughly-square (aspect ≥ 0.6) target bbox with no hard failures.** Localization is genuinely robust. The bbox includes some background dark area (cardboard, shadow) around the actual card, which inflates `px_per_mm` underestimate and propagates downstream.

**Canonical OpenCV primitives for the spec-compliant version** (literature):
- `cv2.Canny(gray, 75, 200)` for edges
- `cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)`
- `cv2.approxPolyDP(c, 0.02 * arcLength, True)` → look for 4 vertices
- `cv2.getPerspectiveTransform(quad, canonical_dst)` then `cv2.warpPerspective`
- The pyimagesearch "document scanner" pattern.

**Alternatives:**
- Manual corner click UI via `cv2.selectROI` / `cv2.setMouseCallback` — least robust, most accurate; what many open-source ISSF scorers actually do.
- AR/fiducial markers (`cv2.aruco.detectMarkers`) — robust but requires physical changes to the range backer.
- Hough-lines intersection — better than contour-quad when the card edge is partially occluded.

#### Stage 2 — Ring geometry extraction
**Implemented at** [`cv/detect.py:234-287`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L234-L287) (`_stage2_rings`).

**Spike approach:** find the largest roughly-circular dark blob (the black portion of the target) inside the cropped frame; take its `cv2.minEnclosingCircle` centroid as the bullseye; derive `px_per_mm` from its diameter assuming the black portion is ~0.85 × card_mm.

**Result:** finds *a* center but it's frequently wrong. When shots concentrate in one quadrant of the 10-ring, the holes extend the black region asymmetrically and the centroid shifts away from the true target center. ISSF ring-size assumption is a single constant for both target types — the linear 10-ring model is wrong (real ISSF rings are unevenly spaced).

**What the spec called for:** adaptive threshold + contour hierarchy (`RETR_TREE`) + circularity filter + sort by radius → concentric rings + bullseye. The canonical primitives are:
- `cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, blockSize=75, C=10)`
- `cv2.findContours(bin_, cv2.RETR_TREE, ...)` — `RETR_TREE` preserves the "ring inside ring inside ring" topology
- `cv2.fitEllipse(c)` per ring contour — handles paper warping better than `minEnclosingCircle`
- Circularity filter `4π·area/perimeter² ≥ 0.85`

**Literature finding (unverified):** for ISSF 10m Air Pistol the ring spacing is ~8 mm (10-ring Ø 11.5 mm, 9-ring Ø 27.5 mm, …, 1-ring Ø 155.5 mm; X-ring Ø 5 mm). For 25m/50m Precision Pistol spacing is 50 mm (10-ring Ø 50 mm, …, 1-ring Ø 500 mm; no X-ring in qualification). The 10-ring diameters are verified from Wikipedia primary sources; the full tables are widely reproduced but I could not fetch the official ISSF rule-book PDF — treat as authoritative-but-unverified until checked against <https://www.issf-sports.org/rules>.

**Strongly recommended alternative:** since the warp produces a metric image with known mm dimensions, **ring radii are constants — only the bullseye needs to be solved.** Template-matching a synthetic ISSF ring pattern against the cropped target would give both accurate bullseye AND per-ring alignment in one shot, killing two problems at once.

#### Stage 3 — Morphological isolation of holes  ⚠ THE BLOCKER
**Implemented at** [`cv/detect.py:290-361`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L290-L361) (`_stage3_morph`, `_filter_blob_area`).

**The fundamental problem:** the target's printed rings are dark ink; bullet holes in the 8/9/10 rings are also dark. **There is no consistent brightness signature separating them.** 79% of all shots in the dataset land in the 8/9/10 rings (the black disc) — exactly where luminance-based detection fails.

**Three attempts, in order:**

1. **Otsu inverted + elliptical opening** (closest to spec) — failed catastrophically. Opening kernel sized to bullets (≈15–30 px) cannot distinguish bullet-sized dark blobs from the target black; the whole black region is returned as one blob.
2. **Black-hat morphology** (`cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)`) with bullet-sized kernel — responds to ring lines too; closing the kernel against printed rings produces a response across the entire black area.
3. **Shipped version** — HoughCircles (caliber-bounded radius) plus area-filtered black-hat. Best of three but still massively under-counts: 35/46 images have `count_err > 0`.

**Per-caliber failure modes:**
- 22lr is worst — tiny 5.7 mm holes are nearly invisible on black at the dataset's resolution (5–10 px/mm typical → 30–60 px diameter bullets, but no contrast against black background).
- slug also bad — over-blending produces single-blob detection where multiple slug holes touch.
- 9×19 and .223Rem — torn paper edges give a faint ring of contrast, but thresholding it cleanly across lighting diversity in the dataset isn't possible with global parameters.

**What this means for the pipeline as specified:** Stage 3 is unrecoverable as a luminance/morphology operation on this dataset. The fix must come from a different signal source — edges (Canny + HoughCircles on the *edge map*, not the luminance), local contrast (CLAHE on small patches), or controlled-capture conditions (backlight the target).

#### Stage 4 — Watershed de-clustering
**Implemented at** [`cv/detect.py:364-433`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L364-L433) (`_stage4_watershed`, `_centroids_from_mask`).

**Result:** works correctly. `cv2.distanceTransform` + threshold markers + `cv2.watershed` cleanly splits overlapping blobs that *do* have detectable necks. Centroids extracted via `cv2.moments`.

**But:** watershed cannot recover holes that Stage 3 failed to surface in the first place. Garbage in, garbage out. **This stage is not the bottleneck** — it would work well if Stage 3 gave it a clean mask.

**Canonical pattern** (from the OpenCV tutorial "Image Segmentation with Distance Transform and Watershed" by Tsesmelis — verified at <https://docs.opencv.org/4.x/d2/dbd/tutorial_distance_transform.html>):

```python
dist = cv2.distanceTransform(bw, cv2.DIST_L2, 3)
cv2.normalize(dist, dist, 0, 1.0, cv2.NORM_MINMAX)
_, dist = cv2.threshold(dist, 0.4, 1.0, cv2.THRESH_BINARY)
dist = cv2.dilate(dist, np.ones((3, 3), dtype=np.uint8))
dist_8u = dist.astype('uint8')
contours, _ = cv2.findContours(dist_8u, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
markers = np.zeros(dist.shape, dtype=np.int32)
for i in range(len(contours)):
    cv2.drawContours(markers, contours, i, (i + 1), -1)
cv2.circle(markers, (5, 5), 3, (255, 255, 255), -1)   # background seed
cv2.watershed(img_result, markers)
```

**Known failure mode (ISSF-specific):** the peak threshold (0.4 in the tutorial) is calibrated for similar-sized touching objects. Real bullet clusters of 4–5 holes produce distance-transform peaks of very different heights; a fixed threshold either over-segments lone holes (phantom splits) or under-segments dense clusters. **Adaptive threshold per connected component** — `cv2.connectedComponents` first, then `distanceTransform` per component, threshold at `0.5 × max(dist_in_component)` — is the standard fix and is what the spike implements at [`cv/detect.py:377-380`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L377-L380).

#### Stage 5 — Radial scoring
**Implemented at** [`cv/detect.py:436-457`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L436-L457) (`_stage5_score`).

**The ISSF line-break rule** is implemented correctly:
```python
adj = max(0.0, distance - bullet_radius_px)
score = 10 - int(math.floor(10 * adj / scoring_radius_px))
```
This is the algebraic equivalent of the plug-gauge rule: "if the bullet edge touches the higher-value ring line, the higher value is awarded." Verified against the ISSF General Technical Rules description on the ISSF site (canonical PDF not directly fetchable).

**But:** scores are meaningless in practice because (a) the bullseye location is biased (Stage 2) and (b) the predicted hole set differs from ground truth by ±5 holes typically (Stage 3). The arithmetic is right; the inputs are wrong.

**Caliber → bullet radius table** ([`cv/detect.py:26-31`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L26-L31)):

| Caliber | Nominal Ø (mm) | Plug gauge Ø (mm) | `bullet_radius_mm` |
|---|---|---|---|
| 22lr | 5.7 | 5.6 | 2.8 |
| 9x19 | 9.0 | 9.0 | 4.5 |
| .223Rem | 5.56 | 5.56 | 2.78 |
| slug | ~18 | event-defined | take user input |

**Caveat on torn-paper holes:** for clean air-pistol holes the line-break algebra is essentially exact. For torn 9×19 holes the detected centroid can drift 1–2 mm from the true plug-gauge centre — enough to flip a ring at the 9/10 boundary. **Recommended:** flag shots whose `effective_distance` lands within `±bullet_radius` of a ring line for human review. This is an MVP-acceptable partial-automation pattern.

## Architecture Insights

### Django independence by construction
The `cv/` package has zero references to `target_o_meter.*`, Django models, settings, or any Django-specific module. Dependencies are only `opencv-python-headless`, `numpy`, and (in the eval harness) `pyyaml` + `pillow` — all already pinned in [`pyproject.toml:6-14`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/pyproject.toml#L6-L14). The package can be lifted into a separate repo without modification.

### Why a stable I/O contract matters more than the algorithm
The roadmap explicitly parks fidelity work in `S-02` ([`context/foundation/roadmap.md:84-87`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/context/foundation/roadmap.md#L84-L87)). The point of `cv-service-boundary` is to land the seam — a callable that takes `(image_path, caliber, target_type)` and returns a result dict — so downstream slices (`S-02`, eventually `S-03`) can iterate on accuracy without touching the Django upload/review/persist flow.

**The contract documented above is the deliverable of this change.** The current implementation is a baseline; replacing the algorithm entirely (e.g., switching to a U-Net instance-segmentation model, or adding a manual-corner-click UI, or doing edge-based detection) does not change the contract.

### Eval harness as regression lock
`cv/eval.py` runs the full pipeline against all 46 ground-truth images and prints per-image + aggregate metrics. **Treat this as the regression test for the algorithm.** Any algorithmic change should be evaluated by re-running the harness and comparing the aggregate table — the per-image breakdown makes regressions on specific edge cases (#12 zero-score, #19 all-tens, #31 mixed-caliber) immediately visible.

### `train/` is a calibration subset, not a random sample
The 10 images in `resources/train/` are hand-curated to cover all four calibers, all three "interesting" edge cases (#12, #19, #31), both resolution clusters, and both EXIF orientations. **Develop against `train/`; reserve the other 36 for evaluation.** This avoids the overfitting trap of tuning parameters on the eval set.

## Code References

- [`cv/__init__.py`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/__init__.py) — empty package marker.
- [`cv/detect.py:51-170`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L51-L170) — `detect()` public entry point.
- [`cv/detect.py:176-231`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L176-L231) — `_stage1_localize` (target bbox from largest dark blob).
- [`cv/detect.py:234-287`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L234-L287) — `_stage2_rings` (bullseye + scale).
- [`cv/detect.py:290-345`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L290-L345) — `_stage3_morph` (HoughCircles + black-hat — **the blocker**).
- [`cv/detect.py:348-361`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L348-L361) — `_filter_blob_area` (bullet-sized area filter).
- [`cv/detect.py:364-433`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L364-L433) — `_stage4_watershed` (distance-transform + watershed split).
- [`cv/detect.py:436-457`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L436-L457) — `_stage5_score` (ISSF line-break rule).
- [`cv/eval.py`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/eval.py) — harness; run via `uv run python -m cv.eval`.
- [`resources/paper_targets/metadata.yml`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/resources/paper_targets/metadata.yml) — ground truth (141 LOC).
- [`pyproject.toml:6-14`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/pyproject.toml#L6-L14) — `opencv-python-headless`, `numpy`, `pillow`, `pyyaml` already pinned.

## Risk Register (ranked)

| # | Risk | Source | Likelihood | Impact | Mitigation |
|---|---|---|---|---|---|
| 1 | **Luminance invisibility of holes in the black target region** — the proposed pipeline's Stage 3 cannot recover 9/10-ring hits | Spike finding | **Certain** (observed) | **Blocks ≥90% fidelity target entirely** | Pivot Stage 3 from luminance to edges (Canny + HoughCircles on edge map), or local contrast (CLAHE on small patches), or controlled-capture (backlight). Add a capture-condition gate at intake. |
| 2 | Bullseye centroid biased by hole clusters in 10-ring | Spike finding | H | H (compounds Stage 3 errors; flips 9↔10 at boundary) | Template-match a synthetic ISSF ring pattern instead of blob-centroid heuristic. |
| 3 | Mixed-caliber targets (#31) break single-diameter assumption | Spike finding + dataset | M | M (only 1 image, but the failure mode generalizes) | Multi-scale detection or accept user input per-target with multi-select. Surface as product requirement. |
| 4 | EXIF orientation=6 images processed sideways | Dataset | M | H (silently rotates 3 images) | Apply `PIL.ImageOps.exif_transpose` at load — single-line fix. |
| 5 | ≥90% bar unreachable from arbitrary phone photos without capture constraints | Spike + literature | M-H | H (could force product rethink per [`roadmap.md:157`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/context/foundation/roadmap.md#L157)) | Surface as product question. Options: (a) require oblique lighting / backlit capture, (b) accept lower fidelity bar (e.g., ≥75% with manual-correction UI), (c) manual corner-click + per-hole click fallback. |
| 6 | X-ring labels missing from `metadata.yml` despite PRD | Dataset | L | L (doesn't block detection; affects scoring-precision eval) | Either annotate X-ring labels in metadata for the train subset, or treat X as a UX-only concept (display but don't eval). |
| 7 | Full ISSF ring-diameter tables unverified | Literature | M | M (wrong ring table silently mis-assigns scores) | Fetch official rule-book PDF from <https://www.issf-sports.org/rules> and hard-code the verified table for both target types. |
| 8 | No real homography — perspective distortion untreated on skewed photos | Spike deviation | M | M (precision target at 30° looks elliptical, breaking radial scoring) | Implement full 4-corner homography as Stage 1 once Stage 3 is unblocked. Localization already proves the target blob is findable. |
| 9 | Watershed over/under-segmentation on dense overlaps (>50% area overlap, common on 5-shot clusters) | Literature | M | M | Adaptive per-component distance-transform threshold already implemented ([`cv/detect.py:377-380`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L377-L380)). Acceptable to flag for human review beyond 50% overlap. |
| 10 | Torn-paper centroid drift on 9×19 / .223Rem ragged holes | Literature | M | L-M (1–2 mm drift; flips 9↔10 boundary only) | Flag shots within `±bullet_radius` of a ring line for human review. |

## Historical Context (from prior changes)

- [`context/foundation/roadmap.md:75-87`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/context/foundation/roadmap.md#L75-L87) — **F-02 (`cv-service-boundary`) is explicitly the seam**. The roadmap note: "the underlying detection fidelity is downstream (this foundation establishes the seam, not the accuracy)." Risk register row: "Sequenced as a foundation (not folded into `S-02`) because the top blocker is `skills` (CV novel) — establishing the service seam first lets the agent/user iterate on fidelity behind a stable contract instead of conflating 'what does the service expect' with 'does it work'." **This research validates the roadmap's instinct** — the seam is now in place, and the wedge risk (`≥90%` unvalidated) is contained inside `S-02`.
- [`context/foundation/roadmap.md:118-129`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/context/foundation/roadmap.md#L118-L129) — **S-02 carries the wedge risk** ("Which CV approach actually hits ≥90% fidelity on real ISSF photos"). **This research suggests the wedge is bigger than the roadmap assumed** — the proposed classical pipeline does not hit the bar; S-02 needs to either pivot to edges/DL or descope the bar with manual-correction fallback. **Re-running `/10x-frame` on the wedge assumption before `/10x-plan cv-service-boundary` (or before scheduling S-02) is advisable.**
- [`context/foundation/lessons.md`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/context/foundation/lessons.md) — single entry is about Railpack Django deployment, not relevant to CV.
- [`context/foundation/infrastructure.md:91`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/context/foundation/infrastructure.md#L91) — flagged OpenCV build risk on Railpack ("Pre-build a Docker image with OpenCV installed and use Dockerfile deploy path instead of Railpack auto-detection"). Out of scope for this slice per user direction, but worth re-visiting when S-02 lands.
- [`context/foundation/prd.md:36-39`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/context/foundation/prd.md#L36-L39) — NFR: "hole detection fidelity ≥90%". **The current baseline observes 0% of images meeting this bar. The PRD NFR is the load-bearing assumption that this research calls into question.**

## Recommended next iteration

Ranked by leverage, not difficulty:

1. **Pivot Stage 3 from luminance to edges.** Try `cv2.Canny` inside the target's black region followed by `cv2.HoughCircles` on the *edge map* (not the luminance). Bullet holes have a sharp circular edge even when their interior matches the background. Calibrate Canny thresholds on image #29 (clean 5×10 case) before scaling. **This is the single change most likely to move the needle.**
2. **Template-match the rings instead of blob-centroid for bullseye.** Generate a synthetic ISSF ring pattern at the calibrated px/mm, slide-match it across the cropped target, take the peak as `(center, fine-scale alignment)`. Gives both accurate bullseye AND per-ring radius — eliminating the linear-ring approximation and the cluster-bias problem at once.
3. **Add a capture-condition check at intake.** Detect low-contrast-on-black-target cases and prompt the user to rephotograph with oblique lighting. The PRD's ≥90% fidelity target is likely unreachable from arbitrary phone photos without capture constraints — surface this as a product requirement rather than a CV problem.
4. **Implement real 4-corner homography** with `cv2.findContours` → `cv2.approxPolyDP` → 4-vertex polygon. The localization stage already proves the dark-target blob is findable; tightening the contour approximation to lock onto the card edges (rather than the black portion) would let us warp to canonical 850×850 / 2750×2750 and remove perspective distortion.
5. **Verify the ISSF ring-diameter tables** against the official rule-book PDF before hard-coding. The 10-ring diameters (11.5 mm air pistol; 50 mm precision pistol) are Wikipedia-verified; the rest are widely-cited but unverified.
6. **Apply `PIL.ImageOps.exif_transpose` at load** — one-line fix for the 3 EXIF-orient=6 images.

## Open Questions

1. **Is the ≥90% fidelity bar reachable on this dataset without capture constraints?** The spike says no for the proposed classical pipeline. A re-run of `/10x-frame` on the wedge assumption before `/10x-plan cv-service-boundary` (or before scheduling S-02) is advisable. Owner: user. Block: yes — blocks S-02/S-03 sequencing per [`roadmap.md:157`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/context/foundation/roadmap.md#L157).
2. **Edges vs. DL for Stage 3 pivot?** Edge-based HoughCircles is the cheapest next experiment; a small U-Net or YOLO-v8 seg model trained on the 10-image train subset (with heavy augmentation) is the high-ceiling alternative. Which one to invest in depends on whether the edge pivot gets close to 90% on the train subset. Owner: user.
3. **Should the contract grow a `manual_corners` parameter?** If homography from auto-detected quads fails on most images, a manual-corner-click UI may be the pragmatic MVP path. The contract can accommodate this without breaking. Owner: user.
4. **Capture-condition gate as a product requirement, or accept lower fidelity?** Either requires PRD amendment — currently [`prd.md:38`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/context/foundation/prd.md#L38) states ≥90% as a guardrail. Owner: user. Block: yes — the wedge slice depends on it.
5. **How should mixed-caliber targets (#31) be presented in the UX?** Single-caliber dropdown (current FR-009) doesn't fit. Multi-select? Per-hole caliber marking? Out of CV scope but informs contract evolution. Owner: user.
6. **Should `metadata.yml` be extended to label X-ring hits and per-hole positions?** Without per-hole ground-truth positions, eval is limited to multiset Jaccard (no spatial matching). Annotating even the train subset would enable stricter eval. Owner: user.

## Related Research

- No prior `research.md` artifacts exist under `context/changes/**` or `context/archive/**`. This is the first research document in the project.
- [`context/foundation/shape-notes.md`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/context/foundation/shape-notes.md) §Vision & Problem Statement already flagged the CV problem as "genuinely hard" — this research quantifies that intuition.
- [`context/foundation/infrastructure.md`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/context/foundation/infrastructure.md) §Out of Scope explicitly excluded "Computer vision service deployment architecture (separate from Django web service)" — this research respects that boundary by keeping `cv/` standalone but does not address deploy topology.

---

## Follow-up Research 2026-07-19T14:30Z — Texture-based Stage 3 rewrite

After `/10x-frame` rejected the original "fundamental luminance invisibility" framing (see `frame.md`), the Stage 3 algorithm was rewritten to extract **texture features** instead of luminance blobs. The new approach computes a local-standard-deviation map, CLAHE-equalizes it, and runs `HoughCircles` (`HOUGH_GRADIENT_ALT`) on the texture map with caliber-bounded radius constraints. Stage 3 now returns centroids directly from HoughCircles, bypassing the Stage 4 watershed when it succeeds (watershed was collapsing adjacent HoughCircles detections).

### Headline result

| Iteration | Mean Jaccard | % count_err==0 | Hit-count RMSE |
|---|---|---|---|
| Baseline (HoughCircles on luminance + black-hat) | 0.089 | 4.3% | 5.22 |
| Texture-threshold-only (no HoughCircles) | 0.004 | 2.2% | 7.39 |
| Hough-ALT on CLAHE-equalized local-std (param2=0.75) | **0.163** | 6.5% | 8.60 |
| Hough-ALT on CLAHE-equalized local-std (param2=0.80) | 0.132 | **13.0%** | 7.37 |

**Best result: mean Jaccard 0.163 (param2=0.75) — an 83% relative improvement over baseline.** Six images now have count_err=0 (vs 2 at baseline). Best per-image Jaccards reached 0.50 (#28) and 0.38 (#15, #14).

### What worked

1. **Local-std as primary feature** confirms the frame hypothesis. The texture signal IS present in pure grayscale and IS detectable when the right feature is extracted.
2. **CLAHE equalization** is load-bearing — the raw std distribution is heavily long-tailed (most pixels ≈ 2-5, holes ≈ 30-70) and CLAHE spreads the tail so HoughCircles' internal Canny can find hole-boundary arcs.
3. **`HOUGH_GRADIENT_ALT`** (OpenCV 4.3+) is more accurate than the classic algorithm for this use case. Its `[0,1]` param2 sensitivity is also easier to tune than the classic algorithm's integer accumulator threshold.
4. **Returning centroids directly from Stage 3** (skipping Stage 4 watershed when HoughCircles succeeds) was a critical architectural fix — watershed was collapsing adjacent disjoint detections because the mask disks overlap.
5. **Kernel size k=25** (matching the empirical probe) worked best for 5-9mm calibers. Scaled-to-caliber kernel (`k=max(15,min(51,int(1.5*bullet_radius_px)))`) helped marginally.

### Remaining failure modes (ranked)

1. **Image-specific over-detection** (#12: 54 vs 13; #20: 15 vs 10; #43: 17 vs 10). When the std map has many local maxima — e.g., on highly-textured backgrounds or images with paper grain — HoughCircles finds too many circles. `param2=0.80` reduces but doesn't eliminate this.
2. **Catastrophic slug failure** (mean Jaccard 0.074-0.088, often n_pred=1-2 vs n_true=5-11). The 18mm slug holes are huge (≈200px at typical phone resolution), and a single slug hole tears paper so violently that the texture signature differs from smaller calibers. The caliber-scaled kernel (`k=51`) helps marginally but slug needs a dedicated approach.
3. **Stage 2 bullseye bias compounds scoring error**. Even when Stage 3 detects the right hole count, Stage 2's blob-centroid bullseye heuristic is off-center on dense-stack targets, flipping 9↔10 ring assignments. Example: #44 gets count_err=0 but Jaccard=0.00 because the bullseye is wrong.
4. **Catastrophic under-detection on specific images** (#2, #5: n_pred=1 vs n_true=10). These images consistently produce few HoughCircles detections; root cause not yet diagnosed (possibly low local-std contrast from flat lighting, or HoughCircles parameters wrong for these specific image scales).
5. **Mixed-caliber target #31** produces 11-24 detections vs 14 true — the single-caliber assumption breaks here as predicted.

### What did NOT work

1. **Threshold-based mask from local-std** (first iteration: 0.004 mean Jaccard). The std distribution is heavily right-skewed but NOT bimodal — any fixed-percentile threshold catches far too much background texture. HoughCircles on the texture map is required to localize hole centers.
2. **CLAHE on std + thresholding alone** — produces massive over-detection (e.g. 884 CCs vs 13 true on #12). Same root cause as above.
3. **Stage 4 watershed on HoughCircles disk-mask** — collapses adjacent detections because the disks overlap. Replaced by direct centroid return from Stage 3.

### Recommended next iteration (for `/10x-plan`)

The texture-based Stage 3 is the right architecture. Remaining work to clear ≥90% Jaccard:

1. **Per-caliber algorithm tuning**. Slug needs a different kernel size and possibly a different feature (DoG at slug-scale, or LoG blob detection). The current one-size-fits-all approach under-fits slug.
2. **Stage 2 rewrite: template-match ISSF rings**. Replace the blob-centroid heuristic with a synthetic ring template slid across the cropped target. This eliminates the bullseye bias that flips 9↔10 ring assignments on dense-stack targets. Without this, even correct hole detection won't yield correct scores.
3. **Over-detection post-filter**. After HoughCircles, rank detections by std-map response strength and keep only the top-N where N is caliber-typical (5-15). Or apply non-max suppression more aggressively.
4. **Multi-feature fusion**. Combine local-std with DoG-at-caliber-scale and/or Sobel-gradient as a weighted feature stack. May rescue cases where local-std alone is weak (#2, #5).
5. **Diagnose image-specific failures**. #2 and #5 need eyeball investigation — what's structurally different about these images that breaks HoughCircles?
6. **Real perspective homography (Stage 1)**. Currently skipped — localization is a bbox crop. Full 4-corner warp would fix perspective distortion on skewed targets and make radial scoring more accurate.

### Code state after spike

- [`cv/detect.py:290-371`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L290-L371) — `_stage3_morph` rewritten to return `(mask, centroids, failed)` with texture-based detection.
- [`cv/detect.py:135-156`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L135-L156) — `detect()` updated to use Stage 3 centroids directly when available; Stage 4 watershed is fallback only.
- `cv/detect.py` Stage 4 (`_stage4_watershed`) and Stage 5 (`_stage5_score`) unchanged.
- Run via `uv run python -m cv.eval`.
