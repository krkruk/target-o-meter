"""CLI for the fused pipeline.

Runs the fused multiring-detection + iteredge-refinement pipeline. Default
test set is the same 4 images used in Phase 2 (12 gold, 46 gold, 29 logo
disaster, 21 cropped-holes).

Per-image outputs land under resources/train/intermediate_fused/ — see the
package docstring in cv.approaches.fused.__init__ for the full file manifest.

Usage:
    uv run python -m cv.approaches.fused.run 12 46 29 21
    uv run python -m cv.approaches.fused.run 12 46 29 21 --out /tmp/fused_test
    uv run python -m cv.approaches.fused.run 1 4 6 10 12 19 21 29 31 46  # all 10

The pipeline uses MockDetector for now (Phase 3 swaps in LangChain detectors
behind the same HoleDetector seam).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from cv.approaches.fused.pipeline import run_pipeline
from cv.mock_detector import MockDetector


DEFAULT_IDS = ["12", "46", "29", "21"]
DEFAULT_OUT = "resources/train/intermediate_fused"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ids", nargs="*", default=DEFAULT_IDS, help="image IDs")
    parser.add_argument("--target-type", default="air_pistol",
                        choices=["air_pistol", "precision_pistol"])
    parser.add_argument("--caliber", default=None)
    parser.add_argument("--out", default=DEFAULT_OUT, help="output directory")
    parser.add_argument("--no-gt", action="store_true",
                        help="disable adaptive frame sizing from magenta GT "
                             "(fall back to conservative ring1_px=470)")
    args = parser.parse_args()

    detector = MockDetector()
    out_dir = Path(args.out)

    print(f"approach=fused  detector={detector.name}  target_type={args.target_type}  out={out_dir}")
    print(f"{'img':>5} {'ok':>3} {'s':>6} {'r_bw':>6} {'r_bl':>6} "
          f"{'nit':>4} {'cost':>9} {'revert':>6} "
          f"{'ring1px':>8} {'invErr':>9}")

    summary = []
    for img_id in args.ids:
        img_path = Path(f"resources/train/{img_id}.jpg")
        if not img_path.exists():
            print(f"{img_id:>5} MISSING {img_path}")
            continue
        gt_path = None if args.no_gt else Path(f"resources/train/{img_id}_marked.jpg")
        try:
            r = run_pipeline(
                img_path, detector,
                target_type=args.target_type,
                caliber_hint=args.caliber,
                out_dir=out_dir,
                gt_marked_path=gt_path,
            )
            cal = r.get("calibration", {})
            opt = r.get("refinement", {})
            af = r.get("adaptive_frame", {})
            st = r.get("self_test", {})
            ok_flag = "Y" if r.get("ok") else "N"
            revert = "Y" if opt.get("reverted_to_init") else "N"
            print(
                f"{img_id:>5} {ok_flag:>3} "
                f"{cal.get('s_px', 0):>6.1f} "
                f"{cal.get('r_bw_px', 0):>6.1f} "
                f"{cal.get('r_bull_px', 0):>6.1f} "
                f"{opt.get('n_iterations', 0):>4} "
                f"{opt.get('final_cost', 0) or float('nan'):>9.2e} "
                f"{revert:>6} "
                f"{af.get('chosen', 0):>8.1f} "
                f"{st.get('bullseye_invert_err_px', 0):>9.2e}"
            )
            summary.append({"id": img_id, "ok": bool(r.get("ok")), "result": r})
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"{img_id:>5} ERR {type(e).__name__}: {e}")
            summary.append({"id": img_id, "ok": False, "err": str(e)})

    ok = sum(1 for s in summary if s["ok"])
    print(f"\n{ok}/{len(summary)} images ok")

    # Write a small summary JSON.
    summary_path = out_dir / "_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    lightweight = [
        {
            "id": s["id"],
            "ok": s["ok"],
            "err": s.get("err"),
            **{k: v for k, v in (s.get("result", {}) or {}).items()
               if k in ("approach", "calibration", "refinement",
                        "adaptive_frame", "self_test", "count")},
        }
        for s in summary
    ]
    summary_path.write_text(json.dumps(lightweight, indent=2))


if __name__ == "__main__":
    main()
