"""Inverse-perspective homography from a single ellipse.

Given (cx, cy, semi_a, semi_b, tilt_direction, focal_length), construct a 3×3
homography H that "un-tilts" the plane of the disc so the disc appears as a
circle in the warped image. Apply via cv2.warpPerspective.

Construction
------------
Under perspective projection, a plane tilted by angle θ about an in-plane axis
projects to a different image than the same plane fronto-parallel. The two
views are related by a homography:

    H = K · R⁻¹ · K⁻¹

where K is the camera intrinsics matrix and R is the 3D rotation that takes
the fronto-parallel plane to the tilted one. Here we use:

    K = diag(f, f, 1)  (focal length f, principal point at the disc center)
    R = Rot(axis, θ)   (axis in the image plane, perpendicular to tilt direction)

The "axis" of rotation is the MAJOR axis direction (cos φ, sin φ, 0): the
plane rotates about this in-image-plane axis. Disc center stays fixed because
the rotation axis passes through it (after the K-centered reparameterization).

Front/back ambiguity: a circle viewed at tilt +θ produces the same ellipse as
tilt -θ. We resolve this with the "up is up" prior: the rotation that puts
the disc's "up" closest to image "up" wins.

Iterative refinement
--------------------
The weak-perspective decomposition (θ = arccos(b/a)) under-corrects for large
tilts because it ignores the projective asymmetry (the "near" side of the disc
is more compressed than the "far" side in the source image). After the first
warp the disc is *more* circular but not perfectly so. We iterate: re-fit the
disc on the warped image, decompose the residual tilt, apply a correction
homography. 2–3 iterations converge for our test set.
"""
from __future__ import annotations

import math

import cv2
import numpy as np


def _rotation_matrix(axis: np.ndarray, angle: float) -> np.ndarray:
    """Rodrigues rotation matrix for axis (3-vector) and angle (radians)."""
    axis = np.asarray(axis, dtype=np.float64)
    n = np.linalg.norm(axis)
    if n < 1e-12:
        return np.eye(3, dtype=np.float64)
    axis = axis / n
    R, _ = cv2.Rodrigues(angle * axis)
    return R


def build_homography(
    cx: float, cy: float,
    semi_a: float, semi_b: float,
    tilt_direction_deg: float,
    focal_length: float,
    tilt_sign: int = +1,
) -> np.ndarray:
    """Build the inverse-perspective homography.

    Args:
        cx, cy: disc center in image px.
        semi_a, semi_b: ellipse semi-axes (a = major).
        tilt_direction_deg: major-axis direction (deg from +x).
        focal_length: in image px.
        tilt_sign: +1 or -1 — which side of the plane is closer to the camera.

    Returns:
        3×3 homography H such that p_warped = H · p_source (homogeneous).
        The disc center (cx, cy) is a fixed point of H.
    """
    f = float(focal_length)
    # Tilt magnitude from aspect ratio.
    a = float(max(semi_a, semi_b))
    b = float(min(semi_a, semi_b))
    theta = math.acos(max(min(b / max(a, 1e-6), 1.0), 1e-6))

    # Tilt direction in radians.
    phi = math.radians(tilt_direction_deg)
    # Rotation axis = major axis direction (in image plane, z=0).
    axis = np.array([math.cos(phi), math.sin(phi), 0.0], dtype=np.float64)

    # Rotation R takes fronto-parallel → tilted. Its inverse (R^T) un-tilts.
    R = _rotation_matrix(axis, tilt_sign * theta)
    R_inv = R.T

    # K centered at disc center.
    K = np.array([[f, 0, cx],
                  [0, f, cy],
                  [0, 0, 1.0]], dtype=np.float64)
    K_inv = np.linalg.inv(K)

    H = K @ R_inv @ K_inv
    return H


def apply_warp(
    gray: np.ndarray,
    H: np.ndarray,
    cx: float, cy: float,
    out_radius_factor: float = 5.0,
    semi_a: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """Apply homography H to gray, with the *warped* disc center placed at the
    centre of a square output.

    The disc center is NOT a fixed point of H (rotation about an axis in the
    image plane moves points off the axis). We therefore compute the warped
    position H·(cx,cy,1) explicitly and translate so that it lands at the
    output center.

    Returns (warped, H_total, (out_w, out_h)) where H_total is the FULL
    homography (image → warped, including the re-centering translation).

    out_radius_factor: half-size of the output = out_radius_factor × semi_a.
    """
    h, w = gray.shape
    a = float(semi_a) if semi_a > 0 else float(max(h, w)) / 6.0
    half = int(out_radius_factor * a)
    out_size = 2 * half
    out_cx = out_cy = half  # square output

    # Compute where the disc center lands after the un-tilt homography.
    p_warped = H @ np.array([cx, cy, 1.0], dtype=np.float64)
    wcx = float(p_warped[0] / p_warped[2])
    wcy = float(p_warped[1] / p_warped[2])

    # Translate the warped disc center to the output center.
    T = np.array([[1, 0, out_cx - wcx],
                  [0, 1, out_cy - wcy],
                  [0, 0, 1]], dtype=np.float64)
    H_total = T @ H

    warped = cv2.warpPerspective(
        gray, H_total, (out_size, out_size),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=245,
    )
    return warped, H_total, (out_size, out_size)


def _score_up_is_up(
    H: np.ndarray, cx: float, cy: float, semi_a: float,
) -> float:
    """Score how well H satisfies the "up is up" prior.

    We probe four cardinal directions on the source disc:
        up:    (cx, cy - semi_a)        — should map to a point with smaller y
        down:  (cx, cy + semi_a)        — should map to a point with larger y
        left:  (cx - semi_a, cy)        — should map to a point with smaller x
        right: (cx + semi_a, cy)        — should map to a point with larger x
    The score is the sum of |Δy| (up positive, down negative) — a correctly
    oriented warp keeps up above center and down below.
    """
    probes = [
        (cx, cy - semi_a, "y", -1.0),  # up: mapped y should be < cy
        (cx, cy + semi_a, "y", +1.0),  # down: mapped y should be > cy
        (cx - semi_a, cy, "x", -1.0),  # left: mapped x should be < cx
        (cx + semi_a, cy, "x", +1.0),  # right: mapped x should be > cx
    ]
    score = 0.0
    for px, py, axis, sign in probes:
        p = H @ np.array([px, py, 1.0])
        p = p[:2] / p[2]
        if axis == "y":
            # We want sign * (p_y - cy) > 0; reward positive, penalise negative.
            score += sign * (p[1] - cy)
        else:
            score += sign * (p[0] - cx)
    return score


def resolve_front_back(
    cx: float, cy: float,
    semi_a: float, semi_b: float,
    tilt_direction_deg: float,
    focal_length: float,
) -> tuple[int, float, str]:
    """Resolve the front/back ambiguity via "up is up".

    Returns (tilt_sign, score, reason). tilt_sign ∈ {+1, -1} is the sign that
    best satisfies the prior.
    """
    if semi_a <= semi_b * 1.001:
        # Effectively circular — no tilt to resolve.
        return +1, 0.0, "no tilt (circle)"

    candidates = []
    for sign in (+1, -1):
        H = build_homography(cx, cy, semi_a, semi_b,
                             tilt_direction_deg, focal_length, tilt_sign=sign)
        s = _score_up_is_up(H, cx, cy, semi_a)
        candidates.append((sign, s))
    # Pick the sign with the higher "up is up" score.
    candidates.sort(key=lambda t: -t[1])
    best_sign, best_score = candidates[0]
    margin = best_score - candidates[1][1]
    reason = (
        f'"up is up" prior (sign={best_sign:+d}, score={best_score:.1f}, '
        f'margin={margin:.1f})'
    )
    return best_sign, best_score, reason


def warp_with_refinement(
    gray: np.ndarray,
    disc: dict,
    decomposition: dict,
    sign: int,
    n_iters: int = 3,
    out_radius_factor: float = 5.0,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Apply the inverse-perspective warp, then iteratively refine.

    Each iteration:
      1. Re-detect the disc on the current warped image (using a hint that
         follows the disc through the chain).
      2. Decompose the residual tilt.
      3. Build a correction homography and apply it.

    The composition of all homographies (initial + corrections) is returned as
    H_total. The warped image and the disc center / radius in the final warped
    frame are also returned.
    """
    # Initial warp.
    cx, cy = float(decomposition["cx"]), float(decomposition["cy"])
    a = float(decomposition["semi_a"])
    f = float(decomposition["focal_length_estimate"])
    phi = float(decomposition["tilt_direction_deg"])
    H = build_homography(cx, cy, a, float(decomposition["semi_b"]),
                         phi, f, tilt_sign=sign)
    warped, H_total, out_shape = apply_warp(
        gray, H, cx, cy, out_radius_factor=out_radius_factor, semi_a=a,
    )
    out_size = out_shape[0]
    out_cx = out_cy = out_size / 2.0

    # Track the disc center / radius as we iterate (in the *cumulative* warped
    # frame, which always has the disc at (out_cx, out_cy)).
    current_center = (cx, cy)
    current_a = a

    from cv.approaches.singleellipse.blackdisc import fit_disc
    from cv.approaches.singleellipse.localize import _find_disc_candidates, _pick_disc

    for it in range(n_iters):
        # Hint for re-detection: disc at (out_cx, out_cy) with radius = average
        # of 4-axis endpoint distances in the current warped frame.
        endpoints = _axis_endpoints(current_center, current_a,
                                    current_a / max(decomposition["anisotropy"], 1.001),
                                    phi)
        pc = H_total @ np.array([current_center[0], current_center[1], 1.0])
        pc = pc / pc[2]
        dists = []
        for px, py in endpoints:
            p = H_total @ np.array([px, py, 1.0])
            p = p / p[2]
            dists.append(math.hypot(p[0] - pc[0], p[1] - pc[1]))
        hint_r = float(np.mean(dists))
        if hint_r < 5:
            break
        hint = {
            "cx": out_cx, "cy": out_cy,
            "semi_a": hint_r, "semi_b": hint_r, "anisotropy": 1.0,
            "circularity": 0.5, "area": math.pi * hint_r * hint_r,
            "angle": 0.0, "score": 0.0, "close_ks": 0, "blurred": False,
        }
        # Find the disc in the current warped image, near (out_cx, out_cy).
        cands = _find_disc_candidates(warped)
        nearby = [c for c in cands
                  if math.hypot(c["cx"] - out_cx, c["cy"] - out_cy) < 0.5 * hint_r
                  and c["semi_a"] < 2.0 * hint_r]
        if not nearby:
            break
        picked = _pick_disc(nearby, warped.shape)
        if picked is None:
            break
        new_aniso = picked["anisotropy"]
        # If the disc is now nearly circular, stop early.
        if new_aniso < 1.02:
            break
        # Decompose residual tilt and build correction homography.
        from cv.approaches.singleellipse.blackdisc import decompose
        decomp2 = decompose(picked, warped.shape)
        sign2, _, _ = resolve_front_back(
            decomp2["cx"], decomp2["cy"],
            decomp2["semi_a"], decomp2["semi_b"],
            decomp2["tilt_direction_deg"],
            decomp2["focal_length_estimate"],
        )
        H2 = build_homography(
            decomp2["cx"], decomp2["cy"],
            decomp2["semi_a"], decomp2["semi_b"],
            decomp2["tilt_direction_deg"],
            decomp2["focal_length_estimate"],
            tilt_sign=sign2,
        )
        # Apply H2 within the current warped frame: H_new = T2 · H2 (re-center).
        p_w = H2 @ np.array([decomp2["cx"], decomp2["cy"], 1.0])
        wcx = float(p_w[0] / p_w[2]); wcy = float(p_w[1] / p_w[2])
        T2 = np.array([[1, 0, out_cx - wcx],
                       [0, 1, out_cy - wcy],
                       [0, 0, 1]], dtype=np.float64)
        H_total_new = T2 @ H2
        # Compose with the existing H_total.
        H_total = H_total_new @ H_total
        # Re-warp from the source crop using the updated H_total.
        warped = cv2.warpPerspective(
            gray, H_total, (out_size, out_size),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=245,
        )
        current_center = (out_cx, out_cy)
        current_a = picked["semi_a"]
        phi = decomp2["tilt_direction_deg"]

    # Final disc center / radius in the warped frame.
    final_center = (float(out_cx), float(out_cy))
    # Average of 4-axis endpoint distances for the final disc.
    return warped, H_total, {
        "final_center_warped": final_center,
        "final_disc_radius_warped": _mean_axis_distance(H_total, current_center,
                                                        current_a,
                                                        current_a / max(decomposition["anisotropy"], 1.001),
                                                        phi),
        "n_iters_run": it + 1 if 'it' in dir() else 0,
    }


def _axis_endpoints(center, a, b, phi_deg):
    """Return the 4 (cx ± a cos φ, ...) cardinal endpoints of an ellipse."""
    cx, cy = center
    phi = math.radians(phi_deg)
    return [
        (cx + a * math.cos(phi), cy + a * math.sin(phi)),
        (cx - a * math.cos(phi), cy - a * math.sin(phi)),
        (cx + b * math.sin(phi), cy - b * math.cos(phi)),
        (cx - b * math.sin(phi), cy + b * math.cos(phi)),
    ]


def _mean_axis_distance(H, center, a, b, phi_deg):
    endpoints = _axis_endpoints(center, a, b, phi_deg)
    pc = H @ np.array([center[0], center[1], 1.0])
    pc = pc / pc[2]
    dists = []
    for px, py in endpoints:
        p = H @ np.array([px, py, 1.0])
        p = p / p[2]
        dists.append(math.hypot(p[0] - pc[0], p[1] - pc[1]))
    return float(np.mean(dists))
