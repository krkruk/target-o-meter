"""CLI for the iteredge pipeline.

Run on the 4 target images:

    uv run python -m cv.approaches.iteredge.run 12 46 29 21

Outputs 9 files per image under resources/train/intermediate_iteredge/:
  <id>_01_intake.png       EXIF-oriented source
  <id>_02_crop.png         after localization
  <id>_02b_detect.png      KEY DIAGNOSTIC: edges (red) + initial rings (yellow dashed) + final rings (green solid)
  <id>_03_warp.png         after warp with optimized H
  <id>_04_llm_input.png    1024x1024 normalized
  <id>_05_llm_predict.png  1024 + magenta dots
  <id>_06_crop_predict.png crop + inverted magenta + rings
  <id>_07_source_predict.png source + fully-inverted magenta
  <id>_result.json         structured output
"""
from __future__ import annotations

import argparse
from pathlib import Path

from cv.approaches.iteredge.pipeline import run_pipeline
from cv.mock_detector import MockDetector


DEFAULT_IDS = ["12", "46", "29", "21"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ids", nargs="*", default=DEFAULT_IDS, help="image IDs")
    parser.add_argument("--target-type", default="air_pistol", choices=["air_pistol", "precision_pistol"])
    parser.add_argument("--caliber", default=None)
    parser.add_argument("--out", default="resources/train/intermediate_iteredge", help="output directory")
    args = parser.parse_args()

    detector = MockDetector()
    out_dir = Path(args.out)

    print(f"approach=iteredge  detector={detector.name}  target_type={args.target_type}  out={out_dir}")
    print(f"{'img':>5} {'ok':>3} {'s':>6} {'r_bw':>6} {'r_bl':>6} {'aniso':>5} "
          f"{'conv':>4} {'nit':>4} {'cost':>9} {'invErr':>9}")

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
            )
            cal = r.get("calibration", {})
            opt = r.get("optimization", {})
            st = r.get("self_test", {})
            ok_flag = "Y" if r.get("ok") else "N"
            conv = "Y" if opt.get("converged") else "N"
            print(
                f"{img_id:>5} {ok_flag:>3} "
                f"{cal.get('s_px', 0):>6.1f} "
                f"{cal.get('r_bw_px', 0):>6.1f} "
                f"{cal.get('r_bull_px', 0):>6.1f} "
                f"{cal.get('anisotropy', 0):>5.2f} "
                f"{conv:>4} {opt.get('n_iterations', 0):>4} "
                f"{opt.get('final_cost', 0):>9.2e} "
                f"{st.get('bullseye_invert_err_px', 0):>9.2e}"
            )
            summary.append((img_id, bool(r.get("ok"))))
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"{img_id:>5} ERR {type(e).__name__}: {e}")
            summary.append((img_id, False))

    ok = sum(1 for _, s in summary if s)
    print(f"\n{ok}/{len(summary)} images ok")


if __name__ == "__main__":
    main()
