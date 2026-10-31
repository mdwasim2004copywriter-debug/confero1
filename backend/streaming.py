"""
Phase B: chunk-by-chunk transcription during a recording.

Chunks are captured client-side as short, independently-decodable audio
clips (see frontend/src/pages/RecordMeeting.jsx's startStreamingChunks,
built on the same "stop/restart a MediaRecorder on the same stream"
pattern the existing live-caption chunker (startChunkCaptions) already
uses in production -- not a new, unproven capture mechanism). Each
chunk is transcribed through api.py's existing _transcribe_wav(), the
exact same Groq-first/local-CPU-fallback logic the whole-file path uses
-- nothing in this module duplicates that.

This module owns only what's genuinely new for streaming: chunk audio
file lifecycle on disk, the overlap-aware transcript/audio merge done at
finalize, and chunk latency instrumentation.

Diarization is explicitly NOT touched: finalize reassembles one
continuous wav array plus one merged transcript, then hands both to the
existing, unmodified cluster_speakers() exactly as the batch path
always has (see api.py's _finalize_result, shared by both paths).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# --- Configuration: one place to change chunk timing, never scattered
# magic numbers through the endpoints. The server does not enforce the
# client send exactly CHUNK_SECONDS-long chunks -- sequence_number order
# and each chunk's own reported start_time/end_time are what matter here,
# this constant is the default the frontend is built around and the
# value used for the "is there a backlog" check in api.py. ---
CHUNK_SECONDS = 10.0
OVERLAP_SECONDS = 1.5
SAMPLE_RATE = 16000  # matches load_audio()'s output elsewhere in this codebase

STREAM_TMP_DIR = Path(__file__).parent / "output" / "stream_tmp"
METRICS_LOG_PATH = Path(__file__).parent / "output" / "stream_metrics.jsonl"


def chunk_dir(stream_id: str) -> Path:
    d = STREAM_TMP_DIR / stream_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def chunk_audio_path(stream_id: str, sequence_number: int, suffix: str) -> Path:
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return chunk_dir(stream_id) / f"{sequence_number:06d}{suffix}"


def cleanup_stream_dir(stream_id: str) -> None:
    """Deletes every chunk audio file for one session. Called after
    finalize has produced a result (success or given-up-on-pending-
    chunks) -- audio is never kept past that point. Known gap, stated
    plainly rather than silently handled: if the process crashes mid-
    session, this never runs and the directory is simply left on disk --
    there is no background janitor in this phase. See README/limitations."""
    d = STREAM_TMP_DIR / stream_id
    if not d.exists():
        return
    for f in d.iterdir():
        try:
            f.unlink()
        except OSError:
            pass
    try:
        d.rmdir()
    except OSError:
        pass


def log_chunk_metrics(**fields) -> None:
    """Append-only JSONL, one line per chunk/event. Logs identity and
    timing fields only -- never transcript text or audio, nothing
    sensitive goes in this log."""
    METRICS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {"logged_at": round(time.time(), 3), **fields}
    with open(METRICS_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


@dataclass
class ChunkForMerge:
    sequence_number: int
    start_time: float  # absolute seconds into the recording where this chunk's OWN audio begins (includes its leading overlap, if any)
    transcript_raw: list  # [(start, end, text, low_confidence), ...] in chunk-local time (0 = this chunk's own start)


def _overlap_duration(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


# Phase B.2: how much of a segment's own duration must sit inside a
# previous-chunk segment before the two are treated as the same spoken
# content. Still purely timestamp-based (no text matching); this is a
# duration ratio, not a text comparison. 0.5 was picked because a real
# duplicate (the same sentence transcribed independently by two
# overlapping chunks) lands with near-total overlap -- the two confirmed
# real examples that exposed the bug this fixes were effectively 100%
# overlapping -- while two genuinely different adjacent sentences that
# merely touch at a shared pause boundary share only a sliver.
MIN_DUPLICATE_OVERLAP_FRACTION = 0.5


def merge_chunk_transcripts(chunks: list[ChunkForMerge]) -> list[tuple]:
    """Deterministic, timestamp-based merge across ordered chunks -- no
    fuzzy text matching anywhere, by design (fuzzy dedup risks deleting
    real, legitimately repeated words; the brief is explicit that
    conservative is preferred over clever here).

    Each chunk's segments are converted from chunk-local to absolute
    recording time. For every chunk after the first, a segment is
    dropped only if it meaningfully overlaps (>= MIN_DUPLICATE_OVERLAP_
    FRACTION of its own duration) a segment the previous chunk already
    produced -- i.e. only when keeping both would be an actual
    duplicate. If the previous chunk has nothing overlapping (its own
    tail was truncated, VAD-trimmed, or simply missed it), this chunk's
    copy is kept instead of silently losing real speech. This is the
    actual point of the overlap: it's insurance against one chunk's
    truncated tail, not just a region to always discard.

    Phase B.2 fix: this used to additionally require the candidate
    segment to end at or before `chunk.start_time + overlap_seconds`
    (i.e. fit *entirely* inside the ~1.5s overlap window) before even
    checking the previous chunk. That's structurally impossible for any
    segment longer than overlap_seconds -- most real spoken sentences --
    so the dedup check was effectively unreachable for realistic speech.
    A real recording surfaced exactly this: duplicate sentences with
    near-identical timestamps (e.g. 00:42-00:44 and 00:42-00:45) both
    surviving because neither was ever short enough to pass that gate.
    The gate is removed; only the overlap-fraction check above remains,
    which is what actually distinguishes a duplicate from two merely-
    adjacent segments, and was already doing the real work before.
    """
    chunks_sorted = sorted(chunks, key=lambda c: c.sequence_number)
    per_chunk_abs: list[list[tuple]] = []
    for chunk in chunks_sorted:
        abs_segs = [
            (chunk.start_time + s, chunk.start_time + e, text, low_conf)
            for (s, e, text, low_conf) in chunk.transcript_raw
        ]
        per_chunk_abs.append(abs_segs)

    merged: list[tuple] = []
    for i, abs_segs in enumerate(per_chunk_abs):
        if i == 0:
            merged.extend(abs_segs)
            continue
        previous_segs = per_chunk_abs[i - 1]
        for (a_start, a_end, text, low_conf) in abs_segs:
            duration = a_end - a_start
            is_duplicate = False
            if duration > 0:
                for (p_start, p_end, _, _) in previous_segs:
                    p_duration = p_end - p_start
                    shorter = min(duration, p_duration)
                    if shorter <= 0:
                        continue
                    if _overlap_duration(a_start, a_end, p_start, p_end) / shorter >= MIN_DUPLICATE_OVERLAP_FRACTION:
                        is_duplicate = True
                        break
            if is_duplicate:
                continue  # the previous chunk already transcribed this same stretch
            merged.append((a_start, a_end, text, low_conf))

    merged.sort(key=lambda seg: seg[0])
    return merged


def reassemble_audio(chunks: list[dict]) -> np.ndarray:
    """Concatenates each chunk's decoded audio in sequence order into one
    continuous, gapless timeline whose absolute sample index matches the
    timestamps merge_chunk_transcripts() produces. A chunk with no audio
    path (failed/missing) contributes nothing; that gap in the recording
    is a real, known hole -- not silently patched over with silence.

    Phase B.1 fix: the amount trimmed from each chunk (after the first)
    is now the *measured* overlap -- the previous chunk's own reported
    end_time minus this chunk's reported start_time -- never a fixed
    `OVERLAP_SECONDS` constant. The constant was wrong in two ways: (1)
    client-side setTimeout drift means the real overlap isn't exactly
    OVERLAP_SECONDS (a real test recording showed it growing measurably
    over ~15 chunks), and (2) if the previous chunk in sequence had
    failed, the fixed trim was still applied across what is actually a
    gap, cutting real audio that was never overlapping anything. Using
    each pair's own reported times fixes both without changing any
    timing, model, or chunk-duration behavior.

    `chunks` is the list of row dicts storage.list_stream_chunks()
    returns (needs sequence_number, status, audio_path, start_time,
    end_time). Uses the same decode function as the rest of the app
    (speaker_id.load_audio) rather than a second audio pipeline.
    """
    from speaker_id import load_audio  # local import: avoids a circular import with api.py at module load time

    ok_chunks = sorted(
        (c for c in chunks if c["status"] == "ok" and c["audio_path"]), key=lambda c: c["sequence_number"]
    )
    pieces = []
    prev_end_time = None
    for chunk in ok_chunks:
        wav = load_audio(chunk["audio_path"])
        if prev_end_time is not None:
            measured_overlap = prev_end_time - chunk["start_time"]
            chunk_duration = len(wav) / SAMPLE_RATE
            # Clamp: a negative value means there's an actual gap (a
            # chunk between these two failed) -- nothing to trim, not a
            # negative trim. A value larger than this chunk's own
            # duration would mean something is wrong with the reported
            # timestamps -- capped so a trim can never consume more
            # audio than the chunk actually has.
            trim_seconds = max(0.0, min(measured_overlap, chunk_duration))
            trim_samples = int(round(trim_seconds * SAMPLE_RATE))
            wav = wav[trim_samples:]
        pieces.append(wav)
        prev_end_time = chunk["end_time"]
    if not pieces:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(pieces)
