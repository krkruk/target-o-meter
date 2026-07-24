"""Standalone CLI for the vision domain.

``uv run python -m src.domains.vision <IMAGE_PATH>... --detector {google,ollama,mock}``

Accepts one or more **image paths** (relative or absolute). For each image, an
optional ``<stem>_marked.jpg`` sibling (in the same directory) feeds
``AdaptiveFrameSizer``'s GT-aware margin; pass ``--no-gt`` to skip the lookup.

Loads ``.env`` via ``python-dotenv`` BEFORE importing the detectors (so
``GOOGLE_API_KEY`` is present when ``GoogleAIStudioDetector`` constructs). Does
NOT require Django — pure-Python path only. Per image: ``PipelineRunner.run``
→ collect result → optional Jaccard eval table → write ``_summary.json``.
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
        description=(
            "Run the vision pipeline (geometry + LLM detector) on one or more "
            "image paths. For each image, an optional <stem>_marked.jpg "
            "sibling feeds GT-aware warp sizing (use --no-gt to disable)."
        ),
    )
    p.add_argument(
        "images", nargs="+", type=Path,
        help="One or more image paths (relative or absolute) to process.",
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
        help="Disable AdaptiveFrameSizer's GT-aware margin (skip <stem>_marked.jpg lookup).",
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


def _resolve_marked_sibling(image_path: Path) -> Path | None:
    """Look up ``<stem>_marked.jpg`` in the same directory as ``image_path``.

    Returns the path if it exists, else None. The CLI uses this for
    AdaptiveFrameSizer's GT-aware margin (disabled by --no-gt).
    """
    sibling = image_path.parent / f"{image_path.stem}_marked.jpg"
    return sibling if sibling.exists() else None


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # must run before detector construction reads env
    env = _env_status()
    _print_env_status(env)

    args = _build_parser().parse_args(argv)

    detector = DetectorFactory.build(args.detector)
    runner = PipelineRunner(detector)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    metadata = MetadataLoader.load_metadata() if args.eval else {}

    summaries: list[dict] = []
    jaccards: list[float] = []
    succeeded = 0

    for image_arg in args.images:
        image_path = Path(image_arg)
        if not image_path.exists():
            print(f"{image_path}: MISSING (skipping)")
            summaries.append({"image": str(image_arg), "ok": False, "error": "file not found"})
            continue

        marked_path = None if args.no_gt else _resolve_marked_sibling(image_path)

        # Per-image caliber: explicit flag wins; otherwise metadata.yml's primary
        # (matched by stem, e.g. "12" for "12.jpg").
        caliber = args.caliber
        if caliber is None and args.eval:
            entry = metadata.get(image_path.stem, {})
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
            print(f"{image_path.name}: FAILED ({type(exc).__name__}: {exc})")
            summaries.append({
                "image": str(image_arg), "ok": False, "error": str(exc),
            })
            continue

        succeeded += 1

        entry: dict = {"image": str(image_arg), "ok": True, "count": result["count"]}

        if args.eval and metadata:
            meta_entry = metadata.get(image_path.stem, {})
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
                f"{image_path.name}: jaccard={jac:.3f} count_match={count_match} "
                f"n_llm={sum(llm_ms.values())} n_gt={sum(gt_ms.values())}"
            )
        else:
            print(
                f"{image_path.name}: ok count={result['count']} "
                f"total_llm={result['total_llm']}"
            )

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
    # Non-zero exit when every requested image skipped or failed — prevents CI
    # from greenlighting a run that produced no deliverables (the original
    # exit-0-on-all-skipped was a silent-failure mode).
    if args.images and succeeded == 0:
        print(
            f"# error: 0 of {len(args.images)} images produced deliverables",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

