"""CLI for the full pipeline (fused geometry + live LLM detector).

Phase 3 Step 2. Runs the fused CV geometry end-to-end and swaps the detector
behind the ``HoleDetector`` seam: ``--detector langchain`` (default; the locked
``gemini-3.5-flash-lite``) or ``--detector mock`` (the Phase-1 plumbing pattern).

Per image, writes EXACTLY 3 files to the output dir (the user's Step-2 spec):
  <id>_llm_input.png, <id>_marked.png, <id>_result.json
Pass ``--debug`` to additionally write the 14-file Phase-2.5 diagnostic
manifest (intake/crop/detect/warp/stage projections/source-predict).

After the run, prints a per-image Jaccard + hole-count table vs
``resources/paper_targets/metadata.yml`` and surfaces any score misalignments
neutrally (could be LLM error or a metadata mis-count — the user re-checks).

Usage:
    uv run python -m cv.approaches.full_pipeline.run 12 46 29 21
    uv run python -m cv.approaches.full_pipeline.run 12 46 29 21 \\
        --detector langchain --model gemini-3.5-flash-lite
    uv run python -m cv.approaches.full_pipeline.run 12 --detector mock
    uv run python -m cv.approaches.full_pipeline.run 12 --caliber 9mm
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
from pathlib import Path

from cv.approaches.full_pipeline.pipeline import run_pipeline
from cv.langchain_detector import LangChainDetector
from cv.mock_detector import MockDetector
from cv.phase3_spike.compare import (
    exact_count_match,
    misalignment_flags,
    per_score_breakdown,
    score_jaccard,
    score_multiset,
)
from cv.phase3_spike.metadata import (
    gt_hits_for,
    load_metadata,
    primary_caliber_for,
)


DEFAULT_IDS = ["12", "46", "29", "21"]
DEFAULT_OUT = "resources/train/intermediate_full_pipeline"
DEFAULT_MODEL = "gemini-3.5-flash-lite"  # locked in Step 1 (mean Jaccard 0.799)


def _build_detector(detector_name: str, model: str) -> object:
    if detector_name == "langchain":
        return LangChainDetector(model=model)
    if detector_name == "mock":
        return MockDetector()
    raise ValueError(f"unknown detector: {detector_name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ids", nargs="*", default=DEFAULT_IDS, help="image IDs")
    parser.add_argument("--target-type", default="air_pistol",
                        choices=["air_pistol", "precision_pistol"])
    parser.add_argument("--detector", default="langchain",
                        choices=["langchain", "mock"],
                        help="hole-detection strategy (default: langchain)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="Google AI Studio model id (locked: gemini-3.5-flash-lite)")
    parser.add_argument(
        "--caliber", default=None,
        help="primary caliber hint for ALL images (overrides metadata.yml). "
             "Default: read per-image from metadata.yml (simulating the UI).",
    )
    parser.add_argument("--out", default=DEFAULT_OUT, help="output directory")
    parser.add_argument("--no-gt", action="store_true",
                        help="disable adaptive frame sizing from magenta GT "
                             "(fall back to conservative ring1_px)")
    parser.add_argument("--debug", action="store_true",
                        help="also write the 14-file Phase-2.5 diagnostic "
                             "manifest alongside the 3 default files")
    args = parser.parse_args()

    detector = _build_detector(args.detector, args.model)
    out_dir = Path(args.out)
    meta = load_metadata()

    print(f"approach=full_pipeline  detector={detector.name}  "
          f"target_type={args.target_type}  out={out_dir}")
    if args.detector == "langchain":
        print(f"  (GOOGLE_API_KEY "
              f"{'set' if __import__('os').environ.get('GOOGLE_API_KEY') else 'MISSING'})")
    print(f"  caliber source: "
          f"{'--caliber ' + args.caliber if args.caliber else 'metadata.yml per-image'}")

    summary = []
    eval_rows = []
    for img_id in args.ids:
        img_path = Path(f"resources/train/{img_id}.jpg")
        if not img_path.exists():
            print(f"{img_id:>5} MISSING {img_path}")
            continue
        gt_path = None if args.no_gt else Path(f"resources/train/{img_id}_marked.jpg")

        # caliber_hint: CLI override wins; else metadata.yml per-image (subtlety #3).
        entry = meta.get(img_id) or {"hits": [], "caliber": None}
        caliber_hint = args.caliber or primary_caliber_for(entry)

        try:
            r = run_pipeline(
                img_path, detector,
                target_type=args.target_type,
                caliber_hint=caliber_hint,
                out_dir=out_dir,
                debug=args.debug,
                gt_marked_path=gt_path,
            )
            ok_flag = "Y" if r.get("ok") else "N"

            # Score comparison vs metadata.yml (only when detection succeeded).
            eval_row = None
            if r.get("ok"):
                gt_hits = gt_hits_for(entry)
                llm_ms = score_multiset(r.get("scores_llm", []))
                gt_ms = score_multiset(gt_hits)
                jac = score_jaccard(llm_ms, gt_ms)
                eval_row = {
                    "id": img_id,
                    "gt_n": len(gt_hits),
                    "llm_n": r.get("count", 0),
                    "jaccard": round(jac, 4),
                    "count_match": exact_count_match(llm_ms, gt_ms),
                    "flags": misalignment_flags(llm_ms, gt_ms),
                    "per_score": per_score_breakdown(llm_ms, gt_ms),
                }
                eval_rows.append(eval_row)

            # Concise per-image line: geometry ok + detection eval.
            nm = r.get("norm_meta", {})
            st = r.get("self_test", {})
            inv_err = st.get("bullseye_invert_err_px", float("nan"))
            r1 = nm.get("target_ring1_px", 0)
            eval_str = ""
            if eval_row is not None:
                eval_str = (f" n={eval_row['llm_n']}/{eval_row['gt_n']}"
                            f" jac={eval_row['jaccard']:.2f}"
                            f" {'Y' if eval_row['count_match'] else 'N'}")
            print(f"{img_id:>5} {ok_flag:>3} r1@1024={r1:>6.0f} "
                  f"invErr={inv_err:>9.2e}{eval_str}")
            summary.append({"id": img_id, "ok": bool(r.get("ok")), "result": r,
                            "eval": eval_row})
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"{img_id:>5} ERR {type(e).__name__}: {e}")
            summary.append({"id": img_id, "ok": False, "err": str(e)})

    ok = sum(1 for s in summary if s["ok"])
    print(f"\n{ok}/{len(summary)} images ok")

    # Eval table + mean Jaccard.
    if eval_rows:
        print(f"\n{'img':>5} {'gt_n':>5} {'llm_n':>6} {'jac':>6} {'count':>6}  flags")
        for e in eval_rows:
            flags = "; ".join(e["flags"]) if e["flags"] else "OK"
            print(f"{e['id']:>5} {e['gt_n']:>5} {e['llm_n']:>6} "
                  f"{e['jaccard']:>6.2f} {'Y' if e['count_match'] else 'N':>6}  {flags}")
        mean_j = statistics.mean(e["jaccard"] for e in eval_rows)
        print(f"\nmean score-Jaccard: {mean_j:.3f}  "
              f"(Step-1 spike ≈ 0.799; classical baseline ~0.255; PRD bar 0.90)")

    # Summary JSON.
    summary_path = out_dir / "_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    lightweight = [
        {
            "id": s["id"],
            "ok": s["ok"],
            "err": s.get("err"),
            "eval": s.get("eval"),
            **{k: v for k, v in (s.get("result", {}) or {}).items()
               if k in ("approach", "detector", "calibration", "refinement",
                        "adaptive_frame", "self_test", "norm_meta", "count")}
        }
        for s in summary
    ]
    summary_path.write_text(json.dumps(lightweight, indent=2))
    print(f"\nwrote per-image {{_llm_input.png, _marked.png, _result.json}} "
          f"+ _summary.json to {out_dir}")


if __name__ == "__main__":
    main()
