# Confero benchmark + capability probe (Phase A)

Isolated measurement tooling. Nothing here is imported by `api.py` or any
other production module, and running anything in this folder cannot change
application behavior.

No new dependency was added. Everything needed (RAM, process memory, CPU
time, GPU/driver info, Vulkan/DirectML/NPU presence) is reachable through
the Python standard library (`ctypes` on Windows, `/proc` on Linux) or by
shelling out to OS-shipped tools (`powershell`, `dxdiag`) that are not pip
packages. `faster-whisper`/`ctranslate2`/`numpy`/`onnxruntime` are already
Confero dependencies, nothing extra is required for them either.

## Capability probe

```
python backend/bench/capability_probe.py          # fast
python backend/bench/capability_probe.py --dxdiag  # also runs dxdiag (slower)
```

Prints a PRESENT / SUPPORTED-BY-THIS-ENGINE / NOT-TESTED summary and saves
a full JSON snapshot to `results/capability_<timestamp>.json`.

## Single benchmark run

```
python backend/bench/run_bench.py --clip output/last_recording.wav \
    --model small --device cpu --compute-type int8 --threads 8 --runs 3
```

Refuses (doesn't crash) if `--threads` exceeds logical core count, if
`--compute-type` isn't supported on `--device`, or if the requested model
is estimated to need more RAM than is currently available — pass `--force`
to override any of those once you've judged the risk acceptable.

## Comparison suite

```
python backend/bench/run_suite.py --clip output/last_recording.wav --runs 2
```

Runs a small, fixed `{tiny, small, medium} x {4, min(8, cores)} threads`
matrix sequentially (never two models loaded at once) and prints a
speedup table relative to `small @ 8 threads` (api.py's production
default). Skips any config the RAM safety check refuses.

## Output

- `results/bench_results.jsonl` — one JSON object per run, append-only.
- `results/transcripts/<run_id>.json` — the actual transcript segments
  that run produced, so a speed number is never reported without the text
  that produced it.
- `results/capability_<timestamp>.json` — hardware/software snapshots.

None of `results/` is committed (see `.gitignore`).

## Accuracy / ground truth

If a file `<clip-stem>.reference.txt` exists next to a clip, the bench
compares the run's transcript against it (word error rate, stdlib-only
Levenshtein, no `jiwer` dependency) and stores the WER breakdown in the
result's `accuracy` field. Without a reference file, `accuracy` is
literally the string `"NO GROUND TRUTH — SPEED ONLY"` — never a guess.

The WER implementation is O(n·m) over whitespace-tokenized words; fine for
short clips, but a 1-2 hour meeting's reference transcript (thousands of
words) would be slow in pure Python. Not an issue yet since no such
reference transcript exists in the repo (see the test-data inventory in
the audit report) — worth revisiting if/when one is added.

## Known gaps (carried over from the research audit)

The repo has exactly one real test clip
(`backend/output/last_recording.wav`, ~2 minutes, English, mostly
silence) and no ground-truth reference transcript for it yet. Benchmarks
run against it are real measurements, but they're a single, short,
sparse-speech data point — not representative of a dense 1-2 hour
multilingual meeting. Missing conditions, matching the audit's own list:

1. clean English · 2. Indian English · 3. Tamil · 4. Tanglish ·
5. English/Tamil switching · 6. quiet speech · 7. noisy room ·
8. cheap laptop mic · 9. Bluetooth mic · 10. multiple speakers ·
11. overlapping speech · 12. 1-2 hour recording

Add clips (plus, ideally, a `.reference.txt` next to each) under
`backend/output/` or point `--clip` at wherever they live.
