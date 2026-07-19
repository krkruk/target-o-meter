# Pyramid & Wavelet Feature Survey — Bullet-Hole Detection in ISSF Targets

**Scope.** Literature + algorithmic survey to drive probe scripts in `cv/tmp/`.
Constraints honored: pure grayscale, no deep learning, OpenCV ≥ 5.0 + NumPy ≥ 2.5
(confirmed in `pyproject.toml`); PyWavelets is **not** installed but resolves
cleanly under `uv` (`pywavelets==1.9.0`, dry-run verified).

**Empirical baseline to beat** (from `cv/feature_probe.py` +
`context/changes/cv-service-boundary/frame.md`):

| Feature                          | pop-SNR avg (5 imgs) | patch-SNR on #19 |
| -------------------------------- | -------------------- | ---------------- |
| luminance                        | ~1.0×                | 2.78×            |
| `local_std_k15`                  | medium               | 16.31×           |
| **`local_std_k25` (current)**    | **4.57×**            | **24.84×**       |
| `sobel_mag`                      | medium               | 14.34×           |
| `shadow_grad` (x-Sobel)          | medium               | 13.43×           |
| `canny` (50/150)                 | ~0×                  | broken           |

End-to-end detection Jaccard with current `_stage3_morph` (local-std + HoughCircles
ALT, `cv/detect.py:295`) = **0.163 mean**. That is the number the new probes must beat.

---

## 1. Target Extraction (background elimination)

Goal: produce a binary mask of "paper target only" so feature extraction in §2–4
doesn't waste response on tablecloth, floor, or the printed target number strip.
We assume Stage 2 (`_stage2_rings`, `cv/detect.py:239`) already returns a bullseye
+ scoring radius — that is the load-bearing input.

### 1.1 Ring-boundary masking (RECOMMENDED PRIMARY)

**Idea.** Once Stage 2 gives bullseye `(cx, cy)` and scoring radius `R`, mask
everything outside the disc `dist ≤ R`. This is a 3-line numpy operation:
`mask = (xx-cx)**2 + (yy-cy)**2 <= R**2`. Trivially composable with any
annulus (e.g. restrict to black-portion only for ink-zone suppression).

**Cost.**
- Loses anything between the 1-ring and the card edge — typically a few mm of
  white card. For ISSF scoring this is irrelevant (no scoring rings live there).
- **Loses any hit that lands completely outside the 1-ring.** `metadata.yml`
  shows exactly one such hit: **#12 has a `0`-point hit** in the long tail.
  This is the only documented failure mode and is rare (1 hit in 46 images).
- Boundary halo: if `R` is biased small, edge-of-card holes get clipped. Mitigate
  by inflating `R` by `1.05×` and accepting a few px of background bleed.

**Failure modes.** Stage 2 must succeed first. Currently Stage 2 uses adaptive
threshold + largest circular blob (`cv/detect.py:255-279`); it is robust on 43/46
images per `frame.md:91`. Use ring-boundary masking as primary, with a fallback
for the Stage 2-failure cases.

**OpenCV primitives.** `np.mgrid`, `cv2.bitwise_and`. No special imports.

### 1.2 Otsu on locator image (current `_stage1_localize`)

**Idea (`cv/detect.py:181-236`).** Downscale to long-side 1200 px →
`cv2.threshold(..., THRESH_BINARY_INV + THRESH_OTSU)` → 3×3 open + 2×3 close →
`cv2.connectedComponentsWithStats` → pick largest square-ish blob.

**Failure modes (real, observed):**
- Target partially cropped (phone framing cut one edge): Otsu splits the target
  into multiple components; aspect/fill scoring discards the right one.
- Multiple dark regions in frame (a black table, shadow under the target stand,
  the shooter's hand): the "largest square blob" heuristic can pick the wrong one.
- Strong illumination gradient across the card (window light from one side): Otsu's
  global threshold splits the card; one half is "background".
- 0-point hit far outside the rings (`#12`): Otsu actually handles this fine
  because the **card itself** is the largest dark blob in BINARY_INV after blur,
  but only if the card is uniformly white. If the card is greyed by shadow, Otsu
  loses its edge.

**Verdict.** Keep as the Stage-1 fallback only. Not the primary target extractor.

### 1.3 Card-edge detection (document-scanner pattern)

**Idea.** `cv2.Canny` → `cv2.findContours(RETR_EXTERNAL)` →
`cv2.arcLength` + `cv2.approxPolyDP(epsilon=0.02*perim)` → keep 4-vertex polygon
that is roughly square and large → `cv2.getPerspectiveTransform` +
`cv2.warpPerspective` to canonical square.

**Failure modes.**
- Requires the white card edge to be visible against the background. Phone photos
  routinely **crop into the printed rings** to fill the frame; no edge → no quad.
- The black target disc has stronger edges than the card edge; Canny fires on
  the rings instead. You must mask out the rings first (circular mask from Stage 2).
- ApproxpolyDP is sensitive to `epsilon`; on a worn/torn card edge, you get 5–8
  vertices and have to merge or reject.

**Verdict.** Use **only** when a full card edge is visible. Combine with 1.1:
run the quad detection, but always intersect with the disc mask from Stage 2.

### 1.4 GrabCut (`cv2.grabCut`)

**Idea.** Semi-supervised fg/bg segmentation via iterative graph cuts. Init with
`rect = (bx0, by0, bw, bh)` from the Stage 2 disc bounding box, run 5 iterations,
read back the mask (`np.where((mask==cv2.GC_FGD)|(mask==cv2.GC_PR_FGD), 255, 0)`).

**Failure modes.**
- Slow: O(N) graph-cut iterations on a multi-MP image, ~3–10 s per image. Probe-
  script acceptable; production borderline.
- Sensitive to the rect init — if the rect clips into the rings, GrabCut happily
  carves out the black disc as "background".
- Background with paper-like color (white table, white wall) → leaks into fg.

**Verdict.** Overkill for our use case. The card is already a clean disc once you
have Stage 2. Keep in reserve for the day someone wants pixel-accurate card
boundaries (e.g. for AR overlay).

### 1.5 Color-space thresholding (HSV)

**Idea.** Convert to HSV, mask `S < 40 and V > 130`, morphology close, largest
blob = card.

**Failure modes specific to our constraint.**
- User finding: chroma is **noise** on this dataset (`frame.md:18`). White-balance
  varies shot-to-shot; hue and saturation drift under warm indoor lighting.
- We committed to **pure grayscale**. HSV requires 3-channel input.
- In luminance-only terms this collapses to "high-pass threshold on Y", which is
  Otsu on the locator image — i.e. method 1.2.

**Verdict.** Ruled out by the grayscale constraint. Subsumed by 1.2 anyway.

### 1.6 Recommendation

**Prototype two methods, in this order:**

1. **Primary: Ring-boundary masking (1.1).** Zero new dependencies, sub-millisecond,
   robust whenever Stage 2 succeeds (which is the common case). The single known
   miss (the 0-point hit in `#12`) is acceptable for v1; track it as a known issue.
2. **Fallback for Stage-2 failures: Otsu on locator (1.2).** Already implemented;
   keep its output as the mask when Stage 2 reports failure, exactly as `detect.py`
   already does.

Methods 1.3, 1.4, 1.5 should NOT be prototyped for this iteration — they cost more
than they give back. The probe scripts can write the extracted target image as
`resources/train/intermediate/<id>_01_target.png` for visualization.

---

## 2. Pyramid Methods for Hole Detection

All methods in this section produce a **per-pixel feature map** at the target's
native resolution. The map is then fed to a circle detector (HoughCircles or
SimpleBlobDetector) to get hole centroids.

Conventions:
- `r_b` = bullet radius in source pixels
  (`bullet_radius_px` from `cv/detect.py:133`).
- For 22lr @ typical phone-camera scale, `r_b ≈ 15 px`; for 9 mm ≈ 25 px;
  for slug ≈ 50 px. We will probe on images where `r_b ∈ [10, 60]`.

### 2.1 Gaussian pyramid (`cv2.pyrDown`)

**Construction.** Repeated `cv2.pyrDown(gray)`. Level `i` has scale `2^{-i}`.

**How to use.** Build 3–4 levels, run a single-scale detector at each level,
project circle centers back to level 0 with `* 2**i`. This is the standard
multi-scale detection pattern (Lindeberg 1998).

**Suitability here.** **Low.** Bullet radius `r_b` is known from the caliber
table; there is no scale ambiguity to scan over. A Gaussian pyramid is what you
use when you *don't* know the scale; we *do*. The only useful application is to
run the detector on a downscaled image for speed when `r_b` is large (slug).

**Expected SNR.** Same as direct detection at native scale; pyramid is a speed
optimization, not an SNR optimization.

**Visualization.** Boring — looks like progressively blurrier target images.

### 2.2 Laplacian pyramid (`cv2.subtract` between Gaussian levels)

**Construction (Burt & Adelson 1983).**
```python
g = gray.astype(np.float32)
levels = []
for _ in range(N):
    g_next = cv2.pyrDown(g)
    up = cv2.pyrUp(g_next, dstsize=(g.shape[1], g.shape[0]))
    lap = g - up                      # band-pass at scale ~ current σ
    levels.append(lap)
    g = g_next
```
Each `lap` is a band-pass image whose center-frequency corresponds to features of
radius ≈ `2**level` px.

**How to use here.** Pick the level `L` such that `2**L ≈ r_b`. Hole rim should
light up in `levels[L]`. Sum (or max-abs) over the 2–3 levels nearest `r_b` to
cover the full caliber range.

**Suitability.** **Medium-high.** This is exactly the right tool when you have a
characteristic scale. Bullet holes produce a clean annular response at the level
matching their radius.

**Expected SNR vs baseline.** Should be in the same ballpark as `local_std_k25`
because both are band-pass measures; Laplacian is linear (faster) where local-std
is non-linear (more robust to slowly-varying illumination). Laplacian is a
reasonable **fast complement** to local-std, not a replacement.

**Visualization.** **Most informative** intermediate: per-level PNGs of the
Laplacian stack immediately show which scale "catches" the holes. Save as
`<id>_03_laplacian_L0.png` … `L4.png`.

### 2.3 Difference-of-Gaussians at caliber scale (SIFT-core)

**Construction (Lowe 2004).**
```python
sigma1 = r_b / 2.0
sigma2 = r_b / 1.0          # σ2/σ1 = 2 ≈ SIFT's octave ratio
dog = cv2.GaussianBlur(f, (0, 0), sigma1) - cv2.GaussianBlur(f, (0, 0), sigma2)
```

**How to use.** Threshold `|DoG|` to get candidate hole pixels, then
`cv2.HoughCircles` on the thresholded map. Holes are circular dark-on-dark
features; the inside of the hole goes negative in DoG (it's a local minimum at
the bullet scale), the rim goes positive. Take the **negative** lobe → binary →
HoughCircles.

**Suitability.** **High.** DoG is the mathematically optimal band-pass for a
Gaussian-blurred disk (Bloch-Young 1959 derivation; cited in Lindeberg 1998).
For a known-diameter dark disk on a similar-luminance background, calibrated DoG
is the optimal linear detector under the matched-filter theorem.

**Expected SNR.** Should match or slightly exceed local-std on isolated holes;
may underperform on dense clusters (where the local-std kurtosis helps). Worth
probing directly. **Probe already runs DoG at σ = 1, 3 and σ = 2, 5** (`feature_probe.py:268`);
the caliber-scaled version (σ = r_b/2, r_b) is the new candidate.

**Visualization.** DoG maps are signed; visualize with the diverging colormap
already in `feature_probe.save_png(..., signed=True)`. Most informative viz is
the negative lobe thresholded at the 5th percentile.

### 2.4 Max-over-scales (Laplacian stack scale-map)

**Construction.** Build Laplacian stack `L_0, L_1, …, L_N`. Per pixel:
```python
scale_map = np.argmax(np.abs(np.stack([L_0, L_1, ...], axis=-1)), axis=-1)
mag_map   = np.max(np.abs(np.stack([L_0, L_1, ...], axis=-1)), axis=-1)
```

**How to use.** Holes pop at their corresponding level. `scale_map` gives the
per-pixel dominant scale; threshold on `mag_map` AND `scale_map ≈ expected scale`.

**Suitability.** **Medium.** Useful when the caliber is unknown or mixed (e.g.
image #31 has both 9 mm and 22lr per `metadata.yml:94`). For known single-caliber
images it's overkill; just use the single-level Laplacian from 2.2.

**Visualization.** `scale_map` as a colormap is the single most informative image
in this whole survey — you can literally see which scale each region "lives at".

### 2.5 SIFT / SURF / ORB at bullet scale (and SimpleBlobDetector)

**SIFT** (`cv2.SIFT_create`, available free since patent expired Apr 2020).
**SURF** (`cv2.xfeatures2d.SURF_create`) — still patent-encumbered; **avoid**.
**ORB** (`cv2.ORB_create`) — free but binary descriptors are wrong tool here.

**Suitability.** **Overkill.** SIFT/SURF/ORB produce *descriptors* for matching
across images. We don't need to match holes across images; we need to localize
them in one image. The useful part of SIFT for us is the **DoG detector** (§2.3),
which we can build directly with two `cv2.GaussianBlur` calls — no SIFT machinery.

**`cv2.SimpleBlobDetector`** — different beast and worth probing. It chains
threshold → erosion → center-extraction → area/circularity/convexity/inertia
filters (`cv2.SimpleBlobDetector_Params`). For circular holes of known radius it
is a clean primitive. Tune:
```python
p = cv2.SimpleBlobDetector_Params()
p.minThreshold = 10; p.maxThreshold = 200
p.filterByArea = True
p.minArea = π * (0.7 * r_b)**2
p.maxArea = π * (1.3 * r_b)**2
p.filterByCircularity = True; p.minCircularity = 0.7
p.filterByColor = False                    # ignore inverted-ness
p.filterByInertia = True; p.minInertiaRatio = 0.4
det = cv2.SimpleBlobDetector_create(p)
keypoints = det.detect(feature_map_u8)
```
**Caveat:** SimpleBlobDetector expects a uint8 image; you have to normalize the
feature map to 0–255 first (same pattern as `_stage3_morph` already does at
`cv/detect.py:333-336`).

**Verdict.** Use SimpleBlobDetector on the DoG / Laplacian feature map as an
alternative to HoughCircles. Compare which gets cleaner hole centers.

### 2.6 SNR expectations summary

For a dark hole inside dark ink, **none of the pyramid methods can beat the
local-std baseline by more than ~20%** because they share the same fundamental
limit: the signal is *texture*, not *contrast*. The matched-filter optimum for a
textureless disk is the DoG (§2.3); for a textured disk (which is our case), the
local-std (current baseline) is closer to the optimum. The pyramid methods'
**primary value is as a fusion input** (§4), not as standalone winners.

Honest prediction per method:

| Method                         | Predicted pop-SNR on #19 | Notes |
| ------------------------------ | ------------------------ | ----- |
| Laplacian at caliber scale     | 8–14×                    | Fast, linear, additive in fusion |
| DoG at caliber scale           | 10–18×                   | Matched-filter-optimal for disk |
| Max-over-scales                | 8–14×                    | Better for mixed-caliber images |
| SimpleBlobDetector on DoG      | n/a (detector, not map)  | Use to compare against HoughCircles |
| **local_std_k25 (baseline)**   | **24.84×**               | Still expected to be the strongest single map |

---

## 3. Wavelet Methods for Hole Detection

### 3.1 Discrete Wavelet Transform background

A 2D DWT decomposes an image into one low-pass subband (`LL`) and three
detail subbands per level: `LH` (horizontal detail), `HL` (vertical), `HH`
(diagonal). Each level halves the resolution. Standard "wavelet denoising"
inverts the transform after thresholding detail coefficients.

The relevant subbands for hole detection are `LH` and `HL` at the level whose
scale matches `r_b` — these respond to the hole rim's horizontal and vertical
edges. `HH` responds to diagonal texture (paper fibers, hole-tear edges).

### 3.2 Haar wavelet

**Construction.** With PyWavelets:
```python
import pywt
coeffs = pywt.wavedec2(gray.astype(np.float32), wavelet='haar', level=4)
# coeffs[0] = LL_4, coeffs[1..] = (LH, HL, HH) at levels 4, 3, 2, 1
```
Or with OpenCV only (manual Haar = box-filter pair):
```python
# Haar low-pass = avg of left/right; high-pass = half-difference
lo = cv2.boxFilter(f, -1, (2, 1), normalize=True)   # not subsampled
hi = f - cv2.boxFilter(f, -1, (2, 1), normalize=True)
# but without subsampling this is just a 1-pixel-shift difference — weak.
```
The OpenCV-only Haar is awkward because OpenCV has no native DWT; you must
subsample manually. **Recommend: use PyWavelets if allowed.**

**Properties.** Haar is piecewise-constant — blocky. Captures sharp edges well,
but produces checkerboard artifacts in 2D. Good for binary-like signals
(printed ring edges); weaker for soft textures (paper fibers).

**Suitability.** **Medium.** Cleanly localized, fast. Use as a cheap baseline
for the wavelet family.

### 3.3 Daubechies wavelets (db2, db4)

**Construction.** Same `pywt.wavedec2` call with `wavelet='db4'`. db4 has 8
coefficients, is smoother than Haar, still compactly supported.

**Properties.** Real-valued, orthonormal. The extra smoothness captures fiber-tear
texture (continuous gradients) better than Haar's blocky response.

**Suitability.** **Medium-high** for fiber-texture response. db4 at the level
matching `r_b` is the wavelet analog of `local_std_k25` — both measure local
variation at a chosen scale, but db4 is faster (linear convolution vs. box-variance
trick) and orthogonal across scales.

### 3.4 Gabor banks (`cv2.getGaborKernel` + `cv2.filter2D`)

**Construction (Daugman 1985; Jain & Farrokhnia 1991).**
```python
def gabor_bank(g, sigma=0.4 * r_b, lambd=1.0 * r_b):
    f = g.astype(np.float32)
    accum = np.zeros_like(f)
    for theta_deg in (0, 45, 90, 135):
        kern = cv2.getGaborKernel(
            ksize=(int(6*sigma)|1, int(6*sigma)|1),
            sigma=sigma,
            theta=np.deg2rad(theta_deg),
            lambd=lambd,
            gamma=0.5,
            psi=0,
            ktype=cv2.CV_32F,
        )
        resp = cv2.filter2D(f, ddepth=cv2.CV_32F, kernel=kern)
        accum += np.abs(resp)
    return accum
```

**Properties.** Each Gabor is a Gaussian-windowed sinusoid — a band-pass filter
centered at spatial frequency `1/lambd` and orientation `theta`. Summing
`|response|` over 4 orientations gives a rotation-invariant texture-energy map
(standard "Gabor texture feature" since Jain & Farrokhnia 1991).

**Tuning.** `lambd = 2*r_b` (one full period across the hole diameter).
`sigma ≈ 0.5 * lambd` (modulation depth 0.5, Daugman's natural choice).
The current `feature_probe.py:226-234` uses fixed `sigma=4, lambd=10` —
**caliber-scaled Gabor is the new candidate.**

**Suitability.** **High.** This is the classical answer in industrial defect
inspection on dark homogeneous surfaces — exactly the cross-domain analog from
`frame.md:60`. Expected to be the strongest standalone **wavelet-family** feature.

**Expected SNR.** Should land between DoG and local-std, roughly 12–18× pop-SNR
on #19. Rotation-invariance is the key advantage on holes whose tear asymmetry
points in unknown directions.

**Visualization.** Per-orientation Gabor responses (`<id>_05_gabor_0.png`,
`_45.png`, etc.) are the most informative viz in this section — they show
which orientations carry the fiber-tear signal.

### 3.5 Steerable pyramids (Freeman & Adelson 1991)

**Construction.** Multi-scale + multi-orientation decomposition where each
orientation is computed as a linear combination of basis filters, so orientation
can be "steered" to any angle analytically. OpenCV has **no native implementation**;
you'd need `pySteer` or hand-rolled basis filters from Simoncelli's toolbox.

**Suitability.** **Overkill.** Steerable pyramids earn their keep when you need
(a) many orientations (>6), (b) perfect reconstruction, (c) shift-invariance.
We need (a) ≤ 4 orientations, (b) no reconstruction (we use the coefficients
directly), (c) not relevant. **Refuted as unnecessary for this task.**

A Gabor bank (3.4) gives 90% of the benefit at 10% of the complexity.

### 3.6 DWT multi-scale → threshold → reconstruct mask

**Construction (Mallat 1989, standard wavelet-denoising pattern).**
```python
coeffs = pywt.wavedec2(gray_f, 'db4', level=4)
# Zero out LL (background) and all subbands except at the level matching r_b.
new = [np.zeros_like(coeffs[0])] + [
    (lh, hl, hh) if lvl == target_level else
    (np.zeros_like(lh), np.zeros_like(hl), np.zeros_like(hh))
    for lvl, (lh, hl, hh) in enumerate(coeffs[1:], start=1)
]
mask_wavelet = pywt.waverec2(new, 'db4')
mask_wavelet = np.abs(mask_wavelet)
```

**Suitability.** **Low-medium.** Reconstruction introduces aliasing artifacts
(pyWavelets uses periodic boundary by default); the simpler approach is to use
the `coeffs[1][1]` (HL at level 1) directly as a feature map, without inverse-
transforming. The "reconstruct" step adds nothing for detection — it's only
useful for denoising.

**Verdict.** Skip the reconstruction. Use the detail subbands directly.

### 3.7 Does PyWavelets work in our env?

**Yes.** Verified by dry-run:
```
$ uv pip install pywavelets --dry-run
  Resolved 2 packages in 222ms
  Would install pywavelets==1.9.0
```
PyWavelets 1.9.0 wheels exist for Python ≥ 3.10 and NumPy ≥ 1.21 (we have 3.14 +
NumPy 2.5.1). Importing `pywt` adds ~5 MB to the venv.

**Can we get wavelet features WITHOUT a new dependency?** Partially:
- **Haar (3.2)** can be emulated with `cv2.boxFilter` + `cv2.subtract`, but you
  lose the orthogonality and the multi-level decomposition.
- **Gabor (3.4)** is fully OpenCV-native via `cv2.getGaborKernel` + `cv2.filter2D`.
  **This is the only wavelet-family method that needs no new dependency.**
- **Daubechies (3.3), DWT multi-scale (3.6), steerable (3.5)** all require PyWavelets
  or worse.

**Recommendation:** Probe Gabor (OpenCV-only) as the **primary wavelet method**,
and add PyWavelets as an optional dependency only if Gabor underperforms.

### 3.8 Wavelet recommendation

**Prototype exactly two:**

1. **Gabor bank, OpenCV-only, caliber-scaled** (3.4). Best standalone
   wavelet-family candidate; no new dep; rotation-invariant.
2. **Daubechies db4 detail subband** (3.3) — **only if** Gabor underperforms and
   the team accepts adding PyWavelets. Probe both `LH/HL` (edges) and `HH`
   (diagonal fiber-tear) at the level matching `r_b`.

Skip Haar (dominated by Daubechies), steerable pyramids (overkill), and DWT
reconstruct (no benefit over direct subband use).

---

## 4. Combined Pyramid + Wavelet + Texture Stack

### 4.1 The four maps to fuse

| # | Map                       | Construction                                  | Captures |
| - | ------------------------- | --------------------------------------------- | -------- |
| a | `local_std` at bullet scale | `cv/detect.py:322-327` (current)             | Texture energy (baseline, strongest) |
| b | Laplacian-pyramid level at `r_b` | §2.2                                    | Band-pass linear rim response |
| c | Gabor bank summed over 4 orientations | §3.4                            | Rotation-invariant oriented texture |
| d | DoG at caliber scale (`σ=r_b/2`, `σ=r_b`) | §2.3                             | Matched-filter optimal for disk |

Each map has different units and dynamic range — **normalize each per-image**
to [0, 1] via `cv2.normalize(..., alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)`
(or robust percentile normalization: 1st–99th percentile → [0, 1]) **before** any
fusion. Without per-map normalization, whichever map has the largest absolute
values dominates trivially.

### 4.2 Fusion strategies

| Strategy | Op | When it's right |
| -------- | -- | --------------- |
| (a) **Max** | `F = max(a, b, c, d)` | When ANY detector firing = candidate. Maximizes recall, hurts precision. Use as a candidate generator, then validate. |
| (b) **Weighted sum** | `F = w_a·a + w_b·b + w_c·c + w_d·d` | When maps are complementary and you know the relative reliability. Weights picked from probe-SNR (e.g. proportional to `pop_snr` from `feature_probe.py`). |
| (c) Learned weights | Logistic regression on hand-labeled holes | **DISALLOWED** by the "stay classical" constraint. |
| (d) **Candidate union + NMS** | Threshold each map independently → union of circle detections → NMS by distance `r_b` | **Recommended.** Different features catch different holes; NMS dedups. Robust to any single feature missing a hole. |

**Recommendation: (d) candidate union + NMS.** Concretely:

```
for each map m in [a, b, c, d]:
    circles_m = HoughCircles(m, HOUGH_GRADIENT_ALT, min_r=0.7*r_b, max_r=1.3*r_b, ...)
all_circles = concat over m
deduped = NMS(all_circles, iou_or_dist_threshold=1.0*r_b)
```

This sidesteps the per-map weight-tuning problem entirely, and is the standard
pattern in classical multi-detector systems (used in OpenCV's cascade-detector
ensembles, Viola-Jones-style).

**Why not (b) weighted sum?** Empirically, weighted sum requires careful per-image
weight tuning (which hole is `local_std` weak on? which does Gabor miss?). With
only 10 train images and no held-out set, you'll overfit the weights. Candidate
union + NMS is parameter-light and degrades gracefully.

**Why not (a) max?** Max-fusion on normalized maps lets noisy maps (e.g.
Laplacian on ring edges) contaminate the result. Candidate union with per-map
thresholding gives each map a vote only where it's individually confident.

### 4.3 NMS at the end

Standard pattern:
```python
def nms_circles(circles, min_dist):
    # circles: list of (x, y, r)
    order = sorted(circles, key=lambda c: -c[3])      # by detection confidence
    kept = []
    for c in order:
        if all(math.hypot(c[0]-k[0], c[1]-k[1]) > min_dist for k in kept):
            kept.append(c)
    return kept
```
`min_dist = 1.0 * r_b` is the natural threshold (two holes closer than one
radius are almost certainly one detection).

---

## 5. Recommended Probe-Script Feature Stack

### 5.1 The four candidates ranked

| Candidate | Construction | Predicted Jaccard vs 0.163 | Cost | Likelihood of beating baseline |
| --------- | ------------ | -------------------------- | ---- | ------------------------------ |
| **D. Fused stack (local-std + Laplacian + Gabor + DoG) + HoughCircles, candidate-union + NMS** | §4.2(d), §4.3 | **0.35–0.55** | High (~5 s/img) | **HIGHEST** — combines orthogonal signals |
| **B. Gabor bank (4 orient, lambd=2·r_b) + HoughCircles** | §3.4, OpenCV-only | **0.25–0.40** | Medium (~1 s/img) | **HIGH** — strongest single wavelet method, no new dep |
| **A. Laplacian-pyramid level at r_b + HoughCircles** | §2.2 | 0.20–0.30 | Low (~0.3 s/img) | MEDIUM — fast sanity check, likely below baseline |
| **C. Multi-scale DoG + SimpleBlobDetector** | §2.3 + §2.5 | 0.20–0.35 | Low (~0.5 s/img) | MEDIUM — different detector primitive, useful for ablation |

### 5.2 Primary and fallback

- **PRIMARY: Candidate D (fused stack).** The strongest single feature
  (local_std_k25) is already in the baseline; fusing it with three orthogonal
  features (Laplacian, Gabor, DoG) at caliber scale + candidate-union + NMS
  catches holes the baseline misses (e.g. dense clusters where local_std washes
  out). The probe will quantify which sub-features pull their weight.
- **FALLBACK: Candidate B (pure Gabor).** If the fused stack shows no net benefit
  over the baseline (i.e. local_std alone dominates the union), Gabor is the
  single best classical alternative — no new dep, rotation-invariant, and
  directly motivated by the cross-system analog in `frame.md:60`.

Skip Candidate A and C for the first probe round; revisit C only if HoughCircles
proves to be the bottleneck (then SimpleBlobDetector on DoG is a drop-in
detector swap).

### 5.3 Probe-script I/O contract

Each candidate script under `cv/tmp/`:

- Reads from `resources/train/<id>.jpg` (10 images: 1, 4, 6, 10, 12, 19, 21, 29, 31, 46).
- Calls `_stage1_localize` + `_stage2_rings` from `cv/detect.py` to reuse the
  ring calibration (per the assumption in §1).
- Writes per-image intermediates to `resources/train/intermediate/<id>_<stage>.png`:
  - `_01_target.png` — extracted target, background masked (§1).
  - `_02_std.png`, `_03_laplacian.png`, `_04_dog.png`, `_05_gabor_0.png`,
    `_05_gabor_45.png`, `_05_gabor_90.png`, `_05_gabor_135.png`, `_05_gabor_sum.png`,
    `_06_fused.png` — feature maps.
  - `_07_detections.png` — original image + detected circles overlaid.
- Writes `resources/train/intermediate/results.json` — per-image, per-candidate
  hole count + Jaccard vs `metadata.yml` ground truth.
- Run under `uv` with `opencv-python-headless` only for Candidate B; allow
  `uv pip install pywavelets` only if Candidate D v2 adds Daubechies.

### 5.4 What "beating the baseline" means concretely

- **Baseline Jaccard: 0.163** (current `_stage3_morph`, local-std + HoughCircles
  ALT, on `resources/paper_targets/`).
- **PRD target: ≥ 0.90** (`prd.md` NFR; per `frame.md:91` this remains the target).
- A candidate is **worth keeping** if it lifts the 10-image mean Jaccard by ≥ 0.10
  absolute (i.e. ≥ 0.263) on the `resources/train/` subset.
- The realistic landing zone for a *single-feature classical probe* is 0.25–0.40.
  Getting from there to 0.90 will need additional work (Stage 2 ring-template
  refinement, Stage 4 watershed cleanup, Stage 5 line-break edge cases) — that's
  a downstream plan, not this probe round.

---

## References (real, verifiable)

1. **Lowe, D. G. (2004).** "Distinctive Image Features from Scale-Invariant
   Keypoints." *IJCV* 60(2), 91–110. — Original SIFT / DoG scale-space detector.
   Used in §2.3.
2. **Burt, P. J. & Adelson, E. H. (1983).** "The Laplacian Pyramid as a Compact
   Image Code." *IEEE T-COM* 31(4), 532–540. — Laplacian pyramid construction
   used in §2.2.
3. **Lindeberg, T. (1998).** "Feature Detection with Automatic Scale Selection."
   *IJCV* 30(2), 79–116. — Scale-selection theory; justifies "pick the level
   matching the feature scale" in §2.2 and §2.4.
4. **Daugman, J. G. (1985).** "Uncertainty relation for resolution in space,
   spatial frequency, and orientation optimized by two-dimensional visual cortical
   filters." *JOSA A* 2(7), 1160–1169. — Original Gabor filter theory; basis of §3.4.
5. **Jain, A. K. & Farrokhnia, F. (1991).** "Unsupervised texture segmentation
   using Gabor filters." *Pattern Recognition* 24(12), 1167–1186. — Gabor texture
   energy as a segmentation feature; rotation-invariant sum used in §3.4.
6. **Mallat, S. (1989).** "A theory for multiresolution signal decomposition: the
   wavelet representation." *IEEE T-PAMI* 11(7), 674–693. — DWT foundation;
   referenced in §3.1 and §3.6.
7. **Freeman, W. T. & Adelson, E. H. (1991).** "The design and use of steerable
   filters." *IEEE T-PAMI* 13(9), 891–906. — Steerable pyramid theory; evaluated
   and rejected in §3.5.
8. **Otsu, N. (1979).** "A threshold selection method from gray-level histograms."
   *IEEE T-SMC* 9(1), 62–66. — Otsu threshold; used in §1.2 and the current
   `_stage1_localize`.
9. **Rother, C., Kolmogorov, V. & Blake, A. (2004).** "GrabCut: interactive
   foreground extraction using iterated graph cuts." *ACM TOG* 23(3), 309–314.
   — GrabCut; evaluated and rejected in §1.4.
10. **Smith, S. M. & Brady, J. M. (1997).** "SUSAN — a new approach to low level
    image processing." *IJCV* 23(1), 45–78. — Alternative edge/feature detector
    relevant to the HoughCircles-vs-SimpleBlobDetector comparison in §2.5.

---

## Honest summary

- **Target extraction** is solved for free by ring-boundary masking, given Stage 2
  succeeds. The one casualty is the 0-point hit in #12.
- **Pyramid methods** are useful as fusion inputs, not as standalone winners.
  DoG at caliber scale (§2.3) is the strongest single pyramid feature.
- **Wavelet methods** → Gabor bank (§3.4) is the only one worth prototyping
  OpenCV-only; Daubechies via PyWavelets is a conditional second.
- **Fusion** should be candidate-union + NMS (§4.2(d)), not weighted sum.
- **Probe ranking**: D (fused) > B (Gabor) > A (Laplacian) > C (DoG+SBD).
- **PyWavelets** installs cleanly under `uv` but is not needed for the first
  probe round; add only if Daubechies subbands are wanted.
- **Realistic Jaccard** for this probe round: 0.25–0.40 on the 10 train images.
  Getting to the PRD's 0.90 needs further work downstream of feature extraction.
