"""Multi-ring concentric-ellipses → projective homography normalization approach.

Independently designed by Agent A. Reuses cv.gt.load_bgr (EXIF-safe load),
cv.blob_detect primitives (to_gray, score_holes, calibrate-as-init), and
cv.mock_detector.MockDetector (fixed pattern). Everything else is fresh code.

Math summary:
  * Projected concentric circles → concentric ellipses sharing axes.
  * Pencil of two such conics has degenerate members; the rank-2 member
    factors as two complex-conjugate lines through the common center whose
    intersections with the line at infinity are the image of the circular
    points (I, J).
  * Q^{-1/2} (matrix square root of the mean conic's 2x2 block) maps those
    circular points to their canonical Euclidean positions, which is the
    affine rectification that turns every concentric ellipse into a circle.
  * For coplanar concentric circles the mathematically recoverable part is
    affine; the 3x3 H is therefore constructed with bottom row [0,0,1] by
    default. A small projective refinement is layered on top when the
    detected ellipse centers drift with radius (which they shouldn't for a
    flat target — large drift ⇒ paper curvature or fit error).
"""
