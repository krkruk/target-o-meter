"""Standalone CLI for the vision domain.

``uv run python -m src.domains.vision [ids...] --detector {google,ollama,mock}``

Loads ``.env`` via ``python-dotenv`` BEFORE importing the detectors (so
``GOOGLE_API_KEY`` is present when ``GoogleAIStudioDetector`` constructs). Does
NOT require Django — pure-Python path only. Per image: ``PipelineRunner.run`` →
collect result → optional Jaccard eval table → write ``_summary.json``.

Defaults mirror the cv/ CLI (commit 76f6fc4): ids=[12,46,29,21],
detector=google, target_type=air_pistol, out=`resources/train/intermediate_vision`.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.domains.vision.detectors.factory import DetectorFactory
from src.domains.vision.eval.metadata_loader import MetadataLoader
from src.domains.vision.eval.score_comparison import (
    exact_count_match,
    misalignment_flags,
    score_jaccard,
    score_multiset,
)
from src.domains.vision.pipeline.pipeline_runner import PipelineRunner


DEFAULT_IDS = ["12", "46", "29", "21"]
DEFAULT_OUT = "resources/train/intermediate_vision"


def _env_status() -> dict[str, bool]:
    """Presence-only env-var snapshot (never leak values)."""
    import os
    return {
        "GOOGLE_API_KEY": bool(os.environ.get("GOOGLE_API_KEY")),
        "OLLAMA_HOST": bool(os.environ.get("OLLAMA_HOST")),
        "OLLAMA_MODEL": bool(os.environ.get("OLLAMA_MODEL")),
    }


def _print_env_status(env: dict[str, bool]) -> None:
    print("# env presence (values not shown):")
    for k, present in env.items():
        print(f"  {k}: {'SET' if present else 'unset'}")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.domains.vision",
        description="Run the vision pipeline (geometry + LLM detector) on train images.",
    )
    p.add_argument(
        "ids", nargs="*", default=DEFAULT_IDS,
        help=f"Image ids (e.g. 12 46 29 21). Default: {DEFAULT_IDS}",
    )
    p.add_argument(
        "--detector", choices=("google", "ollama", "mock"), default="google",
        help="Detector strategy. Default: google.",
    )
    p.add_argument(
        "--target-type", choices=("air_pistol", "precision_pistol"), default="air_pistol",
        help="ISSF target type. Default: air_pistol.",
    )
    p.add_argument("--caliber", default=None, help="Primary caliber hint (e.g. 9mm).")
    p.add_argument("--out", default=DEFAULT_OUT, help=f"Output dir. Default: {DEFAULT_OUT}")
    p.add_argument(
        "--no-gt", action="store_true",
        help="Disable AdaptiveFrameSizer's GT-aware margin (skip _marked.jpg lookup).",
    )
    p.add_argument(
        "--debug", action="store_true",
        help="Also write the 14-file Phase-2.5 diagnostic manifest.",
    )
    p.add_argument(
        "--eval", action="store_true",
        help="Compute score-Jaccard vs metadata.yml and print a per-image table.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # must run before detector construction reads env
    env = _env_status()
    _print_env_status(env)

    args = _build_parser().parse_args(argv)

    detector = DetectorFactory.build(args.detector)
    runner = PipelineRunner(detector)

    train_dir = Path("resources/train")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    metadata = MetadataLoader.load_metadata() if args.eval else {}

    summaries: list[dict] = []
    jaccards: list[float] = []

    for img_id in args.ids:
        image_path = train_dir / f"{img_id}.jpg"
        if not image_path.exists():
            print(f"img {img_id}: MISSING ({image_path})")
            continue

        marked_path = None if args.no_gt else train_dir / f"{img_id}_marked.jpg"
        if marked_path is not None and not marked_path.exists():
            marked_path = None

        # Per-image caliber: explicit flag wins; otherwise metadata.yml's primary.
        caliber = args.caliber
        if caliber is None and args.eval:
            entry = metadata.get(str(img_id), {})
            caliber = MetadataLoader.primary_caliber_for(entry)

        try:
            result = runner.run(
                image_path,
                target_type=args.target_type,
                caliber_hint=caliber,
                out_dir=out_dir,
                debug=args.debug,
                gt_marked_path=marked_path,
            )
        except Exception as exc:
            print(f"img {img_id}: FAILED ({type(exc).__name__}: {exc})")
            summaries.append({"image": image_path.name, "ok": False, "error": str(exc)})
            continue

        entry: dict = {"image": image_path.name, "ok": True, "count": result["count"]}

        if args.eval and metadata:
            meta_entry = metadata.get(str(img_id), {})
            gt_hits = MetadataLoader.gt_hits_for(meta_entry)
            llm_scores = result["scores_llm"]
            llm_ms = score_multiset(llm_scores)
            gt_ms = score_multiset(gt_hits)
            jac = score_jaccard(llm_ms, gt_ms)
            count_match = exact_count_match(llm_ms, gt_ms)
            flags = misalignment_flags(llm_ms, gt_ms)
            entry.update({
                "jaccard": jac,
                "count_match": count_match,
                "n_llm": sum(llm_ms.values()),
                "n_gt": sum(gt_ms.values()),
                "flags": flags,
            })
            jaccards.append(jac)
            print(
                f"img {img_id}: jaccard={jac:.3f} count_match={count_match} "
                f"n_llm={sum(llm_ms.values())} n_gt={sum(gt_ms.values())}"
            )
        else:
            print(f"img {img_id}: ok count={result['count']} total_llm={result['total_llm']}")

        summaries.append(entry)

    if args.eval and jaccards:
        mean_jac = statistics.mean(jaccards)
        print(f"\n# mean Jaccard: {mean_jac:.3f} (n={len(jaccards)})")

    summary_path = out_dir / "_summary.json"
    summary_path.write_text(json.dumps({
        "detector": args.detector,
        "target_type": args.target_type,
        "caliber_hint": args.caliber,
        "env_presence": env,
        "images": summaries,
        "mean_jaccard": statistics.mean(jaccards) if jaccards else None,
    }, indent=2))
    print(f"\n# wrote {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
