"""
Confero, Meeting Processing Prototype (v1)

A standalone CLI script that proves out Modules 2-5 end to end:

    audio file -> speaker diarization -> transcription -> meeting notes

This does NOT touch the frontend. It's a separate, testable proof of the
real pipeline. Once it works reliably, the same logic gets wrapped in a
FastAPI endpoint (Module 7) that the "Record Meeting" page calls instead
of using mock data, that's the next step after this one.

Output JSON matches the exact shape the frontend already expects (see
../docs/architecture.md), so it can be dropped straight into
frontend/src/data/mockMeeting.js later with no frontend code changes.

Usage:
    python process_meeting.py path/to/audio.wav
    python process_meeting.py path/to/audio.wav --title "Team Standup"
    python process_meeting.py path/to/audio.wav --skip-diarization
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

HF_TOKEN = os.getenv("HF_TOKEN")
SPEAKER_COLORS = ["teal", "coral", "periwinkle", "moss", "amber"]


def run_transcription(audio_path: str):
    """Returns a list of (start, end, text) tuples from Whisper."""
    from faster_whisper import WhisperModel

    print("Loading transcription model (first run downloads the Whisper weights, a few hundred MB)...")
    model = WhisperModel("small", device="cpu", compute_type="int8")

    print("Transcribing (this can take a while on CPU)...")
    segments, _info = model.transcribe(audio_path, beam_size=5)

    return [(seg.start, seg.end, seg.text.strip()) for seg in segments]


def run_diarization(audio_path: str):
    """Returns a list of (start, end, raw_speaker_label) tuples."""
    from pyannote.audio import Pipeline

    if not HF_TOKEN:
        print(
            "\n[!] No HF_TOKEN found in backend/.env.\n"
            "    Speaker diarization needs a free Hugging Face token, and you must\n"
            "    also accept the terms on two model pages first. See README.md.\n"
            "    (Or re-run with --skip-diarization to test transcription only.)\n"
        )
        sys.exit(1)

    print("Loading diarization model (first run downloads ~500MB, be patient)...")
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=HF_TOKEN)

    print("Running diarization...")
    diarization = pipeline(audio_path)

    return [(turn.start, turn.end, speaker) for turn, _, speaker in diarization.itertracks(yield_label=True)]


def assign_speakers(transcript_segments, diarization_segments):
    """For each transcribed line, pick whichever diarized speaker overlaps
    it the most in time. This is the step that turns 'raw transcript' into
    'who said what.'"""

    def overlap(a_start, a_end, b_start, b_end):
        return max(0.0, min(a_end, b_end) - max(a_start, b_start))

    raw_to_friendly = {}
    result = []

    for start, end, text in transcript_segments:
        if not text:
            continue

        best_speaker, best_overlap = None, 0.0
        for d_start, d_end, d_speaker in diarization_segments:
            ov = overlap(start, end, d_start, d_end)
            if ov > best_overlap:
                best_overlap, best_speaker = ov, d_speaker

        if best_speaker is None:
            best_speaker = "SPEAKER_00"

        if best_speaker not in raw_to_friendly:
            raw_to_friendly[best_speaker] = f"S{len(raw_to_friendly) + 1}"

        result.append(
            {
                "id": f"seg_{len(result) + 1:03d}",
                "speakerId": raw_to_friendly[best_speaker],
                "start": round(start, 1),
                "end": round(end, 1),
                "text": text,
            }
        )

    speakers = [
        {
            "id": friendly,
            "label": f"Speaker {friendly[1:]}",
            "color": SPEAKER_COLORS[i % len(SPEAKER_COLORS)],
        }
        for i, friendly in enumerate(raw_to_friendly.values())
    ]
    return result, speakers


def main():
    parser = argparse.ArgumentParser(description="Turn a meeting recording into a labeled transcript + notes.")
    parser.add_argument("audio", help="Path to an audio file (wav/mp3/m4a/etc)")
    parser.add_argument("--title", default="Untitled Meeting")
    parser.add_argument("--date", default=str(date.today()))
    parser.add_argument("--output", default=None, help="Output path (default: output/<filename>.json)")
    parser.add_argument(
        "--skip-diarization",
        action="store_true",
        help="Treat the whole recording as one speaker. Useful for a quick test before setting up HF_TOKEN.",
    )
    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"File not found: {audio_path}")
        sys.exit(1)

    transcript_raw = run_transcription(str(audio_path))

    if args.skip_diarization:
        print("(Skipping diarization, everything will be labeled Speaker 1.)")
        diarization_raw = [(0.0, 10**7, "SPEAKER_00")]
    else:
        diarization_raw = run_diarization(str(audio_path))

    transcript, speakers = assign_speakers(transcript_raw, diarization_raw)

    print("Generating meeting notes...")
    from notes_generator import generate_notes

    notes = generate_notes(transcript, speakers)

    duration = max((seg["end"] for seg in transcript), default=0)

    output = {
        "meeting": {
            "id": audio_path.stem,
            "title": args.title,
            "date": args.date,
            "durationSeconds": round(duration),
            "speakerCount": len(speakers),
        },
        "speakers": speakers,
        "transcript": transcript,
        "notes": notes,
    }

    out_path = Path(args.output) if args.output else Path(__file__).parent / "output" / f"{audio_path.stem}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))

    print(f"\nDone, {len(speakers)} speaker(s), {len(transcript)} transcript line(s).")
    print(f"Saved to: {out_path}")
    print("\nOpen that file and compare its shape to frontend/src/data/mockMeeting.js,")
    print("they match. That's what makes wiring this into the UI a small step later.")


if __name__ == "__main__":
    main()