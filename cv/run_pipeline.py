"""CLI driver for the LLM-pivot pipeline.

Run on all 10 train images with the mock detector:

    uv run python -m cv.run_pipeline

Run on a subset with a caliber hint:

    uv run python -m cv.run_pipeline 46 6 --caliber 9x19

The --detector flag selects the strategy. Currently only "mock"; Phase 3 adds
"aistudio" (Gemma 4 via Google AI Studio) and "ollama" (Gemma 4 via local Ollama).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from cv.mock_detector import MockDetector
from cv.pipeline import run_pipeline


DETECTORS = {
    "mock": MockDetector,
}

DEFAULT_IDS = ["1", "4", "6", "10", "12", "19", "21", "29", "31", "46"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ids", nargs="*", default=DEFAULT_IDS, help="image IDs (default: 10 train)")
    parser.add_argument("--detector", choices=list(DETECTORS), default="mock",
                        help="hole-detection strategy (Phase 3 adds 'aistudio' and 'ollama')")
    parser.add_argument("--target-type", default="air_pistol", choices=["air_pistol", "precision_pistol"])
    parser.add_argument("--caliber", default=None, help="caliber hint e.g. 9x19 / 22lr / .223Rem / slug")
    parser.add_argument("--out", default="resources/train/intermediate_llm", help="output directory")
    parser.add_argument("--no-intermediates", action="store_true", help="skip writing PNGs + JSON")
    args = parser.parse_args()

    detector = DETECTORS[args.detector]()
    out_dir = Path(args.out)

    print(f"detector={detector.name}  target_type={args.target_type}  "
          f"caliber_hint={args.caliber}  out={out_dir}")
    print(f"{'img':>5} {'ok':>3} {'s_px':>6} {'r_bw':>6} {'r_bul':>6} {'aniso':>5} "
          f"{'invErr':>7} {'n':>3} {'llm':>4} {'cla':>4}")

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
            )
            if r.get("ok"):
                cal = r["calibration"]
                st = r["self_test"]
                print(f"{img_id:>5} {'Y':>3} {cal['s_px']:>6.1f} {cal['r_bw_px']:>6.1f} "
                      f"{cal['r_bull_px']:>6.1f} {cal['anisotropy']:>5.2f} "
                      f"{st['bullseye_invert_err_px']:>7.3f} "
                      f"{r['count']:>3} {r['total_llm']:>4} {r['total_classical']:>4}")
                summary.append((img_id, True))
            else:
                print(f"{img_id:>5} N   calibrate failed: {r.get('failure_stage')}")
                summary.append((img_id, False))
        except Exception as e:
            print(f"{img_id:>5} ERR {type(e).__name__}: {e}")
            summary.append((img_id, False))

    ok = sum(1 for _, s in summary if s)
    print(f"\n{ok}/{len(summary)} images ok; inversion self-test in result.json under self_test.passed")


if __name__ == "__main__":
    main()
