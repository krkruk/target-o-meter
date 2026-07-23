"""Stage 7 — fit the entire warp canvas into 1024×1024 (bullseye at (512, 512);
no content crop).

Ported verbatim from ``cv/approaches/iteredge/normalize.py::normalize_to_1024``
(commit 76f6fc4). Returns the 1024 image plus the ``CoordinateFrame`` that
holds the exact-analytical inverse chain.
"""
from __future__ import annotations

import cv2
import numpy as np

from src.domains.vision.geometry.coordinate_frame import CoordinateFrame


class Normalizer:
    """Resize + pad the warped image to 1024×1024 with bullseye at centre.

    Ported from cv/approaches/iteredge/normalize.py:42-93.
    """

    @staticmethod
    def normalize_to_1024(
        warped: np.ndarray,
        H_full: np.ndarray,
        bullseye_warped: tuple[float, float],
        bbox: tuple[int, int, int, int],
        r_ring1_warped: float,
        cx_crop: float,
        cy_crop: float,
        target_ring1_px: float = 500.0,
        size: int = 1024,
        fill_value: int = 245,
    ) -> tuple[np.ndarray, CoordinateFrame]:
        h, w = warped.shape[:2]
        if r_ring1_warped <= 0:
            r_ring1_warped = float(max(h, w)) / 2.0
        scale = float(target_ring1_px) / r_ring1_warped

        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        resized = cv2.resize(warped, (new_w, new_h), interpolation=cv2.INTER_AREA)

        bcx, bcy = bullseye_warped
        bullseye_resized_x = bcx * scale
        bullseye_resized_y = bcy * scale

        target_cx = size / 2.0
        target_cy = size / 2.0
        tx = target_cx - bullseye_resized_x
        ty = target_cy - bullseye_resized_y

        canvas = np.full((size, size), fill_value, dtype=np.uint8)
        dst_x0 = int(round(tx))
        dst_y0 = int(round(ty))
        src_x0 = max(0, -dst_x0)
        src_y0 = max(0, -dst_y0)
        src_x1 = min(new_w, size - dst_x0)
        src_y1 = min(new_h, size - dst_y0)
        out_x0 = max(0, dst_x0)
        out_y0 = max(0, dst_y0)
        out_x1 = min(size, dst_x0 + new_w)
        out_y1 = min(size, dst_y0 + new_h)
        if out_x1 > out_x0 and out_y1 > out_y0 and src_x1 > src_x0 and src_y1 > src_y0:
            canvas[out_y0:out_y1, out_x0:out_x1] = resized[src_y0:src_y1, src_x0:src_x1]

        H_full_inv = np.linalg.inv(H_full)
        meta = CoordinateFrame(
            bbox=tuple(int(v) for v in bbox),
            H_full=H_full,
            H_full_inv=H_full_inv,
            out_size_warped=(int(w), int(h)),
            bullseye_warped=(float(bcx), float(bcy)),
            scale=float(scale), tx=float(tx), ty=float(ty),
            size=int(size), r_ring1_warped=float(r_ring1_warped),
            cx_crop=float(cx_crop), cy_crop=float(cy_crop),
        )
        return canvas, meta
