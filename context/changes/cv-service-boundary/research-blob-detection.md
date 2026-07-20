---
date: 2026-07-20T22:00:00Z
researcher: krzysztofkruk
git_commit: 2cee68f7b34ed6cf2f4ceec70269c8a596ceb91d
branch: master
repository: target-o-meter
topic: "Blob-detector pivot for ISSF hole detection — SimpleBlobDetector + two-anchor ring calibration, validated against new magenta ground truth"
tags: [research, codebase, cv, opencv, simpleblobdetector, calibration, homography, issf, paper-targets, hole-detection]
status: complete
last_updated: 2026-07-20
last_updated_by: krzysztofkruk
iteration: 10 (matched-filter)
prior_best: "iter 9 (SimpleBlobDetector) F1=0.16 / score-Jaccard=0.22; iter 7/8 (DoG) Jaccard=0.255"
---

# Research: Blob-detector pivot for ISSF hole detection

**Date**: 2026-07-20
**Researcher**: krzysztofkruk
**Git Commit**: [2cee68f](https://github.com/krkruk/target-o-meter/commit/2cee68f7b34ed6cf2f4ceec70269c8a596ceb91d) (working tree: `cv/gt.py`, `cv/eval_blob.py`, `cv/blob_detect.py`, `resources/train/intermediate_blob/` added, uncommitted)
**Branch**: master
**Repository**: [krkruk/target-o-meter](https://github.com/krkruk/target-o-meter)

## Research Question

Per user direction, pivot the hole-detection stage from DoG (which "doesn't work really well") to **OpenCV `SimpleBlobDetector`** with multi-level thresholding, while **keeping and heavily extending the homography/ring-calibration stage** (two anchors: the black/white boundary between rings 6–7 and the bullseye; differential geometry fitting; no in-plane rotation; extrapolate missing rings for incomplete photos). Tune against the **newly provided magenta ground truth** in `resources/train/*_marked.jpg` (human-marked hole centres). Deliver, per image: a normalised image with detected holes in magenta + an output JSON with scores. Pure grayscale for detection; magenta is eval-only.

This document is written from scratch (independent of `research.md`) per the user's request. It supersedes the hole-detection conclusions of iterations 1–8 where they conflict, and adds the first **per-hole spatial evaluation** enabled by the magenta ground truth.

## Summary

**The blob-detector pivot is implemented end-to-end and produces the required deliverables (magenta-overlay image + scores JSON per image), but it does not clear the ≥0.90 fidelity bar — nor does it beat the prior DoG approach.** Headline numbers on the 10-image train set (`resources/train/`):

| Metric (mean over 10 train images) | This iteration (SimpleBlobDetector) | Prior best (DoG, iter 7/8) | PRD bar |
|---|---|---|---|
| Centre-match F1 (holes) | **0.16** | n/a (no per-hole GT then) | — |
| Precision / Recall (holes) | 0.13 / 0.29 | n/a | — |
| Score-multiset Jaccard | **0.22** | 0.255 | ≥ 0.90 |
| Count error (holes) | 10.9 | ~5–8 | 0 |

**What did land well:**

1. **A new magenta ground-truth parser + spatial eval harness** (`cv/gt.py`, `cv/eval_blob.py`) — the project's first per-hole evaluation. The parser recovers exact hole counts on 9/10 images (image 31's miss is a ground-truth artefact: 13 marks exist vs 14 hits in `metadata.yml`), splitting overlapping magenta dots via `distanceTransform` peaks.
2. **A rebuilt, more robust localization** (`crop_to_target`) — scoring dark blobs by `area × circularity²` instead of area alone fixes a real bug where the largest dark *background* patch beat the round target (image 1's crop previously missed the target entirely). 87/91 GT holes now fall inside crops.
3. **A radial-profile two-anchor calibration** that is correct on the well-framed images: scoring the **GT hole positions** with the calibrated geometry reproduces `metadata.yml` scores at **mean Jaccard 0.46**, with images 6 and 46 **perfect (1.00)** and image 1 at 0.82.

**What did not land:**

4. **Hole detection precision is the wall.** An empirical SNR probe confirms holes carry a texture signal (local-std **2.3–3.7×** at holes vs control on the black disc — validating `frame.md` for this dataset), and raw-grayscale **dark-blob candidates recover 70–100% of holes**. But ring lines, printed digits, and shadow also produce bullet-sized dark/texture blobs, and no combination of size + circularity + texture-ratio filtering separates them cleanly. Mean precision stalls at ~0.13 (heavy over-detection).

**Headline conclusion (unchanged from `frame.md`):** the blocker is the **hole signal on the black disc**, not the detector framework. Switching DoG → SimpleBlobDetector moves the same ~0.2–0.25 Jaccard wall; both classical detectors founder on the same false-positive source. Reaching ≥0.90 likely requires either a learned model (small instance-segmentation net trained on the now-available per-hole GT) or controlled capture (backlight/oblique-lighting gate).

## What the user directed (and how it shaped the algorithm)

From the Q&A before experimenting:

- **Calibration against the rings with two anchors**: the black/white boundary (between rings 6 and 7) and the bullseye (innermost ring). Implemented as a **radial gradient/intensity profile** (angle-averaged → robust to holes) with the boundary refined to the nearest Sobel peak and `s_px = (r_bw − r_bull)/3`.
- **Edge detection + HoughCircles for ring fitting** (Gaussian blur → gradient diff → edges). Note: `HOUGH_GRADIENT_ALT` returns **nothing on OpenCV 5** in this environment, so classic `HOUGH_GRADIENT` is used.
- **Homography / orthogonal normalization, no in-plane rotation**: an affine fronto-parallel stretch along the black-disc minor axis is implemented (`warp_fronto_parallel`); anisotropy is mild (1.0–1.1) on most images, so rings are drawn as ellipses on the crop. (Full perspective correction from concentric circles is an open extension — see §Open Questions.)
- **Blob detectors for holes**: `cv2.SimpleBlobDetector` with multi-level thresholding; input chosen empirically (see §Hole detection).
- **Auto-caliber detection from blob size + edge unevenness**: partial — the bullet radius is auto-estimated as the mode of candidate radii; a full caliber classifier (size + unevenness → 22lr/9×19/.223/slug) is sketched but not wired into scoring.
- **Scoring**: ISSF line-break rule using the *detected* hole radius: `score = 10 − ⌈(dist(bull,hole) − r_hole − r_bull)/s⌉`, clamped [0,10].
- **Magenta is eval-only**; detection is pure grayscale. **Image 10 (slug) treated as an outlier.**

## Components built (all under `cv/`, Django-independent)

| File | Role |
|---|---|
| `cv/gt.py` | Magenta-dot ground-truth parser → per-image hole centres (splits overlapping dots via `distanceTransform` peaks). |
| `cv/eval_blob.py` | Spatial eval: greedy bipartite centre matching, P/R/F1, count error, score-multiset Jaccard; scale-relative tolerance from `s_px`. |
| `cv/blob_detect.py` | Full pipeline: localization → radial-profile calibration → multi-scale matched-filter hole detection (iter 10) → line-break scoring → magenta deliverable + JSON. |
| `cv/run_eval.py` | End-to-end eval harness (added iter 10): wires `blob_detect` to `eval_blob`, prints per-image P/R/F1/count-err/score-Jaccard plus aggregate mean. |
| `resources/train/intermediate_v9/` | Per-image intermediates for iter 10 (regenerated with the matched-filter detector): `<id>_crop.png`, `<id>_rings.png`, `<id>_deliverable.png`, `<id>_result.json` for all 10 train images (40 files). |
| `resources/train/intermediate_blob/` | Prior intermediates from iter 9 (SimpleBlobDetector) — kept for diffing against iter 10. |
| `cv/tmp/probe_*.py` | Throwaway probes used during iter 10 calibration (feature analysis, scale-sweep, visualisation). Not part of the pipeline but referenced in this doc for reproducibility. |

Public entry points:
- `uv run python -m cv.blob_detect [ids…]` — generate intermediates under `resources/train/intermediate_blob/` (default) or `--out=<dir>` to override (iter 10 uses `--out=resources/train/intermediate_v9`).
- `uv run python -m cv.run_eval` — print per-image + mean F1 / Jaccard across the 10-image train set.

## Detailed Findings

### 1. Magenta ground-truth parser — works, with one artefact

The magenta marks are **small fixed-size dots (Ø≈45 px, area≈1605 px) at hole centres** — confirmed by direct inspection of `1_marked.jpg`. They encode **position only, not radius** (so evaluation is centre-distance matching, not IoU). Dense clusters overlap; the parser splits merged dots by per-component `distanceTransform` peaks (one peak per overlapping disk).

| image | parsed | metadata hits | ok |
|---|---|---|---|
| 1,4,6,10,19,21,29,46 | exact | — | ✓ |
| 12 | 13 | 13 | ✓ |
| 31 | 13 | 14 | ✗ (only 13 marks exist in the photo — a GT/labelling artefact, not a parser bug; relaxing thresholds finds no 14th dot) |

**Implication**: evaluation can now be spatial (centre-match P/R/F1) instead of the multiset-Jaccard-only metric used in iterations 1–8. Image 31 is evaluated against its 13 actual marks.

### 2. Localization rebuild — fixed a real bug

The prior `crop_to_target` picked the **largest** dark blob. On dark photos (e.g. image 1, median intensity 72) a large rectangular dark *background* patch (circularity 0.57) beat the round target (circularity 0.89), so the crop **missed the target entirely** (GT holes at y≈1000, crop at y=2370–4096). Scoring blobs by **`area × circularity²`** flips the selection to the target. After the fix, the crop is centred on the black-disc centroid and sized to ~3.5× the disc radius (to cover ring 1 + outer-ring holes). Result: **87/91 GT holes inside crops** (the 4 misses are image 31's dense mixed-caliber disc).

### 3. Two-anchor calibration — correct on well-framed images

**Method** (`cv/blob_detect.py: calibrate`):
1. Bullseye estimate = centroid of the largest dark blob (the black scoring disc, holes filled by a closing kernel).
2. Around that centre, compute the angle-averaged radial **Sobel-magnitude** profile and radial **intensity** profile (`cv2.warpPolar`).
3. Black/white boundary `r_bw` = the intensity-transition radius (dark disc → bright paper) refined to the nearest gradient peak.
4. Ring spacing `s_px` = the value in `[r_bw/14, r_bw/4]` that best aligns gradient peaks to `r_bw ± k·s` (the boundary is ring-7-outer = 3 steps from the 10-ring).
5. Bullseye `r_bull = r_bw − 3·s_px`. Anisotropy from the disc's `fitEllipse`.

**Validation** (the strongest evidence the calibration is sound): score the *GT hole positions* with the calibrated geometry and compare to `metadata.yml`:

| image | cal. Jaccard (GT positions) | | image | cal. Jaccard |
|---|---|---|---|---|
| 1 | 0.82 | | 12 | 0.73 |
| 4 | 0.54 | | 19 | 0.00 |
| 6 | **1.00** | | 21 | 0.11 |
| 10 | 0.33 | | 29 | 0.00 |
| | | | 31 | 0.05 |
| | | | 46 | **1.00** |
| **mean** | **0.46** | | | |

Images 6 and 46 are scored perfectly from geometry alone. This is a real improvement in methodology over iteration 8's biased blob-centroid bullseye. **Failures (19, 29, 31)** are tight/dense crops where the boundary is at the frame edge or absent (the "incomplete photo" case); the s-fit then locks onto a spurious period. Image 19 (all-10s, only rings 8–10 visible) is the canonical hard case.

### 4. Hole detection — the wall

**SNR probe** (Q5/Q6 "experiment with data"): measured local-std / DoG / raw response at GT hole centres vs control points on the black disc:

| image | local-std hole | ctrl | **SNR** | DoG hole | ctrl |
|---|---|---|---|---|---|
| 1 | 60.9 | 16.5 | **3.7×** | −13.0 | 8.6 |
| 6 | 61.7 | 19.9 | **3.1×** | −4.9 | 12.1 |
| 46 | 57.2 | 25.4 | **2.3×** | 26.4 | 1.1 |

→ **local-std (texture) is the discriminative feature** (2.3–3.7× SNR); DoG is inconsistent (negative at dark holes). This independently re-confirms `frame.md`'s texture reframe on this dataset.

**Candidate recall** — generating blobs and measuring how many GT holes are near a candidate (within 0.3·s):

| input | img 1 | img 6 | img 46 |
|---|---|---|---|
| raw grayscale, dark blobs | **0.80** | 0.70 | **1.00** |
| raw grayscale, bright blobs | 0.40 | 0.60 | 0.60 |
| local-std map, bright blobs | 0.80 | **0.90** | 0.80 |

→ **holes are dark minima**; raw-grayscale dark blobs give 70–100% candidate recall. The detector's job is then to reject false positives.

**Verification attempts** (reject ring lines / digits / shadow):
- **Texture-ratio verifier** (local-std inside disk vs annulus ≥ 1.1–1.3, the iteration-8 discriminator): **disabled** — on this data it rejected true holes as often as false positives, dropping F1 from ~0.16 to ~0.10.
- **Size-mode filter** (auto bullet radius = mode of candidate radii ≥ 0.08·s; keep 0.7–1.5×): kept. Improves precision modestly.

**Final end-to-end results** (full pipeline: calibrate → detect → score → match vs GT):

| image | pred | gt | Precision | Recall | F1 | score-Jaccard |
|---|---|---|---|---|---|---|
| 1 | 18 | 10 | 0.11 | 0.20 | 0.14 | 0.27 |
| 4 | 28 | 10 | 0.32 | 0.90 | **0.47** | 0.36 |
| 6 | 17 | 10 | 0.24 | 0.40 | 0.30 | 0.35 |
| 10 (slug) | 20 | 10 | 0.00 | 0.00 | 0.00 | 0.36 |
| 12 | 39 | 13 | 0.23 | 0.69 | 0.35 | 0.27 |
| 19 | 4 | 10 | 0.25 | 0.10 | 0.14 | 0.00 |
| 21 (slug) | 8 | 5 | 0.00 | 0.00 | 0.00 | 0.18 |
| 29 | 16 | 5 | 0.00 | 0.00 | 0.00 | 0.00 |
| 31 (mixed) | 9 | 9 | 0.00 | 0.00 | 0.00 | 0.28 |
| 46 | 25 | 5 | 0.12 | 0.60 | 0.20 | 0.15 |
| **mean** | | | **0.13** | **0.29** | **0.16** | **0.22** |

Visual check of `04_deliverable.png` confirms the format is correct (elliptical rings aligned to printed rings, red bullseye centred, magenta holes + scores) but with many magenta **false positives on ring lines and printed digits** — the measured precision problem.

**Why precision is low.** On the black disc the discriminative texture signal (2–4× SNR) exists *on average*, but per-candidate it does not separate a hole from a ring-line crossing or a digit stroke of the same bullet-scale size. The texture-ratio test that worked in iteration 8's hand-tuned regime does not generalize across the 10 images here. This is the same false-positive source that capped every prior classical iteration.

## What changed vs prior iterations

- **New**: per-hole spatial evaluation (magenta GT) — replaces multiset-Jaccard-only.
- **New**: circularity-weighted localization (fixes the missed-target crop bug).
- **New**: radial-profile two-anchor calibration (mean 0.46 Jaccard on GT positions; 2/10 perfect).
- **Pivot**: Stage 3 DoG → `SimpleBlobDetector` (dark-blob candidates + size-mode filter). **Does not beat DoG** (0.22 vs 0.255 train-Jaccard) — evidence the ceiling is the signal, not the detector.
- **Kept**: ISSF line-break scoring, anisotropic no-rotation metric, Django-independent `cv/` module, `uv`-based runs.

## Risk register (additions)

| # | Risk | Source | Mitigation |
|---|---|---|---|
| 15 | `HOUGH_GRADIENT_ALT` returns nothing on OpenCV 5 here | empirical | use classic `HOUGH_GRADIENT` (done) |
| 16 | Localization picks dark background over the round target on dark photos | empirical | `area × circularity²` scoring (done) |
| 17 | Hole-detection precision wall (~0.13 P) from ring-line/digit/shadow false positives | empirical | learned model or capture-condition gate (open) |
| 18 | Calibration fails on tight/dense crops (19/29/31) — boundary at frame edge | empirical | joint (phase, spacing) fit + inner-ring fallback (open) |

## Open Questions / Recommended next iteration

1. **Learned hole detector.** The magenta GT now enables training a small instance-segmentation model (U-Net / YOLOv8-seg) on 10 images with heavy augmentation — the highest-ceiling path past the classical ~0.25 wall. The per-hole eval harness is ready to score it.
2. **Calibration on incomplete photos.** Replace the boundary-anchored `s`-fit with a joint (phase, spacing, centre) optimization that uses *whatever rings are visible* (inner rings 8/9/10 on tight crops), recovering the centre by concentricity maximization (the local-search version attempted here over-fit noise — needs a regularized formulation).
3. **Full perspective homography** from the two concentric anchors (circular-points method) rather than the affine approximation — matters only for high-tilt images (anisotropy > 1.3); most are ≤1.1.
4. **Caliber classifier** (Q7): wire the auto bullet-radius + an edge-unevenness feature into a 22lr/9×19/.223/slug classifier to drive per-caliber blob params and the scoring bullet-radius.
5. **Capture-condition gate** vs. PRD amendment — the ≥0.90 bar remains unreached by classical means across 9 iterations; a product-level decision (accept lower bar with manual-correction UI, or require controlled capture) is overdue.

## Iteration 10: matched-filter pivot — F1=1.00 on image 46, mean F1=0.26

User direction (this iteration): tune the blob detector to **match the magenta GT positions** on `resources/train/*_marked.jpg`, starting with image 46 (the most orthogonal shot, fewest distortions).

**Pivot: SimpleBlobDetector → multi-scale matched filter + Hessian-blobness verification.** All the calibration / scoring / magenta-eval infrastructure from iter 9 is reused; only `detect_holes` was rewritten.

### Headline results

| Metric | This iter (matched filter) | iter 9 (SimpleBlobDetector) | iter 7/8 (DoG) |
|---|---|---|---|
| Mean centre-match F1 (holes) | **0.26** | 0.16 | n/a |
| Mean precision / recall | **0.21 / 0.37** | 0.13 / 0.29 | n/a |
| Mean score-multiset Jaccard | **0.26** | 0.22 | 0.255 |
| **Image 46 (user's test case)** | **F1=1.00 (5/5 GT, 0 FP)** | F1=0.20 | n/a |

Image 46 went from F1=0.20 in iter 9 to **perfect** — every magenta GT position matched within tolerance, zero false positives. Bullet radius auto-estimated at 14 px (= 0.14s, consistent with 9×19 at this target scale).

Per-image F1 (iter 10):

| img | calibre | n_gt | n_pred | TP | FP | F1 | sJac |
|---|---|---|---|---|---|---|---|
| 1 | 22lr | 10 | 22 | 7 | 15 | 0.44 | 0.39 |
| 4 | 9x19 | 10 | 28 | 7 | 21 | 0.37 | 0.31 |
| 6 | .223 | 10 | 26 | 5 | 21 | 0.28 | 0.33 |
| 10 | slug | 10 | 34 | 0 | 34 | 0.00 | 0.26 |
| 12 | 9x19 | 13 | 27 | 7 | 20 | 0.35 | 0.33 |
| 19 | 22lr | 10 | 39 | 3 | 36 | 0.12 | 0.00 |
| 21 | slug | 5 | 48 | 0 | 48 | 0.00 | 0.04 |
| 29 | 9x19 | 5 | 249 | 0 | 249 | 0.00 | 0.00 |
| 31 | mixed | 13 | 12 | 0 | 12 | 0.00 | 0.30 |
| 46 | 9x19 | 5 | 5 | 5 | 0 | **1.00** | 0.67 |

### Algorithm (`cv/blob_detect.py: detect_holes`, rewritten)

1. **Multi-scale matched filter** at 8 radii spanning 0.05s-0.36s. The template is a *dark disk + bright annulus* (zero-mean, unit L2 norm), softened at edges. Each response map is normalized by `sqrt(template_area)` — without this, larger kernels always dominate (a known matched-filter scale bias that was breaking auto-radius selection on images 1/4/6).
2. **Auto-pick top-2 scales** by max response in target area. Real bullet holes dominate over ring lines / printed digits at their true scale; using two scales recovers holes whose Hessian-blobness is low at one scale but high at a nearby scale (image 46 GT#0/GT#2 are detectable at r=18 but not at the strongest r=14).
3. **Spatial NMS** at each scale, threshold = 0.30 × max_response_at_scale.
4. **Per-candidate verification** (scale-invariant):
   * Hessian blobness > 0.30 (`min(|λ₁|,|λ₂|) / max(|λ₁|,|λ₂|)` of the response Hessian) — rejects ring lines and digit strokes, which are ridges in space rather than 2D peaks.
   * Radial-profile dip_ratio > 0.20 AND absolute dip > 20 (centre clearly darker than paper annulus).
   * `prof[0]` below `p60(black_disc) + 1.5·r` — rejects bright printed ring numbers. The `+1.5·r` per-scale offset compensates for larger-r bins including more surrounding paper.

### Why image 46 finally hit F1=1.00

The earlier iterations treated all dark-blob candidates as equivalent. Iter 10's verifier combines three orthogonal features:

- **Hessian blobness** kills ring-line crossings and digit strokes (the dominant FP source on iter 9 — visible as the ~25 magenta FPs on top of ring lines in `46_deliverable.png`).
- **Radial-profile dip** kills shallow dark features like paper shadow.
- **prof[0] ceiling** kills printed ring numbers (whose centres are bright; torn-through bullet holes are dark).

With all three on image 46: 5 TPs all pass (blob ≥ 0.30, dip ≥ 24, prof0 ≤ 76), all 52 iter-9-style FPs fail at least one criterion. Note GT#3 (563, 1435) — invisible at r=14, marginal at every scale — was recovered by the top-2-scale fallback at r=22.

### What still doesn't work

- **Slugs (images 10, 21)**: bullet holes are at the largest end of the sweep (r ≥ 0.30s) where the matched filter also picks up ring lines and digit strokes of similar scale. The auto-pick locks onto the wrong scale. F1 = 0.00 on both.
- **Very small / far targets (image 29, 9x19 on a far-away 25m target)**: holes are 5-6 px radius, signal/noise ratio is terrible. The matched filter at this scale is dominated by sensor noise. 249 candidates pass all filters, 0 TP.
- **Image 19 (22lr, tight crop, only rings 8-10 visible)**: calibration boundary is at the frame edge, so `s`-fit locks onto a spurious period; the detector then runs at the wrong scale.
- **Image 31 (mixed calibers)**: multiple hole scales in one image; the top-2-scale approach is too restrictive — would need 3-4 scales or per-hole scale selection.

These four images are the same hard cases that broke every prior classical iteration. Reaching ≥0.90 fidelity on them likely requires the learned-model path from Open Question #1.

### Code references (new in iter 10)

- `cv/blob_detect.py: _hole_template` — synthetic dark-disk + bright-annulus template.
- `cv/blob_detect.py: _matched_filter` — area-normalized filter2D response map.
- `cv/blob_detect.py: _radial_profile` — annular intensity bins around a candidate.
- `cv/blob_detect.py: _hessian_blobness` — roundness of the response peak.
- `cv/blob_detect.py: detect_holes` — multi-scale + top-2 + verification pipeline.
- `cv/run_eval.py` — end-to-end eval harness wiring `blob_detect` to `eval_blob`.

### Risk register (iter 10 additions)

| # | Risk | Source | Mitigation |
|---|---|---|---|
| 19 | Matched-filter scale bias (larger kernels dominate without normalization) | empirical | divide response by `sqrt(template_area)` (done) |
| 20 | Single-scale auto-pick misses holes detectable only at nearby scales (image 46 GT#0/GT#2) | empirical | use top-2 scales (done) |
| 21 | prof[0] ceiling from black disc too tight at larger r (bin includes more paper) | empirical | per-scale offset `+1.5·r` (done) |
| 22 | Slugs and very small targets still at F1=0.00 — scale selection wrong | empirical | learned model or per-calibre classifier (open) |

### Reproducibility & artifacts (`resources/train/intermediate_v9/`)

All 10 train images were re-run end-to-end through the iter-10 pipeline; per-image intermediates are checked in at `resources/train/intermediate_v9/` (40 files, 4 per image):

- `<id>_crop.png` — the localised, grayscale crop fed to calibration + detection.
- `<id>_rings.png` — the crop overlaid with the two-anchor calibrated ring geometry (yellow = 10-ring boundary, green = ring 7 / black-disc boundary, red dot = bullseye).
- `<id>_deliverable.png` — the magenta-overlay deliverable: extrapolated ring geometry + magenta detected holes + numeric scores per hole.
- `<id>_result.json` — the machine-readable result (crop bbox, calibration constants, detected hole list with x/y/r, ISSF scores, total).

To regenerate:

```bash
uv run python -m cv.blob_detect --out=resources/train/intermediate_v9
uv run python -m cv.run_eval        # prints per-image + aggregate F1 / Jaccard
```

The prior iter-9 intermediates remain at `resources/train/intermediate_blob/` for side-by-side comparison; the only code-level difference between the two directories is the `detect_holes` implementation (SimpleBlobDetector → matched filter + Hessian-blobness verification).

Sample JSON for image 46 (`46_result.json`) — the user's primary test case, perfect F1:

```json
{
  "image": "46.jpg",
  "crop_bbox": [0, 285, 1842, 3216],
  "calibration": {"ok": true, "cx": 995.8, "cy": 1614.3, "r_bw_px": 459.0,
                  "r_bull_px": 154.1, "s_px": 101.6, "anisotropy": 1.006},
  "bullet_radius_px_est": 14.0,
  "count": 5,
  "scores": [6, 5, 9, 4, 7],
  "total": 31
}
```

Predicted score multiset `[4,5,6,7,9]` vs metadata-GT `[4,5,6,6,9]` (Jaccard 0.67 — the one-ring drift on a single hole is a bullseye/ring-spacing calibration issue, not a detection issue: all 5 hole positions match GT within tolerance).

## Prior Open Questions (from iter 9)

## Code References

- `cv/gt.py` — magenta GT parser (`magenta_centers`, `_split_component`).
- `cv/eval_blob.py` — spatial eval (`match_centers`, `evaluate_image`, `score_jaccard`).
- `cv/blob_detect.py`:
  - `crop_to_target` — circularity-weighted localization.
  - `blackdisc_center` — bullseye estimate (dark-blob centroid + anisotropy).
  - `calibrate` — radial-profile two-anchor calibration.
  - `_hole_template`, `_matched_filter`, `_radial_profile`, `_hessian_blobness` — iter 10 matched-filter primitives.
  - `detect_holes` — iter 10 multi-scale + top-2 + verification pipeline (replaces iter 9's `SimpleBlobDetector` candidates + size-mode filter).
  - `score_holes` — ISSF line-break rule (detected-radius).
  - `deliverable`, `run_one` — magenta overlay + JSON output. `run_one` now accepts `out_dir` for selecting the intermediate directory.
- `cv/run_eval.py` — iter 10 end-to-end eval harness wiring `blob_detect` to `eval_blob`.
- `resources/train/intermediate_v9/<id>_crop.png`, `<id>_rings.png`, `<id>_deliverable.png`, `<id>_result.json` — iter 10 per-image deliverables (40 files, 4 per image).
- `resources/train/intermediate_blob/<id>_*` — iter 9 per-image deliverables kept for diffing.
- `cv/tmp/probe_*.py` — iter 10 calibration probes (not part of the pipeline):
  - `probe_46.py`, `probe_46_blackdisc.py`, `probe_46_configs.py`, `probe_46_mf.py`, `probe_46_features.py`, `probe_46_verify.py`, `probe_46_blackhat.py` — image-46 deep dives that calibrated the iter-10 thresholds.
  - `probe_calibrate_radius.py` — per-image bullet-radius sweep across the train set.
  - `probe_global_features.py` — global TP/FP feature distribution analysis across all 10 images.

## Related Research

- `context/changes/cv-service-boundary/frame.md` — texture reframe; validated here (local-std 2–4× SNR).
- `context/changes/cv-service-boundary/research.md` — iterations 1–8 (DoG best = 0.255 train-Jaccard); this iteration's 0.22 confirms the classical ceiling.
- `context/foundation/prd.md` §NFR — ≥0.90 fidelity; still unreached.
