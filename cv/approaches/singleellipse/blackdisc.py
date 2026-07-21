"""Black-disc ellipse fit + perspective decomposition.

Given a grayscale crop centered on the black scoring disc, this module:
  1. Re-fits the disc ellipse (more reliable than the source-level fit because
     the crop removes most confounders).
  2. Decomposes the ellipse into a tilt model:
       tilt_magnitude θ = arccos(min_axis / max_axis)
       tilt_direction φ = major-axis angle from +x (image frame, deg)
     plus an assumed focal length (in pixel units) for the perspective model.
  3. Resolves the front/back ambiguity via the "up is up" prior: a phone
     photographer holds the camera roughly level, so the target's "up" maps to
     image "up" within ±45°. We pick the sign of θ that keeps the warped
     bullseye above (or near) the warped bottom of the disc.

Mathematical foundation
-----------------------
A planar circle of radius r at depth Z, viewed by a perspective camera with
focal length f, tilted by angle θ about an in-plane axis, projects to an
ellipse. Under weak perspective (r ≪ Z):

    ellipse semi-axes:  a = f·r/Z    (along the in-plane tilt axis)
                        b = f·r·cos θ / Z  (perpendicular to it)

so θ = arccos(b/a). The full perspective case admits two solutions for the
tilt sign (front/back); a single ellipse cannot distinguish them without an
external prior. We use "up is up".

References:
  - Forsyth & Ponce, *Computer Vision: A Modern Approach*, sec. 3.2 (pose from
    circles).
  - Safaee-Rad et al. (1992), "New three-dimensional location estimation
    technique for circular features".
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from cv.approaches.singleellipse.localize import find_disc


# Default focal-length estimate for a phone camera, in pixel units.
# A typical phone has f ≈ image_width (60° horizontal FOV). Using max(h, w) of
# the crop gives a reasonable first estimate; the front/back-resolved
# homography is fairly insensitive to f in the range [0.5, 2.0]·max(h,w).
def _estimate_focal_length(shape: tuple[int, int]) -> float:
    h, w = shape
    return float(max(h, w))


def fit_disc(gray: np.ndarray, hint: dict | None = None) -> dict:
    """Find + fit the black disc on the given image.

    If `hint` is provided (a disc dict from a coarser-grained search, e.g. on
    the full source), we restrict the candidate search to contours whose
    centroid is within `hint_radius = 0.15 × hint.semi_a` of the hint center,
    AND whose semi-axes are within ±25% of the hint's. This aggressively
    suppresses both (a) the "disc + dark rings" merged blob that appears when
    the adaptive-threshold block size is recomputed for a smaller crop, and
    (b) the smaller / more circular inner blob that appears when the crop is
    padded (which changes the adaptive threshold's block size and context).

    Returns the disc dict (contour, cx, cy, semi_a, semi_b, ...) — either a
    refit candidate that matches the hint well, or the hint itself.
    """
    if hint is None:
        return find_disc(gray)
    hint_radius = 0.15 * float(hint["semi_a"])
    hcx, hcy = float(hint["cx"]), float(hint["cy"])
    hint_a = float(hint["semi_a"])
    from cv.approaches.singleellipse.localize import _find_disc_candidates, _pick_disc
    cands = _find_disc_candidates(gray)
    nearby = [
        c for c in cands
        if math.hypot(c["cx"] - hcx, c["cy"] - hcy) < hint_radius
        and 0.75 * hint_a < c["semi_a"] < 1.25 * hint_a
    ]
    if not nearby:
        # Fall back to the hint itself, since re-fitting made things worse.
        return hint
    picked = _pick_disc(nearby, gray.shape)
    if picked is None:
        return hint
    return picked


def decompose(disc: dict, img_shape: tuple[int, int]) -> dict:
    """Decompose an ellipse fit into (θ, φ, f, sign).

    Returns a dict with:
        tilt_magnitude_deg, tilt_direction_deg, focal_length_estimate,
        tilt_sign, front_back_resolved_via, semi_a, semi_b, cx, cy, aniso
    """
    semi_a = float(disc["semi_a"])
    semi_b = float(disc["semi_b"])
    cx = float(disc["cx"]); cy = float(disc["cy"])
    # OpenCV fitEllipse angle is in degrees, measured CCW from +x to the
    # "width" axis (the first of the returned (w, h) tuple). Since we derived
    # semi_a = max(w, h)/2 etc. in localize, the major axis direction is:
    angle = float(disc["angle"])  # degrees, in [0, 180)
    # Convert to a clear "major axis direction" in degrees from +x.
    # If semi_a axis is the "height" (h) axis, the major direction is
    # perpendicular to the OpenCV angle.
    # localize.find_disc already returns semi_a as max(ea, eb)/2 — but
    # cv2.fitEllipse's angle conventionally refers to the w-axis. We re-derive
    # here for safety.
    (ec, er), (ea, eb), ang_cv2 = cv2.fitEllipse(disc["contour"])
    # If h > w, the angle goes to the h axis (perpendicular).
    if eb > ea:
        major_dir_deg = (ang_cv2 + 90.0) % 180.0
    else:
        major_dir_deg = ang_cv2 % 180.0

    # Tilt magnitude from aspect ratio (weak perspective).
    ratio = max(semi_a, semi_b) / max(min(semi_a, semi_b), 1e-6)
    theta = math.acos(max(min(semi_b / max(semi_a, 1e-6), 1.0), 1e-6))

    f_est = _estimate_focal_length(img_shape)

    # Resolve front/back: try both signs, pick the one that minimises the
    # difference between the warped bullseye and the original bullseye
    # location ("up is up" prior — the bullseye stays near the image center).
    # The actual selection is done in warp.py where we have the homography.
    return {
        "cx": cx, "cy": cy,
        "semi_a": semi_a, "semi_b": semi_b, "anisotropy": ratio,
        "angle_cv2": float(ang_cv2),
        "major_dir_deg": major_dir_deg,
        "tilt_magnitude_deg": math.degrees(theta),
        "tilt_direction_deg": major_dir_deg,
        "focal_length_estimate": f_est,
        "theta_rad": theta,
        # tilt_sign and front_back_resolved_via are filled in by warp.py
        "tilt_sign": +1,
        "front_back_resolved_via": "default=+1 (warp.refine_sign overrides)",
    }
