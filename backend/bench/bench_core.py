"""Shared benchmark logic used by both run_bench.py (single config, CLI)
and run_suite.py (a small, hardcoded-safe comparison matrix). Isolated
from api.py on purpose -- imports only faster_whisper/ctranslate2/numpy
directly, never the production app module, so running a benchmark can
never trigger storage.init_db() or any other app-startup side effect.

Mirrors api.py's real preprocessing (decode + peak-normalize to 0.95,
see api.py's /api/process-meeting handler) so benchmark numbers reflect
what production actually does to the audio before Whisper sees it.
"""
from __future__ import annotations

import json
import platform
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from . import sysinfo
from .wer import word_error_rate

RESULTS_DIR = Path(__file__).resolve().parent / "results"
TRANSCRIPTS_DIR = RESULTS_DIR / "transcripts"
RESULTS_JSONL = RESULTS_DIR / "bench_results.jsonl"

# Approximate int8 CPU memory footprint per model, derived from each
# model's published parameter count (not measured on this machine yet --
# every real run records its own peak RSS, which should supersede these
# over time). Used only as a pre-flight safety gate before attempting to
# load a model, never reported as a measurement.
MODEL_RAM_ESTIMATE_MB = {
    "tiny": 100,
    "tiny.en": 100,
    "base": 200,
    "base.en": 200,
    "small": 600,
    "small.en": 600,
    "medium": 1700,
    "medium.en": 1700,
    "large-v3": 3200,
    "large-v3-turbo": 1900,
}
DEFAULT_RAM_ESTIMATE_MB = 2000  # conservative guess for an unlisted model name
SAFETY_MARGIN_MULTIPLIER = 1.5
OS_HEADROOM_MB = 500


class BenchSafetyError(RuntimeError):
    """Raised when a requested config looks likely to exhaust RAM or
    exceed available CPU threads -- caller should report, not crash."""


@dataclass
class RunTiming:
    transcribe_seconds: float
    rtf: float
    segments_count: int
    cpu_utilization_percent: Optional[float]


@dataclass
class BenchResult:
    run_id: str
    timestamp: str
    clip: str
    audio_duration_seconds: float
    model: str
    device: str
    compute_type: str
    threads: int
    machine_platform: str
    python_version: str
    faster_whisper_version: Optional[str]
    ctranslate2_version: Optional[str]
    decode_seconds: float
    model_load_seconds: float
    warmup_runs: int
    timed_runs: list
    mean_transcribe_seconds: Optional[float]
    stdev_transcribe_seconds: Optional[float]
    mean_rtf: Optional[float]
    peak_rss_mb: Optional[float]
    detected_language: Optional[str]
    language_probability: Optional[float]
    accuracy: str
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def check_ram_safety(model: str, force: bool = False) -> list[str]:
    """Returns a list of warnings. Raises BenchSafetyError if unsafe and
    not overridden with force=True."""
    warnings: list[str] = []
    ram = sysinfo.get_system_ram_mb()
    if ram is None:
        warnings.append("Could not read system RAM on this OS -- proceeding without a safety check.")
        return warnings

    estimate = MODEL_RAM_ESTIMATE_MB.get(model, DEFAULT_RAM_ESTIMATE_MB)
    required = estimate * SAFETY_MARGIN_MULTIPLIER + OS_HEADROOM_MB
    if ram["available_mb"] < required:
        msg = (
            f"SKIPPED: insufficient RAM headroom for model='{model}' "
            f"(available {ram['available_mb']:.0f} MB, want >= {required:.0f} MB "
            f"= {estimate} MB estimated model size * {SAFETY_MARGIN_MULTIPLIER} safety margin "
            f"+ {OS_HEADROOM_MB} MB OS headroom)."
        )
        if force:
            warnings.append(msg + " Proceeding anyway because --force was passed.")
        else:
            raise BenchSafetyError(msg + " Pass --force to override.")
    return warnings


def validate_threads(threads: int) -> None:
    import os as _os

    logical = _os.cpu_count() or 1
    if threads < 1:
        raise BenchSafetyError(f"threads must be >= 1, got {threads}")
    if threads > logical:
        raise BenchSafetyError(
            f"Requested {threads} threads but this machine only has {logical} logical "
            f"processors -- refusing rather than oversubscribing the CPU."
        )


def validate_compute_type(device: str, compute_type: str) -> None:
    import ctranslate2

    if device == "cpu":
        supported = ctranslate2.get_supported_compute_types("cpu")
        if compute_type not in supported:
            raise BenchSafetyError(
                f"compute_type='{compute_type}' is not in this CPU's supported set {sorted(supported)}."
            )
    # For device in {"cuda", "auto"} we deliberately don't pre-validate --
    # on this machine cuda is expected to fail at load time (no NVIDIA
    # driver), and that failure is itself a useful, honestly-reported
    # result rather than something to suppress.


def _package_version(name: str) -> Optional[str]:
    import importlib.metadata

    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def decode_clip(clip_path: Path) -> tuple[np.ndarray, float, float]:
    """Returns (wav, duration_seconds, decode_seconds). Same decode +
    peak-normalize (to 0.95) as api.py's production path."""
    from faster_whisper import decode_audio

    t0 = time.time()
    wav = decode_audio(str(clip_path))
    peak = float(np.abs(wav).max())
    if peak > 1e-4:
        wav = wav * (0.95 / peak)
    decode_seconds = time.time() - t0
    duration_seconds = len(wav) / 16000
    return wav, duration_seconds, decode_seconds


def find_reference_transcript(clip_path: Path) -> Optional[str]:
    """Convention: a ground-truth transcript for foo.wav lives next to it
    as foo.reference.txt. Returns None (not an empty string) if absent."""
    ref_path = clip_path.with_suffix("").with_suffix(".reference.txt")
    if ref_path.exists():
        return ref_path.read_text(encoding="utf-8", errors="ignore")
    return None


def run_one(
    clip_path: Path,
    model: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    threads: Optional[int] = None,
    warmup: int = 0,
    runs: int = 1,
    multilingual: bool = False,
    force: bool = False,
) -> BenchResult:
    import os as _os

    threads = threads or (_os.cpu_count() or 4)
    run_id = f"{model}_{device}_{compute_type}_t{threads}_{uuid.uuid4().hex[:8]}"
    errors: list[str] = []
    warnings: list[str] = []

    # Deliberately not caught here: BenchSafetyError should propagate to
    # the caller (run_bench.py / run_suite.py), which reports it cleanly
    # and skips this config rather than crashing the whole process.
    validate_threads(threads)
    validate_compute_type(device, compute_type)
    warnings.extend(check_ram_safety(model, force=force))

    wav, duration_seconds, decode_seconds = decode_clip(clip_path)

    model_load_seconds = 0.0
    timed_runs: list[dict] = []
    detected_language = None
    language_probability = None
    peak_rss_mb = None
    last_segments: list[dict] = []

    try:
        from faster_whisper import WhisperModel

        t0 = time.time()
        wm = WhisperModel(model, device=device, compute_type=compute_type, cpu_threads=threads)
        model_load_seconds = time.time() - t0

        mem = sysinfo.get_process_memory_mb()
        if mem:
            peak_rss_mb = mem["peak_working_set_mb"]

        for i in range(warmup):
            segments, _info = wm.transcribe(wav, beam_size=5, multilingual=multilingual)
            list(segments)  # force the lazy generator to run, then discard

        for i in range(runs):
            cpu_start = sysinfo.sample_cpu_time()
            t_run = time.time()
            segments, info = wm.transcribe(
                wav, beam_size=5, vad_filter=True, vad_parameters={"threshold": 0.35}, multilingual=multilingual
            )
            segs = list(segments)
            run_seconds = time.time() - t_run
            cpu_end = sysinfo.sample_cpu_time()

            detected_language = info.language
            language_probability = info.language_probability
            last_segments = [{"start": s.start, "end": s.end, "text": s.text} for s in segs]

            mem = sysinfo.get_process_memory_mb()
            if mem and (peak_rss_mb is None or mem["peak_working_set_mb"] > peak_rss_mb):
                peak_rss_mb = mem["peak_working_set_mb"]

            timed_runs.append(
                asdict(
                    RunTiming(
                        transcribe_seconds=round(run_seconds, 3),
                        rtf=round(run_seconds / duration_seconds, 4) if duration_seconds else None,
                        segments_count=len(segs),
                        cpu_utilization_percent=sysinfo.cpu_utilization_percent(
                            cpu_start, cpu_end, threads
                        ),
                    )
                )
            )
        del wm
    except Exception as e:  # noqa: BLE001 -- intentionally broad: a bad
        # device/compute_type/model combo should produce a recorded error,
        # never a crashed benchmark script.
        errors.append(f"{type(e).__name__}: {e}")

    mean_t = stdev_t = mean_rtf = None
    if timed_runs:
        times = [r["transcribe_seconds"] for r in timed_runs]
        rtfs = [r["rtf"] for r in timed_runs if r["rtf"] is not None]
        mean_t = round(sum(times) / len(times), 3)
        if len(times) > 1:
            mean_sq = sum((t - mean_t) ** 2 for t in times) / (len(times) - 1)
            stdev_t = round(mean_sq**0.5, 3)
        if rtfs:
            mean_rtf = round(sum(rtfs) / len(rtfs), 4)

    reference = find_reference_transcript(clip_path)
    hypothesis_text = " ".join(seg["text"] for seg in last_segments)
    if reference:
        wer_result = word_error_rate(reference, hypothesis_text)
        accuracy = json.dumps(asdict(wer_result))
    else:
        accuracy = "NO GROUND TRUTH - SPEED ONLY"

    result = BenchResult(
        run_id=run_id,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        clip=str(clip_path),
        audio_duration_seconds=round(duration_seconds, 2),
        model=model,
        device=device,
        compute_type=compute_type,
        threads=threads,
        machine_platform=platform.platform(),
        python_version=sys.version.split()[0],
        faster_whisper_version=_package_version("faster-whisper"),
        ctranslate2_version=_package_version("ctranslate2"),
        decode_seconds=round(decode_seconds, 3),
        model_load_seconds=round(model_load_seconds, 3),
        warmup_runs=warmup,
        timed_runs=timed_runs,
        mean_transcribe_seconds=mean_t,
        stdev_transcribe_seconds=stdev_t,
        mean_rtf=mean_rtf,
        peak_rss_mb=round(peak_rss_mb, 1) if peak_rss_mb else None,
        detected_language=detected_language,
        language_probability=round(language_probability, 3) if language_probability else None,
        accuracy=accuracy,
        errors=errors,
        warnings=warnings,
    )

    _save_result(result, last_segments)
    return result


def _save_result(result: BenchResult, segments: list[dict]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    with open(RESULTS_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(result)) + "\n")

    transcript_path = TRANSCRIPTS_DIR / f"{result.run_id}.json"
    transcript_path.write_text(
        json.dumps({"run_id": result.run_id, "segments": segments}, indent=2), encoding="utf-8"
    )
