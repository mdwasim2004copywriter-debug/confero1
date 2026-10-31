"""Runs a small, hardcoded-safe comparison matrix (model x thread count)
against one clip, using the same bench_core.run_one() as run_bench.py,
and prints a comparison table with speedup relative to a chosen baseline.

Deliberately does NOT sweep every model/thread combination -- only
{tiny, small, medium} x {4, min(8, logical_cores)} threads, and skips any
config bench_core's RAM safety check refuses, rather than overriding it.
medium is never run back-to-back with anything else in parallel; this
script runs everything sequentially, one process-local model at a time,
and deletes each model (see bench_core.run_one) before the next config
loads, which is also why each config pays its own model-load cost -- that
cost is reported separately from transcribe time precisely so it doesn't
get mistaken for the thing being compared.

Example:
    python backend/bench/run_suite.py --clip output/last_recording.wav --runs 2
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from bench import bench_core
    from bench.bench_core import BenchSafetyError
else:
    from . import bench_core
    from .bench_core import BenchSafetyError

DEFAULT_CLIP = Path(__file__).resolve().parent.parent / "output" / "last_recording.wav"
MODELS = ["tiny", "small", "medium"]
BASELINE = ("small", 8)  # matches api.py's production default model; thread count clamped below


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clip", type=Path, default=DEFAULT_CLIP)
    parser.add_argument("--runs", type=int, default=2, help="timed repetitions per config (default: 2)")
    parser.add_argument("--warmup", type=int, default=1, help="untimed warmup runs per config (default: 1)")
    parser.add_argument("--skip-medium", action="store_true", help="skip the medium model even if RAM allows it")
    parser.add_argument("--force", action="store_true", help="override the RAM safety guard for every config")
    args = parser.parse_args()

    if not args.clip.exists():
        print(f"ERROR: clip not found: {args.clip}", file=sys.stderr)
        return 2

    logical = os.cpu_count() or 4
    thread_candidates = sorted({t for t in (4, logical) if 1 <= t <= logical})
    models = [m for m in MODELS if not (args.skip_medium and m == "medium")]

    baseline_model, baseline_threads = BASELINE
    baseline_threads = min(baseline_threads, logical)
    rows = []

    for model in models:
        for threads in thread_candidates:
            label = f"{model} @ {threads} threads"
            try:
                result = bench_core.run_one(
                    clip_path=args.clip,
                    model=model,
                    device="cpu",
                    compute_type="int8",
                    threads=threads,
                    warmup=args.warmup,
                    runs=args.runs,
                    force=args.force,
                )
            except BenchSafetyError as e:
                print(f"SKIPPED {label}: {e}")
                rows.append({"label": label, "model": model, "threads": threads, "skipped": str(e)})
                continue

            if result.errors:
                print(f"ERROR {label}: {result.errors}")
            rows.append(
                {
                    "label": label,
                    "model": model,
                    "threads": threads,
                    "mean_transcribe_seconds": result.mean_transcribe_seconds,
                    "mean_rtf": result.mean_rtf,
                    "model_load_seconds": result.model_load_seconds,
                    "peak_rss_mb": result.peak_rss_mb,
                    "errors": result.errors,
                }
            )
            print(f"OK {label}: mean={result.mean_transcribe_seconds}s RTF={result.mean_rtf} "
                  f"load={result.model_load_seconds}s peak_rss={result.peak_rss_mb}MB")

    baseline_row = next(
        (r for r in rows if r["model"] == baseline_model and r["threads"] == baseline_threads
         and r.get("mean_transcribe_seconds")),
        None,
    )

    print("\n=== Comparison (relative to baseline: "
          f"{baseline_model} @ {baseline_threads} threads, matching api.py's production default) ===")
    print(f"{'config':<24} {'mean_s':>8} {'RTF':>8} {'load_s':>8} {'peak_MB':>9} {'speedup':>8}")
    for r in rows:
        if "skipped" in r:
            print(f"{r['label']:<24} SKIPPED ({r['skipped'][:60]}...)")
            continue
        speedup = (
            round(baseline_row["mean_transcribe_seconds"] / r["mean_transcribe_seconds"], 2)
            if baseline_row and r.get("mean_transcribe_seconds")
            else None
        )
        print(f"{r['label']:<24} {r['mean_transcribe_seconds']!s:>8} {r['mean_rtf']!s:>8} "
              f"{r['model_load_seconds']!s:>8} {r['peak_rss_mb']!s:>9} {speedup!s:>8}")

    if baseline_row is None:
        print("\nNote: baseline config did not complete successfully, speedup column is relative to nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
