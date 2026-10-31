"""
Integration tests for Phase B streaming (backend only -- see
bench/README.md-style note in the final report for what's not covered:
real browser MediaRecorder/IndexedDB behavior isn't exercised here).

Run directly against a live server: start `uvicorn api:app --port 8000`
first, then:
    python test_streaming.py

No assertion is soft -- a failure prints and the script exits non-zero,
same convention as test_meeting_intelligence.py.
"""
import sys
import time

import numpy as np
import requests
import soundfile as sf

import streaming

BASE = "http://localhost:8000"
FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    if not condition:
        FAILURES.append(f"{label}: {detail}")
    print(f"[{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))


def make_wav_bytes(duration_s=3.0, freq=220.0, sr=16000):
    """A real, decodable wav tone -- not silence (silence can get VAD-
    filtered to zero segments, which would make these tests about VAD
    behavior instead of about the streaming plumbing being tested here)."""
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    tone = 0.3 * np.sin(2 * np.pi * freq * t).astype(np.float32)
    import io

    buf = io.BytesIO()
    sf.write(buf, tone, sr, format="WAV")
    return buf.getvalue()


def start_session():
    r = requests.post(f"{BASE}/api/stream/start", data={"title": "Streaming Test", "meeting_type": "General"})
    check("stream_start returns 200", r.status_code == 200, f"got {r.status_code}: {r.text}")
    data = r.json()
    check("stream_start returns a stream_id", bool(data.get("stream_id")))
    return data["stream_id"]


def upload_chunk(stream_id, seq, start_time, end_time, audio_bytes=None):
    audio_bytes = audio_bytes or make_wav_bytes()
    r = requests.post(
        f"{BASE}/api/stream/chunk",
        files={"audio": ("chunk.wav", audio_bytes, "audio/wav")},
        data={"stream_id": stream_id, "sequence_number": seq, "start_time": start_time, "end_time": end_time},
    )
    return r


# --- 0. pure unit tests for the overlap merge logic (no server, no audio) -
def test_merge_drops_duplicate_in_covered_overlap():
    # chunk0 has a segment at abs [8.8,10.0]. chunk1 starts at 8.5 (1.5s
    # overlap) and its local segment (0.5,1.3) maps to abs [9.0,9.8],
    # which nearly-fully overlaps chunk0's [8.8,10.0] -- a real duplicate.
    chunks = [
        streaming.ChunkForMerge(0, 0.0, [(8.8, 10.0, "the quarterly numbers", False)]),
        streaming.ChunkForMerge(1, 8.5, [(0.5, 1.3, "the quarterly numbers", False)]),
    ]
    merged = streaming.merge_chunk_transcripts(chunks)
    texts = [m[2] for m in merged]
    check(
        "duplicate overlap segment is dropped when the previous chunk already covers it",
        texts.count("the quarterly numbers") == 1,
        str(texts),
    )


def test_merge_drops_duplicate_realistic_sentence_length():
    # Phase B.2 regression test: reproduces the exact bug a real
    # recording surfaced -- duplicate full sentences (00:42-00:44 and
    # 00:42-00:45 for the same content) that survived because the old
    # gate required a segment to fit *entirely* inside the 1.5s overlap
    # window, which no 2-3 second sentence ever can. Same shape here:
    # chunk0's segment is 2s long, chunk1's version of the same content
    # is 3s long, both far longer than OVERLAP_SECONDS (1.5s).
    chunks = [
        streaming.ChunkForMerge(0, 0.0, [(42.0, 44.0, "same sentence spoken once", False)]),  # abs [42,44] -- mirrors the real "00:42-00:44" report
        streaming.ChunkForMerge(1, 41.0, [(1.0, 4.0, "same sentence spoken once", False)]),  # abs [42,45] -- mirrors the real "00:42-00:45" report
    ]
    merged = streaming.merge_chunk_transcripts(chunks)
    texts = [m[2] for m in merged]
    check(
        "a realistic multi-second duplicate (longer than OVERLAP_SECONDS) is still dropped -- this is the exact bug that was fixed",
        texts.count("same sentence spoken once") == 1,
        str(texts),
    )


def test_merge_keeps_uncovered_overlap_segment():
    # chunk0's tail is EMPTY in the overlap zone (e.g. VAD trimmed it, or
    # the recorder cut off mid-word) -- chunk1's version of that same
    # time range must be kept, not dropped, since it's the only copy.
    chunks = [
        streaming.ChunkForMerge(0, 0.0, [(0.0, 5.0, "earlier unrelated sentence", False)]),  # abs [0,5], nothing near the boundary
        streaming.ChunkForMerge(1, 8.5, [(0.5, 1.3, "a word chunk zero missed", False)]),  # abs [9.0,9.8], nothing in chunk0 overlaps it
    ]
    merged = streaming.merge_chunk_transcripts(chunks)
    texts = [m[2] for m in merged]
    check(
        "a segment with no overlapping segment in the previous chunk is kept, not deleted",
        "a word chunk zero missed" in texts,
        str(texts),
    )


def test_merge_keeps_distinct_adjacent_segments_that_merely_touch():
    # Two DIFFERENT sentences that just happen to share a sliver of time
    # at a shared pause boundary must NOT be treated as duplicates --
    # only a small fraction of either one's duration overlaps the other.
    chunks = [
        streaming.ChunkForMerge(0, 0.0, [(0.0, 3.0, "first distinct sentence", False)]),  # abs [0,3]
        streaming.ChunkForMerge(1, 2.0, [(0.8, 3.8, "second distinct sentence", False)]),  # abs [2.8,5.8] -- only 0.2s overlaps chunk0's [0,3]
    ]
    merged = streaming.merge_chunk_transcripts(chunks)
    texts = [m[2] for m in merged]
    check(
        "two genuinely different adjacent segments that merely touch are both kept, not merged into one",
        texts.count("first distinct sentence") == 1 and texts.count("second distinct sentence") == 1,
        str(texts),
    )


def test_merge_keeps_straddling_segment_and_sorts_output():
    # A segment with no real counterpart in the previous chunk is always
    # kept, and the final merged list must be chronologically sorted
    # regardless of input chunk order.
    chunks = [
        streaming.ChunkForMerge(1, 8.5, [(0.8, 2.0, "straddles the boundary", False)]),  # abs [9.3, 10.5], nothing in chunk0 overlaps it
        streaming.ChunkForMerge(0, 0.0, [(0.0, 3.0, "first chunk text", False)]),
    ]
    merged = streaming.merge_chunk_transcripts(chunks)
    check("a non-duplicate segment is kept", any(m[2] == "straddles the boundary" for m in merged), str(merged))
    starts = [m[0] for m in merged]
    check("merge output is chronologically sorted regardless of input chunk order", starts == sorted(starts), str(starts))


def _write_tmp_wav(wav, sr):
    import tempfile as _tempfile

    p = _tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    sf.write(p, wav, sr)
    return p


def test_reassemble_audio_is_gapless_and_trims_measured_overlap():
    sr = streaming.SAMPLE_RATE
    wav0 = np.linspace(0, 1, int(sr * 10), dtype=np.float32)  # 10s, distinct ramp so length math is checkable
    wav1 = np.linspace(0, 1, int(sr * 10), dtype=np.float32)
    path0, path1 = _write_tmp_wav(wav0, sr), _write_tmp_wav(wav1, sr)

    # Exactly the nominal 1.5s overlap: chunk1 starts at 8.5, chunk0 ends at 10.0
    chunks = [
        {"sequence_number": 0, "status": "ok", "audio_path": path0, "start_time": 0.0, "end_time": 10.0},
        {"sequence_number": 1, "status": "ok", "audio_path": path1, "start_time": 8.5, "end_time": 18.5},
    ]
    result = streaming.reassemble_audio(chunks)
    expected_len = len(wav0) + (len(wav1) - int(1.5 * sr))
    check(
        "reassembled audio length == chunk0 full + chunk1 minus its measured overlap (gapless, no duplication)",
        abs(len(result) - expected_len) <= 1,
        f"got {len(result)}, expected {expected_len}",
    )


def test_reassemble_audio_uses_real_overlap_not_fixed_constant():
    # Phase B.1 regression test: this is the exact bug class found in the
    # audit -- real client clock drift means the true overlap isn't
    # exactly OVERLAP_SECONDS. Here chunk1 actually started 8.7s in (not
    # 8.5s), i.e. a real overlap of 1.3s, not the nominal 1.5s. Before the
    # fix, reassemble_audio always trimmed a fixed 1.5s regardless of what
    # the timestamps said, which would have cut 0.2s of real, non-
    # overlapping audio out of chunk1.
    sr = streaming.SAMPLE_RATE
    wav0 = np.linspace(0, 1, int(sr * 10), dtype=np.float32)
    wav1 = np.linspace(0, 1, int(sr * 10), dtype=np.float32)
    path0, path1 = _write_tmp_wav(wav0, sr), _write_tmp_wav(wav1, sr)

    chunks = [
        {"sequence_number": 0, "status": "ok", "audio_path": path0, "start_time": 0.0, "end_time": 10.0},
        {"sequence_number": 1, "status": "ok", "audio_path": path1, "start_time": 8.7, "end_time": 18.7},
    ]
    result = streaming.reassemble_audio(chunks)
    expected_len = len(wav0) + (len(wav1) - int(round(1.3 * sr)))  # measured overlap is 10.0 - 8.7 = 1.3s, not 1.5s
    check(
        "trim amount follows the chunks' own reported times (1.3s here), not the fixed OVERLAP_SECONDS constant (1.5s)",
        abs(len(result) - expected_len) <= 1,
        f"got {len(result)}, expected {expected_len}",
    )


def test_reassemble_audio_does_not_trim_across_a_real_gap():
    # chunk 1 failed and contributes no audio; chunk 2 immediately follows
    # chunk 0 in the ok-chunks list but does NOT actually overlap it (its
    # start_time is well after chunk 0's end_time). Before the fix, a
    # fixed trim would have been wrongly applied here too (trimming chunk
    # 2 as if it directly, overlappingly followed chunk 0).
    sr = streaming.SAMPLE_RATE
    wav0 = np.linspace(0, 1, int(sr * 10), dtype=np.float32)
    wav2 = np.linspace(0, 1, int(sr * 10), dtype=np.float32)
    path0, path2 = _write_tmp_wav(wav0, sr), _write_tmp_wav(wav2, sr)

    chunks = [
        {"sequence_number": 0, "status": "ok", "audio_path": path0, "start_time": 0.0, "end_time": 10.0},
        {"sequence_number": 1, "status": "failed", "audio_path": None, "start_time": 8.5, "end_time": 18.5},
        {"sequence_number": 2, "status": "ok", "audio_path": path2, "start_time": 17.0, "end_time": 27.0},
    ]
    result = streaming.reassemble_audio(chunks)
    expected_len = len(wav0) + len(wav2)  # no overlap with chunk 0 -- nothing trimmed from chunk 2
    check(
        "no audio is trimmed from a chunk that doesn't actually overlap the previous ok chunk (gap left by a failed chunk)",
        abs(len(result) - expected_len) <= 1,
        f"got {len(result)}, expected {expected_len}",
    )


def test_reassemble_audio_skips_failed_chunks_without_crashing():
    chunks = [
        {"sequence_number": 0, "status": "failed", "audio_path": None, "start_time": 0.0, "end_time": 10.0},
        {"sequence_number": 1, "status": "failed", "audio_path": None, "start_time": 8.5, "end_time": 18.5},
    ]
    result = streaming.reassemble_audio(chunks)
    check("reassembling with only failed chunks returns empty audio, doesn't crash", len(result) == 0, str(len(result)))


# --- 1. sequence ordering + out-of-order responses ----------------------
def test_out_of_order_chunks():
    stream_id = start_session()
    # Upload chunk 1 before chunk 0 -- server must accept both and rely
    # on sequence_number, never arrival order.
    r1 = upload_chunk(stream_id, 1, 10.0, 20.0)
    r0 = upload_chunk(stream_id, 0, 0.0, 10.0)
    check("chunk 1 uploaded before chunk 0 still succeeds", r1.status_code == 200, r1.text)
    check("chunk 0 uploaded after chunk 1 still succeeds", r0.status_code == 200, r0.text)

    r = requests.post(f"{BASE}/api/stream/finalize", data={"stream_id": stream_id})
    check("finalize after out-of-order chunks returns 200", r.status_code == 200, r.text)
    data = r.json()
    check("finalize produced a transcript list", "transcript" in data, str(data))
    if "transcript" in data and len(data["transcript"]) >= 2:
        starts = [seg["start"] for seg in data["transcript"]]
        check("merged transcript is chronologically sorted", starts == sorted(starts), str(starts))


# --- 2. duplicate chunk upload / idempotency -----------------------------
def test_duplicate_chunk_idempotent():
    stream_id = start_session()
    audio = make_wav_bytes()
    r1 = upload_chunk(stream_id, 0, 0.0, 10.0, audio)
    r2 = upload_chunk(stream_id, 0, 0.0, 10.0, audio)  # retry of the exact same chunk
    check("first upload of chunk 0 succeeds", r1.status_code == 200, r1.text)
    check("duplicate upload of chunk 0 succeeds", r2.status_code == 200, r2.text)
    d2 = r2.json()
    check("duplicate upload is flagged as replayed", d2.get("replayed") is True, str(d2))
    check(
        "duplicate upload returns the same segments as the original (not re-transcribed into something different)",
        d2.get("segments") == r1.json().get("segments"),
        f"{d2.get('segments')} vs {r1.json().get('segments')}",
    )
    requests.post(f"{BASE}/api/stream/finalize", data={"stream_id": stream_id})  # cleanup


# --- 3. failed chunk handling ---------------------------------------------
def test_failed_chunk_not_silently_dropped():
    stream_id = start_session()
    upload_chunk(stream_id, 0, 0.0, 10.0)
    # Chunk 1 is garbage bytes, not decodable audio -- must be recorded
    # as failed, not silently skipped or crash the endpoint.
    r = requests.post(
        f"{BASE}/api/stream/chunk",
        files={"audio": ("chunk.wav", b"not a real wav file", "audio/wav")},
        data={"stream_id": stream_id, "sequence_number": 1, "start_time": 10.0, "end_time": 20.0},
    )
    check("a corrupt chunk returns 200, not a 500 crash", r.status_code == 200, f"{r.status_code}: {r.text}")
    d = r.json()
    check("a corrupt chunk is reported as status=failed", d.get("status") == "failed", str(d))

    r_final = requests.post(f"{BASE}/api/stream/finalize", data={"stream_id": stream_id})
    check("finalize still succeeds despite one failed chunk", r_final.status_code == 200, r_final.text)
    data = r_final.json()
    check("finalize reports the failed chunk, doesn't pretend it succeeded", data.get("failedChunks") == [1], str(data.get("failedChunks")))


# --- 4. finalize with all chunks successful -------------------------------
def test_finalize_all_success():
    stream_id = start_session()
    upload_chunk(stream_id, 0, 0.0, 10.0)
    upload_chunk(stream_id, 1, 8.5, 18.5)  # overlapping per OVERLAP_SECONDS=1.5
    r = requests.post(f"{BASE}/api/stream/finalize", data={"stream_id": stream_id})
    check("finalize with no failures returns 200", r.status_code == 200, r.text)
    data = r.json()
    check("no failedChunks key when nothing failed", "failedChunks" not in data, str(data.keys()))
    check("result has the same top-level shape as /api/process-meeting", set(data.keys()) >= {"meeting", "speakers", "transcript", "notes"}, str(data.keys()))


# --- 5. unknown / foreign stream_id is rejected ---------------------------
def test_unknown_stream_rejected():
    r = requests.post(
        f"{BASE}/api/stream/chunk",
        files={"audio": ("chunk.wav", make_wav_bytes(), "audio/wav")},
        data={"stream_id": "not-a-real-id", "sequence_number": 0, "start_time": 0.0, "end_time": 10.0},
    )
    check("chunk upload to a nonexistent stream_id is rejected (404)", r.status_code == 404, f"{r.status_code}: {r.text}")


# --- 6. legacy batch endpoint is unaffected --------------------------------
def test_legacy_endpoint_still_works():
    with open("output/last_recording.wav", "rb") as f:
        r = requests.post(
            f"{BASE}/api/process-meeting",
            files={"audio": ("clip.wav", f, "audio/wav")},
            data={"title": "Legacy Path Check", "meeting_type": "General", "multilingual": "false"},
            timeout=60,
        )
    check("legacy /api/process-meeting still returns 200", r.status_code == 200, f"{r.status_code}: {r.text[:300]}")
    data = r.json()
    check("legacy endpoint's response still has the expected shape", set(data.keys()) >= {"meeting", "speakers", "transcript", "notes"}, str(data.keys()))


def main():
    print("=" * 70)
    print("PHASE B STREAMING -- unit tests (merge logic, no server needed)")
    print("=" * 70)
    test_merge_drops_duplicate_in_covered_overlap()
    test_merge_drops_duplicate_realistic_sentence_length()
    test_merge_keeps_uncovered_overlap_segment()
    test_merge_keeps_distinct_adjacent_segments_that_merely_touch()
    test_merge_keeps_straddling_segment_and_sorts_output()
    test_reassemble_audio_is_gapless_and_trims_measured_overlap()
    test_reassemble_audio_uses_real_overlap_not_fixed_constant()
    test_reassemble_audio_does_not_trim_across_a_real_gap()
    test_reassemble_audio_skips_failed_chunks_without_crashing()

    print("=" * 70)
    print("PHASE B STREAMING -- integration tests against a live server")
    print("=" * 70)
    test_out_of_order_chunks()
    test_duplicate_chunk_idempotent()
    test_failed_chunk_not_silently_dropped()
    test_finalize_all_success()
    test_unknown_stream_rejected()
    test_legacy_endpoint_still_works()

    print("=" * 70)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} failing checks")
        for f in FAILURES:
            print(f" - {f}")
        sys.exit(1)
    print("RESULT: 0 failing checks")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
