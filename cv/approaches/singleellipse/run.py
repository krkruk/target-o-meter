"""CLI driver for the single-ellipse pipeline.

Run on the 4 test images:
    uv run python -m cv.approaches.singleellipse.run 12 46 29 21

Or with a custom output dir:
    uv run python -m cv.approaches.singleellipse.run 12 46 29 21 --out /tmp/run

Each image produces 9 files under the output directory (8 PNGs + 1 JSON).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from cv.approaches.singleellipse.pipeline import run_pipeline
from cv.mock_detector import MockDetector


DEFAULT_IDS = ["12", "46", "29", "21"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ids", nargs="*", default=DEFAULT_IDS,
                        help="image IDs (default: 12 46 29 21)")
    parser.add_argument("--target-type", default="air_pistol",
                        choices=["air_pistol", "precision_pistol"])
    parser.add_argument("--caliber", default=None)
    parser.add_argument("--out", default="resources/train/intermediate_singleellipse",
                        help="output directory")
    parser.add_argument("--margin", type=float, default=5.5,
                        help="crop half-size = margin × disc semi_a (default 5.5)")
    parser.add_argument("--warp-radius", type=float, default=5.0,
                        help="warped half-size = warp_radius × disc semi_a")
    parser.add_argument("--no-intermediates", action="store_true")
    args = parser.parse_args()

    detector = MockDetector()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"approach=singleellipse  detector={detector.name}  out={out_dir}")
    print(f"{'img':>5} {'ok':>3} {'semi_a':>6} {'semi_b':>6} {'aniso':>5} "
          f"{'theta':>5} {'phi':>4} {'sign':>4} {'invErr':>9} {'n':>3} {'llm':>4} {'cla':>4}")

    summary = []
    for img_id in args.ids:
        img_path = Path(f"resources/train/{img_id}.jpg")
        if not img_path.exists():
            print(f"{img_id:>5} MISSING {img_path}")
            continue
        try:
            r = run_pipeline(
                img_path, detector,
                target_type=args.target_type,
                caliber_hint=args.caliber,
                out_dir=out_dir,
                write_intermediates=not args.no_intermediates,
                margin_factor=args.margin,
                out_radius_factor=args.warp_radius,
            )
            if r.get("ok"):
                dc = r["disc_crop"]; de = r["decomposition"]; st = r["self_test"]
                print(f"{img_id:>5} {'Y':>3} {dc['semi_a']:>6.1f} {dc['semi_b']:>6.1f} "
                      f"{dc['anisotropy']:>5.2f} {de['tilt_magnitude_deg']:>5.1f} "
                      f"{de['tilt_direction_deg']:>4.0f} {de['tilt_sign']:>+4d} "
                      f"{st['bullseye_invert_err_px']:>9.4f} "
                      f"{r['count']:>3} {r['total_llm']:>4} {r['total_classical']:>4}")
                summary.append((img_id, True))
            else:
                print(f"{img_id:>5} N   failed at {r.get('failure_stage')}")
                summary.append((img_id, False))
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"{img_id:>5} ERR {type(e).__name__}: {e}")
            summary.append((img_id, False))

    ok = sum(1 for _, s in summary if s)
    print(f"\n{ok}/{len(summary)} images ok")


if __name__ == "__main__":
    main()
