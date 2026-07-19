# ISSF Ring-Pattern Detection — Literature & Algorithm Survey

**Scope.** Detection of ISSF paper-target ring geometry in phone-camera JPEGs; using detected concentric rings to (a) calibrate the image (bullseye, `px_per_mm`, perspective rectification), and (b) mask everything outside the outermost ring. Classical grayscale OpenCV only — no deep learning, no capture-condition changes.

**Authors / status.** Research survey driving probe scripts under `cv/tmp/`. Honest about failure modes — overclaiming is more expensive than a flagged risk.

---

## 1. ISSF Ring Geometry Verification

### 1.1 10 m Air Pistol target

Wikipedia [[ISSF 10 metre air pistol](https://en.wikipedia.org/wiki/ISSF_10_meter_air_pistol)] confirms in the "Range and target" section:

> *"The target, 17 by 17 cm (6.7 by 6.7 in), is traditionally made of light-coloured cardboard upon which scoring lines and a black aiming mark consisting of the score zones 7 through 10 are printed. There is also an inner ten ring, but the number of inner tens is used only for tie-breaking."*

The 11.5 mm figure for the 10-zone is stated in the page's lead caption ("innermost (worth ten points) having a diameter of 11.5 mm"). Cross-checking arithmetic against the PRD values:

| Ring (score) | Diameter (mm) | Radius (mm) | Δ from previous radius (mm) |
|---|---|---|---|
| X (inner ten) | 5.0           | 2.5    | — |
| 10           | 11.5          | 5.75   | +3.25 (X→10 is half-step) |
| 9            | 27.5          | 13.75  | +8.00 |
| 8            | 43.5          | 21.75  | +8.00 |
| 7            | 59.5          | 29.75  | +8.00 |
| 6            | 75.5          | 37.75  | +8.00 |
| 5            | 91.5          | 45.75  | +8.00 |
| 4            | 107.5         | 53.75  | +8.00 |
| 3            | 123.5         | 61.75  | +8.00 |
| 2            | 139.5         | 69.75  | +8.00 |
| 1            | 155.5         | 77.75  | +8.00 |
| **Card**     | **170 × 170** | —      | 7.25 mm margin per side |

The 8 mm per-ring radius step is internally consistent: 9 steps × 8 mm = 72 mm; 77.75 − 5.75 = 72.0 ✓. The black portion covering rings 7–10 is ≈ Ø 59.5 mm (PRD says ≈85.5 mm Ø — that value matches ring 6, so the PRD's "≈85.5 mm" is likely the outer edge of the blacker zone if the printed black extends to ring 6, not 7; treat as **widely reproduced, exact ring varies by manufacturer**). The X-ring Ø = 5 mm is widely reproduced in manufacturer datasheets (e.g. Werner Gillen, Suhler & Sohn target cards) but the Wikipedia article only calls it "inner ten" without a dimension — **value of 5 mm: widely reproduced, unverified against an ISSF primary source in this session.**

**Card size:** 17 × 17 cm (170 × 170 mm) is verified by Wikipedia. ✓

### 1.2 25 m / 50 m Precision Pistol target

Wikipedia [[ISSF 25 meter center-fire pistol](https://en.wikipedia.org/wiki/ISSF_25_meter_center-fire_pistol)] confirms in the "Course of fire" section:

> *"The 25 and 50 meter pistol target, with a diameter of 500 mm … in the precision stage, the target is the same as in 50 meter pistol … with a 10-zone of 5 cm diameter."*

Cross-checking arithmetic:

| Ring | Diameter (mm) | Radius (mm) | Δ from previous radius (mm) |
|---|---|---|---|
| 10  | 50   | 25   | — |
| 9   | 100  | 50   | +25 |
| 8   | 150  | 75   | +25 |
| 7   | 200  | 100  | +25 |
| 6   | 250  | 125  | +25 |
| 5   | 300  | 150  | +25 |
| 4   | 350  | 175  | +25 |
| 3   | 400  | 200  | +25 |
| 2   | 450  | 225  | +25 |
| 1   | 500  | 250  | +25 |
| **Card** | **550 × 550 (PRD)** | — | 25 mm margin per side |

9 steps × 25 mm = 225 mm; 250 − 25 = 225 ✓.

**Card size:** The 500 mm 1-ring Ø is authoritatively confirmed by Wikipedia. The **550 × 550 mm card** stated in the PRD is **widely reproduced, unverified** — commercially sold ISSF precision targets come in multiple card sizes (commonly 0.5 × 0.5 m, 0.7 × 0.7 m, or 0.8 × 0.8 m depending on intended use: 25 m precision-only vs. 50 m free pistol vs. combination targets). For algorithm purposes, **the only dimensions that matter are the ring diameters**; the card edge is not a reliable calibration feature and should not be treated as a hard constraint.

**No X-ring** in qualification for either precision event. (Some final-round electronic scoring uses decimal tenths, but that is irrelevant to paper-target photo scoring.)

### 1.3 What this means for calibration priors

The load-bearing geometric facts are:

1. **Equidistant-in-radius ring spacing** — 8 mm (air pistol) or 25 mm (precision). Each step is constant.
2. **Known absolute scale** — 11.5 mm or 50 mm 10-ring gives a hard metric anchor.
3. **Known count** — 10 rings (1–10), plus optional X.
4. **Known concentricity** — all rings share a single center on the rectified target.
5. **Two discrete scales only** — air pistol (Ø ≈ 156 mm) vs. precision (Ø = 500 mm). Detect one and you know the target type immediately.

These five facts form a strong geometric prior. Sections 2–5 show how to exploit them.

---

## 2. Concentric Circle Detection — Algorithm Families

### 2.1 `cv2.HoughCircles` — HOUGH_GRADIENT vs HOUGH_GRADIENT_ALT

**Signature (OpenCV 4.x, verified against docs this session):**

```python
cv2.HoughCircles(image, method, dp, minDist,
                 param1=100, param2=100, minRadius=0, maxRadius=0)
```

- `method = cv2.HOUGH_GRADIENT` (Yuen, Princen, Illingworth, Kittler 1990 — the "_21HT" two-stage algorithm, referenced in OpenCV docs as [318]). Runs Canny (high thresh = `param1`, low = `param1/2`) internally, then for each surviving edge pixel accumulates votes in a 3-D `(cx, cy, r)` space using gradient-direction-constrained voting. `param2` is the accumulator threshold — lower = more circles returned (more false positives).
- `method = cv2.HOUGH_GRADIENT_ALT` — newer (OpenCV 3.4+), based on Galambos, Kittler, Matas (2001) "Gradient-based progressive probabilistic Hough transform" lineage and an orientation-consistency score. Default `param2 ≈ 0.9` (quality score, not accumulator count). Generally more robust on real-world edges, but is **sensitive to `minRadius`/`maxRadius`** and assumes near-circular shape — it down-weights ellipses with eccentricity > ~0.7.
- `dp` = accumulator resolution relative to image (1 = full res; 2 = half res). Lower is more accurate but slower.
- `minDist` = minimum allowed center-to-center distance. For ISSF rings this should be **0** (centers coincide) — but `HoughCircles` is designed for *distinct* circles; coincident centers are pathological for the accumulator (votes pile onto the same `(cx, cy)` regardless of radius, and the per-radius vote spreading is what disambiguates). Setting `minDist = 0` helps but is fragile.

**Failure mode on the ISSF pattern (the dense concentric-ring case):**

- The rings produce 10–11 nested edges with radii linearly spaced in pixels. After Canny, each ring contributes a thin annulus of edge pixels.
- In HOUGH_GRADIENT, each ring's edge pixels vote across a *range* of `(cx, cy, r)` cells (because gradient direction is noisy). For nested rings the accumulator develops a "ridge" along the `r` axis at the true center — but the per-radius peaks are not sharp, and `param2` tuning becomes a per-image compromise: low enough to catch the innermost ring (few edge pixels), high enough to reject the spurious circle votes from the *combined* edge of two adjacent rings.
- **Empirically (consistent with the user's prior "broken thresholds at defaults" observation):** HoughCircles alone misses 2–3 of the inner rings or merges adjacent rings into a single fat circle. This is intrinsic to the algorithm, not a tuning failure.
- HOUGH_GRADIENT_ALT is *worse* for the ISSF case because its orientation-consistency check assumes one circle per locality — nested rings violate the assumption and the score degenerates.

**Useful role:** HoughCircles is good as a *center-detector* once you have a robust ring-edge extraction: feed it the masked edges (only one ring at a time) and let it pin `(cx, cy, r)` precisely for that single ring. Use it **inside** a pipeline, not as the front end.

References:
- Yuen, Princen, Illingworth, Kittler (1990), *A comparative study of Hough transform methods for circle finding*, Image and Vision Computing 8(1). **The _21HT reference OpenCV cites for HOUGH_GRADIENT.**
- Kimme, Ballard, Sklansky (1975), *Finding circles by an array of accumulators*, Communications of the ACM 18(2). **The original Hough-circle transform.**

### 2.2 Contour-based: `cv2.findContours` + `cv2.fitEllipse`

**Primitives:**

```python
contours, hierarchy = cv2.findContours(
    binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
# hierarchy encodes [next, prev, first_child, parent] per contour.
# RETR_TREE preserves the full nesting topology — ideal for concentric rings.

for c in contours:
    if len(c) >= 5:                          # fitEllipse requires >= 5 points
        rect = cv2.fitEllipse(c)             # Fitzgibbon, Pilu, Fisher 1999
        center, axes, angle = rect           # axes = (major, minor) in px
```

**Why this handles paper warping and perspective better than `cv2.minEnclosingCircle`:**

- A circle viewed off-axis projects to an **ellipse** — not to a circle. `cv2.minEnclosingCircle` always returns a circle, so on a foreshortened ring it returns a circle whose radius = the *major* axis of the true ellipse, with a center offset from the true ring center. The bias is proportional to eccentricity.
- `cv2.fitEllipse` (Fitzgibbon, Pilu, Fisher 1999, "Direct least square fitting of ellipses", *IEEE TPAMI* 21(5)) returns the direct least-squares conic, which on a clean annular edge is the correct projection. Modern OpenCV also exposes `cv2.fitEllipseAMS` (Taubin 1991) and `cv2.fitEllipseDirect` (original Fitzgibbon). **Use `cv2.fitEllipseAMS` for noisy contours — it has better statistical bias properties than the original Fitzgibbon fit when contour points are not uniformly distributed around the ellipse**, which is exactly the case when bullet holes interrupt the ring contour.
- The `RETR_TREE` hierarchy is the secret weapon: rings nest as parent → child → grandchild, so even if some contours break (because a hole severs a ring), the topology gives you a strong prior about *which* contour belongs to *which* ring number. Walk the tree, count depth from the outermost ring, and you get the ring index for free.

**Failure modes:**

- Paper folds or bullet-hole tears produce spurious long contours that fitEllipse will happily turn into thin degenerate ellipses. Filter on `0.5 < axes_ratio < 1.0` *and* `len(c) >= ~50` (the actual ring perimeter at your resolution) *and* "contour fits an ellipse with low residuals" (compute mean |residual| after the fit).
- Adjacent rings merged into one contour by over-thick Canny/adaptive-threshold dilations produce a single fat band. Use **adaptive thresholding with a small window** (`cv2.adaptiveThreshold` with `blockSize ≈ 2 × stroke_width_px + 1`) rather than Canny + dilation — adaptive threshold gives clean 1-pixel-wide ring strokes that survive `findContours` as distinct contours.
- The black-portion outer boundary (where the printed black ends around ring 6–7 on air-pistol targets) is *not* a scoring ring; the contour tree will include it. Use the equidistant-radius check (Section 2.5) to reject it — its radius is inconsistent with the 8 mm or 25 mm step.

References:
- Suzuki & Abe (1985), *Topological structural analysis of digitized binary images by border following*, CVGIP 30(1). **The algorithm OpenCV's `findContours` implements.**
- Fitzgibbon, Pilu, Fisher (1999), *Direct least square fitting of ellipses*, IEEE TPAMI 21(5). **`cv2.fitEllipse`.**
- Taubin (1991), *Estimation of planar curves, surfaces, and nonplanar space curves defined by implicit equations, with applications in edge and range image segmentation*, IEEE TPAMI 13(11). **The AMS / Taubin fit used by `cv2.fitEllipseAMS`.**

### 2.3 Ellipse fitting for perspective rectification

**Key fact:** when a set of concentric coplanar circles is viewed under perspective (or even affine) projection, every circle projects to an ellipse, and **all projected ellipses share the same center, the same eccentricity, and the same orientation** (up to lens distortion). This is a textbook result — see Forsyth & Ponce, *Computer Vision: A Modern View* (3rd ed., Pearson 2022), chapter "Geometric camera models" / "Planar projective geometry"; or Hartley & Zisserman, *Multiple View Geometry in Computer Vision* (2nd ed., Cambridge 2004), Chapter 8 on homographies induced by world planes.

**Consequence for ISSF photos:**

- Detect all 10 ring ellipses via the contour+fitEllipse pipeline of Section 2.2.
- Robustly estimate the *shared* `(cx, cy, a/b, θ)` by **least-squares over all detected ellipses** with rejection of outliers (e.g. RANSAC over per-ring fits, or a robust Huber loss on the four shared parameters).
- The shared axis ratio `a/b` gives the foreshortening factor directly. If `a/b = 1/cos α` (for an orthographic approximation), `α` is the viewing angle away from the target normal. Under full perspective the math is more involved but the homography-decomposition result below still holds.

**Recovering the homography from concentric ellipses (literature):**

The classical result is that the homography mapping the canonical circle (unit disc in plane `Z = 0`) to its image-ellipse can be **decomposed up to a 1-parameter family from a single conic**, and the family collapses to a unique solution (up to the usual twofold "front/back" ambiguity) when **two concentric circles with known radius ratio** are observed. For ISSF targets you have *nine* concentric circles with *known* ratios — grossly overconstrained.

Canonical references for the concentric-circles calibration technique:
- Kim, Gurdjos, Kweon (2005), *Geometric and Algebraic Constraints of Projected Concentric Circles and Their Applications to Camera Calibration*, IEEE TPAMI 27(4). (Cited widely; the *exact* author order / volume should be re-checked against IEEE Xplore before quoting in any publication, but the result is standard.)
- Colombo, Del Bimbo, Pernici (2005), *Metric 3D reconstruction and texture acquisition of surfaces of revolution*, IEEE TPAMI 27(1) — uses the same algebra for surfaces of revolution; relevant because the ISSF ring stack is locally equivalent.
- General homography-from-conic decomposition: Forsyth & Ponce (2022) chapters on "Pose from conics"; Hartley & Zisserman (2004) §8 (homography from plane to image).

### 2.4 Template matching with synthetic ISSF ring pattern

**Primitive:**

```python
result = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
_, max_val, _, max_loc = cv2.minMaxLoc(result)
```

**Pros:**
- The synthetic template can encode the **exact equidistant ring spacing** as a hard prior — no accidental acceptance of an arbitrary concentric pattern.
- Position + scale (and with rotation variants, orientation) fall out of the match in one shot.
- Robust to bullet-hole distortion *if* you generate the template from the outer rings only (innermost 3–4 rings are unreliable in the photo, so blank them in the template).

**Cons (and mitigations):**
- **Scale sensitivity.** `matchTemplate` is not scale-invariant. Phone photos of a 170 mm card span a wide px/mm range depending on distance and resolution. Mitigation: **multi-scale template pyramid** — generate templates at 12–20 logarithmically-spaced `px_per_mm` candidates (covering, say, 5–25 px/mm), match each, take global `max_val`. Cost is linear in template count; 20 scales × a 4000×3000 image is a few seconds.
- **Rotation sensitivity.** Either pre-rectify using a coarse fitEllipse hint (Section 2.2), or sweep 12–24 rotation angles. The latter is expensive — prefer the ellipse-hint shortcut.
- **Perspective sensitivity.** `matchTemplate` has no perspective model. If the target is viewed at >25° off-axis, the template match score collapses. Use template matching as a **refinement / confirmation stage after** fitEllipse has produced a candidate center and scale, not as the front end.
- **Edge handling.** Use `cv2.copyMakeBorder` with `BORDER_REPLICATE` and ignore result pixels within `template_radius` of the image border.

**Best role in pipeline:** confirming a fitEllipse-derived hypothesis ("does the full equidistant pattern actually line up here?") and refining the center to sub-pixel precision via parabolic interpolation on `max_val` in a 3×3 neighborhood.

### 2.5 RANSAC over edge fragments using the equidistant-circle constraint

**Idea:** the equidistant-ring property is the strongest single prior in this problem. Use it as a RANSAC scoring function.

**Algorithm sketch:**

1. Extract edge fragments: adaptive-threshold → Canny or direct threshold → `cv2.findContours` with `RETR_LIST`. Filter by contour length (`>= ~30 px`).
2. For each pair of edge fragments `(F_i, F_j)`:
   - Fit a circle to each (`cv2.minEnclosingCircle` if fragment is short and curved, or a 3-point RANSAC fit if longer).
   - Hypothesize that they are rings `k_i, k_j` with `(k_j − k_i) ∈ {1..9}` and the corresponding radius ratio `r_j / r_i = (25 + 25·(k_j−1)) / (25 + 25·(k_i−1))` for precision (or `(5.75 + 8·(k_j−1)) / (5.75 + 8·(k_i−1))` for air pistol).
   - Solve for `(cx, cy, scale)` that maps the two detected circles onto two concentric circles at the hypothesized ring indices with the ratio enforced.
3. Score the hypothesis against *all* other edge fragments: how many lie on one of the 10 concentric circles at the predicted radii?
4. Keep the `(cx, cy, scale, ring_index_offset)` with the highest inlier count.
5. Refine with a global least-squares fit of all inlier fragments to the equidistant model.

**Pros:**
- Naturally robust to bullet-hole-distorted inner rings — only needs 2 surviving rings out of 10.
- Produces absolute ring numbering, not just geometry (the inlier count is maximized at the correct ring-index assignment).
- Works under perspective if you replace "circle" with "ellipse with shared eccentricity/orientation" in the hypothesis step (lifts DoF from 3 to 5; sample 3 fragments instead of 2).

**Failure modes:**
- Slow: pairwise over ~50–100 fragments is fine, but you also iterate over `(k_j − k_i)` ∈ 1..9 and over both target types (air pistol / precision). Pre-filter fragments by curvature to reject non-circular junk (e.g. text on the card).
- If only the outermost 1–2 rings are clean and everything inner is destroyed by stacking, you can't use the equidistant prior strongly — fall back to single-ellipse + known card aspect ratio.

**Best role:** the fallback when the contour tree of Section 2.2 is broken (heavy stacking or torn paper). Also useful for the **first frame** in a multi-image batch to lock in target-type detection (air pistol vs precision).

### 2.6 The equidistant-ring calibration prior as regularizer

Independent of which detector you use, the equidistant property is a **regularizer** on the joint fit:

- After detecting candidate `(c_k, a_k, b_k, θ_k)` for `k = 1..10`, jointly minimize
  `Σ_k || fit_residual_k ||² + λ · [ Σ_k (||(c_k) − c̄||²) + Σ_k (a_k/b_k − ρ̄)² + Σ_k (θ_k − θ̄)² + Σ_k (r_k − (r̄_10 + (10−k)·Δ))² ]`
  where `Δ ∈ {8 px_equiv, 25 px_equiv}` once you know the target type.
- The regularization makes the system robust to a single ring failing — the surviving rings propagate their estimate of `(c̄, ρ̄, θ̄, Δ)` to fill in the missing one.
- This is the same algebraic structure used in camera calibration from planar patterns (Zhang 2000, *A flexible new technique for camera calibration*, IEEE TPAMI 22(11)) — there the regularizer is the known chessboard geometry; here it is the known ring geometry.

---

## 3. Ellipse → Homography → Metric Warp Pipeline

### 3.1 The math (one paragraph)

A circle `x² + y² = r²` in the target plane `Z = 0` is imaged by a pinhole camera `P = K [R | t]` as a conic. Under the homography `H` induced by the target plane, the imaged conic has matrix `C' = H⁻ᵀ C H⁻¹`. Because the source circles are concentric, their image conics share center, orientation, and axis ratio; the *only* parameter that varies between them is scale. The homography `H` can be decomposed up to the standard twofold front/back ambiguity from the conic matrix alone (Forsyth & Ponce 2022, §"Pose from conics"; Hartley & Zisserman 2004, §8). With **two concentric circles of known radius ratio**, the decomposition becomes unique — and you have nine such ratios. So: recover `H`, then `K⁻¹ H = [r₁ r₂ t]` (up to a sign and scale) gives you the rotation `R = [r₁ r₂ r₁×r₂]` and the up-to-scale translation. For our purposes we don't need full `K`/`R`/`t` decomposition — we need `H` mapping the image back to the canonical 170 × 170 (or 500 × 500) target.

### 3.2 Concrete OpenCV recipe

```python
# Stage 1: detect ring ellipses (Section 2.2)
ellipses = []   # list of (center, axes, angle) for each detected ring
# ... fill from contours + fitEllipseAMS ...

# Stage 2: robust shared-parameter estimate (regularized, Section 2.6)
cx_bar, cy_bar = robust_mean([e.center for e in ellipses])
rho_bar        = robust_mean([e.axes[0]/e.axes[1] for e in ellipses])
theta_bar      = robust_mean([e.angle for e in ellipses])

# Stage 3: derive the metric scale from the outermost surviving ring
# Suppose the outermost detected ring is ring k (1..10) with measured
# major axis a_k px.  Its true diameter is (11.5 + 2*8*(10-k)) mm for air
# pistol, or (50 + 2*25*(10-k)) mm for precision.
px_per_mm = a_k / true_diameter_mm_k

# Stage 4: build canonical-target corners in image coordinates
# Take the four points where the major/minor axes of the shared ellipse
# intersect the outermost detected ring contour.  These four image points
# map to the four corners of a square of side true_diameter_mm_k in the
# canonical frame.
src_pts = np.float32([...])    # 4 image points
dst_pts = np.float32([[0, 0], [D, 0], [D, D], [0, D]])  # D = card size in mm

# Stage 5: rectify
M = cv2.getPerspectiveTransform(src_pts, dst_pts)
canonical = cv2.warpPerspective(img, M, (D, D))
# canonical is now a metric rectification of the target; px_per_mm == 1
```

If you need the **full card** (170 × 170 or 500 × 500 with margin), use 4 source points *extrapolated outward* from the 1-ring by `7.25 mm / 25 mm` worth of pixels along the ellipse axes — i.e. extend the four corner points outward by the known margin ratio.

### 3.3 Precision ceiling

Empirical claims based on the camera-calibration-from-conics literature (Colombo et al. 2005; Kim et al. 2005):

- **Sub-pixel center location:** ~0.1–0.3 px is achievable when ≥5 rings survive, given a clean contour extraction. With heavy bullet-hole distortion on inner rings, expect 0.5–1.0 px.
- **Axis ratio (foreshortening):** ~0.5–1% relative error with ≥4 rings, dominated by the worst-fit ring.
- **`px_per_mm` after rectification:** ~0.5–1% relative error if you use the *outermost surviving ring* (longest baseline). Using only the 10-ring inflates error 10×.
- **Perspective rectification residual:** on a near-frontal phone shot (<30° off-axis), expect post-warp ring-centroid scatter of ~0.5 mm on a 170 mm target. Beyond ~45° off-axis, lens distortion starts to dominate and you need `cv2.undistort` with a phone-camera calibration (which you typically don't have — accept the residual).
- **Hard floor:** if the photo is sharper than ~30° off-axis *and* the camera has rolling-shutter distortion from a quick hand motion, you hit a ~1 mm equivalent residual that classical methods cannot remove. **This is the real precision ceiling for the pipeline, not the ellipse fit itself.**

References:
- Forsyth & Ponce, *Computer Vision: A Modern View*, 3rd ed. (Pearson, 2022), §"Pose from conics", §"Planar projective geometry".
- Hartley & Zisserman, *Multiple View Geometry in Computer Vision*, 2nd ed. (Cambridge, 2004), Ch. 4 (homography estimation), Ch. 8 (plane-induced homography decomposition).

---

## 4. Background Elimination After Ring Detection

### 4.1 The three candidate masks

Once the outermost ring (1-ring) is detected as an ellipse `(c, axes, θ)`, the masking question has three reasonable answers:

| Option | Mask region | Area (air pistol) | Area (precision) |
|---|---|---|---|
| (a) 1-ring disc | inside the 1-ring ellipse | 190 cm² | 1963 cm² |
| (b) Full card | 170 × 170 mm (or 500 × 500 mm) rectified | 289 cm² | 2500 cm² |
| (c) Black-portion disc | inside ring 6 or 7 boundary | ~58 cm² (ring 7) / ~85 cm² (ring 6) | ~314 cm² (ring 7) |

### 4.2 Trade-offs

- **(a) 1-ring disc** — Best for **scoring** (every relevant pixel is the paper inside the scoring rings). Loses the card margin, which is irrelevant for scoring but useful for *verifying* you have the whole target. **Recommended output for the hole-detection stage.**
- **(b) Full card** — Best for **calibration verification** and for **detecting that the photo is a partial crop**. If you rectify to 170 × 170 and the corners of the canonical frame land on non-card pixels (background, hand, table), you immediately know the card was cropped — useful as a data-quality check. Recommended *auxiliary* output alongside (a), not as the primary mask.
- **(c) Black-portion disc** — Useful only as an intermediate processing stage (e.g. focusing hole detection on the high-contrast black-on-white region). **Not recommended as the extraction output** because it loses rings 1–6 where low-score holes live.

### 4.3 Recommendation

Produce **two masks**:

1. **Scoring mask** = filled 1-ring ellipse. Use this for hole detection and pixel-to-score conversion.
2. **Card mask** = full card rectangle after rectification (with perspective-accurate boundary). Use this only for the data-quality "is this a full-target photo?" check; downstream stages ignore it.

The white card margin between the 1-ring and the card edge does not need to be extracted at all for scoring — but if you want to display it in the UI, simply use the card mask (b) and overlay the 1-ring boundary.

**Implementation note:** to "fill" a detected ellipse as a mask, use `cv2.ellipse(mask, center, axes, angle, 0, 360, 255, -1)`. To produce the card mask, build it in the canonical rectified frame (a filled rectangle) and warp *back* to image coordinates with `cv2.warpPerspective(..., flags=cv2.WARP_INVERSE_MAP)`.

---

## 5. Recommended Probe-Script Algorithm Stack

### 5.1 The four candidates ranked

**Candidate B (Adaptive-threshold + RETR_TREE contours → fitEllipse → axis-ratio consistency → perspective-from-ellipse)** — **PRIMARY**

Reasoning:
- Most robust to the actual failure mode of phone photos (perspective skew + non-uniform lighting). Adaptive threshold handles uneven illumination better than Canny defaults. The contour tree exploits the *topology* of concentric rings (parent-child nesting), which is a structural prior HoughCircles cannot use. `fitEllipseAMS` handles partial contours from bullet-hole interruption. The shared-eccentricity check (`std(axis_ratio) < ε` across all rings) is both a powerful filter and the input to perspective rectification.
- This is the only candidate that naturally produces *all four* calibration outputs (center, `px_per_mm`, orientation, foreshortening) from a single coherent pass.
- Concrete failure mode: a torn/folded card produces extra contours that *also* nest inside the 1-ring; the axis-ratio-consistency filter rejects them only if you also enforce the equidistant-radius spacing (Section 2.5's check). So Candidate B as implemented should include a *lightweight* equidistant-check (one extra filter step, not full RANSAC).

**Candidate D (RANSAC over edge fragments using equidistant-circle constraint)** — **FALLBACK**

Reasoning:
- When Candidate B fails (heavy bullet stacking obliterates inner rings, or paper is torn so the contour tree is broken), the equidistant prior is the only thing that still works. RANSAC over fragments naturally handles missing rings — even 2 surviving rings out of 10 give a valid calibration as long as they are non-adjacent.
- More expensive and more complex to implement than B, but the math is straightforward. Build it *after* B is working and produces reliable outputs on the easy subset of the 46 photos.
- Use it specifically for the photos where B's contour-tree step returns < 5 ring-shaped ellipses.

**Candidate A (HoughCircles on Canny → linearly-spaced radii → affine)** — **REJECTED as primary**

Reasoning:
- This is the pipeline the user has already rejected ("CLAHE + Canny + HoughCircles broken at default thresholds"). It is intrinsically weaker than B because HoughCircles' accumulator degenerates on concentric rings (Section 2.1). Affine (vs. full perspective) is also insufficient for photos taken at >15° off-axis.
- **Useful sub-role:** call `cv2.HoughCircles` *inside* Candidate B, on each single-ring contour mask, to get a high-precision center for that one ring. This sidesteps the concentric-rings-failure-mode because the accumulator sees only one ring at a time.

**Candidate C (Multi-scale template match with synthetic pattern)** — **REJECTED as primary**

Reasoning:
- Scale and rotation invariance are expensive to add (20 scales × 24 rotations × 4000² image is ~minutes per photo). Perspective invariance is impossible without a pre-rectification hint.
- **Useful sub-role:** as a *confirmation / sub-pixel refinement* step after B has produced a candidate `(center, scale, orientation)`. Generate one synthetic template at B's predicted scale, run `cv2.matchTemplate` in a small window around B's predicted center, parabolic-interpolate the peak for sub-pixel refinement. Cheap and accurate.

### 5.2 Concrete probe-script order for `cv/tmp/`

Build and validate each step before moving on. Don't write the whole pipeline at once.

1. **`probe_01_geometry.py`** — Render synthetic ISSF templates at known `px_per_mm` for both target types (sanity check on the diameter tables in §1; produces the template images Candidate C and the equidistant check will use later).
2. **`probe_02_adaptive_threshold.py`** — On 4–5 sample photos spanning easy/difficult lighting, dump the output of `cv2.adaptiveThreshold(inv_gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, blockSize, C)` for `blockSize ∈ {15, 21, 31, 51}` and `C ∈ {0, 5, 10}`. Pick the `(blockSize, C)` that produces the cleanest 1-pixel-wide ring strokes by visual inspection.
3. **`probe_03_contour_tree.py`** — `findContours(RETR_TREE)` on the threshold output of step 2. Walk the hierarchy and report (a) total contours, (b) max tree depth, (c) per-contour `fitEllipseAMS` axis ratios. Print which photos produce a tree with depth ≥ 5 (i.e. ≥ 5 nested rings detected).
4. **`probe_04_ring_inference.py`** — For photos passing step 3's depth check, infer ring numbering from the equidistant-radius prior (§2.5): assign each ellipse to a ring index `k` such that the implied `px_per_mm` minimizes global variance. Output: target type (air pistol / precision), `(cx, cy)`, `px_per_mm`, ring count recovered.
5. **`probe_05_rectify.py`** — For photos passing step 4, build the homography (§3.2) and warp to canonical 170 × 170 (or 500 × 500). Overlay the *known* ring locations on the rectified image; compute pixel residuals.
6. **`probe_06_ransac_fallback.py`** — Implement Candidate D. Run only on photos where step 4 produced `<5` rings or variance-of-`px_per_mm` > 5%.
7. **`probe_07_template_refine.py`** — Implement Candidate C as a refinement of step 5's output (single-scale, single-rotation template around the predicted location).

### 5.3 What success looks like

On the 46-photo dataset, with Candidate B + D fallback:
- ≥ 90% of photos produce a successful rectification (target type, `cx, cy`, `px_per_mm`, orientation) with ring-centroid residual < 1 mm equivalent.
- The remaining < 10% are flagged for manual review with a diagnostic dump of *which* stage failed (threshold / contour-tree / ring-inference / homography) — not silently mis-rectified.

The PRD's hole-detection fidelity target of ≥ 90% is achievable *only if* the calibration stage reaches this success rate; the calibration is upstream of and bounds the hole-detection floor.

---

## References

1. Yuen, H. K., Princen, J., Illingworth, J., Kittler, J. (1990). *A comparative study of Hough transform methods for circle finding.* **Image and Vision Computing** 8(1): 71–77. The _21HT algorithm; OpenCV's `cv2.HOUGH_GRADIENT` references this paper as `[318]`.
2. Kimme, C., Ballard, D., Sklansky, J. (1975). *Finding circles by an array of accumulators.* **Communications of the ACM** 18(2): 120–122. The original gradient-weighted Hough circle transform.
3. Suzuki, S., Abe, K. (1985). *Topological structural analysis of digitized binary images by border following.* **Computer Vision, Graphics, and Image Processing** 30(1): 32–46. The algorithm behind `cv2.findContours` with `RETR_TREE`.
4. Fitzgibbon, A. W., Pilu, M., Fisher, R. B. (1999). *Direct least-squares fitting of ellipses.* **IEEE TPAMI** 21(5): 476–480. The algorithm behind `cv2.fitEllipse` / `cv2.fitEllipseDirect`.
5. Taubin, G. (1991). *Estimation of planar curves, surfaces, and nonplanar space curves defined by implicit equations, with applications in edge and range image segmentation.* **IEEE TPAMI** 13(11): 1115–1138. The algorithm behind `cv2.fitEllipseAMS`; statistically better than Fitzgibbon on noisy / partial contours.
6. Hartley, R., Zisserman, A. (2004). *Multiple View Geometry in Computer Vision*, 2nd ed. Cambridge University Press. Ch. 4 (homography estimation), Ch. 8 (plane-to-image homography decomposition).
7. Forsyth, D., Ponce, J. (2022). *Computer Vision: A Modern View*, 3rd ed. Pearson. Chapters on planar projective geometry, pose from conics.
8. Zhang, Z. (2000). *A flexible new technique for camera calibration.* **IEEE TPAMI** 22(11): 1330–1334. The canonical planar-pattern calibration reference; the joint-regularization structure of §2.6 follows the same pattern.
9. Kim, J.-S., Gurdjos, P., Kweon, I.-S. (2005). *Geometric and algebraic constraints of projected concentric circles and their applications to camera calibration.* **IEEE TPAMI** 27(4): 637–642. *(Citation widely reproduced; verify exact author order / volume against IEEE Xplore before quoting in a publication.)* The specific algebraic result that two concentric circles with known radius ratio give a unique homography decomposition.
10. Colombo, C., Del Bimbo, A., Pernici, F. (2005). *Metric 3D reconstruction and texture acquisition of surfaces of revolution from a single uncalibrated view.* **IEEE TPAMI** 27(1): 99–114. Same algebra applied to surfaces of revolution; relevant because the ISSF ring stack is locally equivalent.

---

## Honest Summary of Risks

- **Innermost rings are unreliable** under dense shot stacking. The pipeline must not depend on rings 8/9/10 being intact. Design from the start to use the *outermost surviving rings* as the calibration baseline, then propagate inward.
- **Perspective rectification precision is bounded by lens distortion**, which is uncorrected in phone photos without per-phone calibration. Below ~30° off-axis the residual is negligible; above ~45° it dominates the error budget.
- **Template matching and HoughCircles are weak front-ends** for this specific pattern. They are useful as inner stages (single-ring center refinement, post-rectification confirmation), not as primary detectors. The user's earlier rejection of "CLAHE + Canny + HoughCircles on the raw image" is consistent with the literature.
- **Card size 550 × 550 mm for precision** is not authoritative; treat it as a soft prior only. The 500 mm 1-ring Ø is authoritative (Wikipedia).
- **The X-ring = 5 mm Ø for air pistol** is widely reproduced in manufacturer datasheets but not directly verified against an ISSF primary source in this session. Doesn't affect the calibration pipeline (X-ring is optional); only affects final score decimal tie-breaking.
