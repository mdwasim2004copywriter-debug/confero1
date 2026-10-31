"""Benchmark one faster-whisper configuration against one audio clip.

Does not touch any production code path (api.py is never imported).
Appends one JSON line to backend/bench/results/bench_results.jsonl and
saves the transcript it produced to backend/bench/results/transcripts/.

Example:
    python backend/bench/run_bench.py --clip output/last_recording.wav \\
        --model small --device cpu --compute-type int8 --threads 8 --runs 3
"""
from __future__ import annotations

import argparse
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clip", type=Path, default=DEFAULT_CLIP, help=f"Audio file (default: {DEFAULT_CLIP})")
    parser.add_argument("--model", default="small", help="tiny/base/small/medium/large-v3/... (default: small)")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda", "auto"], help="default: cpu")
    parser.add_argument("--compute-type", default="int8", help="int8/int8_float32/float32 (default: int8)")
    parser.add_argument("--threads", type=int, default=None, help="default: os.cpu_count()")
    parser.add_argument("--warmup", type=int, default=0, help="untimed warmup transcriptions (default: 0)")
    parser.add_argument("--runs", type=int, default=1, help="timed repetitions (default: 1)")
    parser.add_argument("--multilingual", action="store_true", help="matches the 'mixed language' checkbox behavior")
    parser.add_argument("--force", action="store_true", help="override the RAM safety guard")
    args = parser.parse_args()

    if not args.clip.exists():
        print(f"ERROR: clip not found: {args.clip}", file=sys.stderr)
        return 2

    try:
        result = bench_core.run_one(
            clip_path=args.clip,
            model=args.model,
            device=args.device,
            compute_type=args.compute_type,
            threads=args.threads,
            warmup=args.warmup,
            runs=args.runs,
            multilingual=args.multilingual,
            force=args.force,
        )
    except BenchSafetyError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3

    print(f"run_id: {result.run_id}")
    print(f"clip: {result.clip}  ({result.audio_duration_seconds}s)")
    print(f"model={result.model} device={result.device} compute_type={result.compute_type} threads={result.threads}")
    print(f"decode: {result.decode_seconds}s   model load: {result.model_load_seconds}s")
    if result.errors:
        print(f"ERRORS: {result.errors}")
    for i, r in enumerate(result.timed_runs):
        print(f"  run {i}: {r['transcribe_seconds']}s  RTF={r['rtf']}  segments={r['segments_count']}  "
              f"cpu_util={r['cpu_utilization_percent']}%")
    if result.mean_transcribe_seconds is not None:
        print(f"mean: {result.mean_transcribe_seconds}s (stdev {result.stdev_transcribe_seconds})  "
              f"mean RTF={result.mean_rtf}")
    print(f"detected language: {result.detected_language} (p={result.language_probability})")
    print(f"peak RSS: {result.peak_rss_mb} MB")
    print(f"accuracy: {result.accuracy}")
    for w in result.warnings:
        print(f"WARNING: {w}")
    print(f"\nSaved to {bench_core.RESULTS_JSONL}")
    return 0 if not result.errors else 1


if __name__ == "__main__":
    sys.exit(main())
