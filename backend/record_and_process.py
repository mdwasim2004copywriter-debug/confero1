"""
Confero, Step 1: record your own voice, transcribe it with timestamps,
generate meeting notes.

This is deliberately the SIMPLEST real slice of the pipeline that still
does something genuine:

    your microphone -> real recording -> real transcription (Whisper,
    with timestamps) -> real meeting notes

Single speaker only ("You" / Speaker 1 for everything), no diarization,
which means no Hugging Face token and no gated-model approval needed.
That harder multi-speaker layer already exists in process_meeting.py for
when you're ready for it; this script is the easy on-ramp before that.

Usage:
    python record_and_process.py
    python record_and_process.py --seconds 60
    python record_and_process.py --title "My test notes"
    python record_and_process.py --audio-file existing_clip.wav   # skip recording, use a file instead
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from dotenv import load_dotenv

load_dotenv()

SAMPLE_RATE = 16000  # what Whisper expects


def record_audio(max_seconds: int) -> np.ndarray:
    print(f"\nRecording will run for up to {max_seconds}s, press Ctrl+C to stop early.")
    print("Speak now...\n")

    chunks = []

    def callback(indata, frames, time_info, status):
        chunks.append(indata.copy())

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, callback=callback):
            sd.sleep(max_seconds * 1000)
    except KeyboardInterrupt:
        print("\nStopped early.")

    if not chunks:
        print("No audio captured. Check that a microphone is connected and your OS is allowing mic access.")
        sys.exit(1)

    audio = np.concatenate(chunks, axis=0).flatten()
    print(f"Captured {len(audio) / SAMPLE_RATE:.1f} seconds of audio.")
    return audio


def transcribe(audio_path: str):
    from faster_whisper import WhisperModel

    print("Loading transcription model (first run downloads the Whisper weights, a few hundred MB)...")
    model = WhisperModel("small", device="cpu", compute_type="int8")

    print("Transcribing...")
    segments, _info = model.transcribe(audio_path, beam_size=5)

    return [
        {"start": round(seg.start, 1), "end": round(seg.end, 1), "text": seg.text.strip()}
        for seg in segments
        if seg.text.strip()
    ]


def format_timestamp(total_seconds: float) -> str:
    m = int(total_seconds // 60)
    s = int(total_seconds % 60)
    return f"{m:02d}:{s:02d}"


def main():
    parser = argparse.ArgumentParser(description="Record your voice, transcribe with timestamps, generate notes.")
    parser.add_argument("--seconds", type=int, default=120, help="Max recording length in seconds (default 120)")
    parser.add_argument("--title", default="Solo Recording")
    parser.add_argument("--audio-file", default=None, help="Skip recording, transcribe an existing file instead")
    parser.add_argument("--output", default=None, help="Where to save the result JSON")
    args = parser.parse_args()

    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)

    if args.audio_file:
        audio_path = args.audio_file
        if not Path(audio_path).exists():
            print(f"File not found: {audio_path}")
            sys.exit(1)
    else:
        audio = record_audio(args.seconds)
        audio_path = str(output_dir / "last_recording.wav")
        sf.write(audio_path, audio, SAMPLE_RATE)
        print(f"Saved raw recording to: {audio_path}")

    segments = transcribe(audio_path)

    if not segments:
        print("\nWhisper didn't detect any speech in that recording.")
        print("Try again, speak closer to the mic, or check the mic isn't muted.")
        sys.exit(1)

    transcript = [
        {"id": f"seg_{i + 1:03d}", "speakerId": "S1", "start": seg["start"], "end": seg["end"], "text": seg["text"]}
        for i, seg in enumerate(segments)
    ]
    speakers = [{"id": "S1", "label": "You", "color": "teal"}]

    print("\n--- Transcript (with timestamps) ---")
    for seg in transcript:
        print(f"[{format_timestamp(seg['start'])}\u2013{format_timestamp(seg['end'])}]  {seg['text']}")

    print("\nGenerating meeting notes...")
    from notes_generator import generate_notes

    notes = generate_notes(transcript, speakers)

    print("\n--- Notes ---")
    print("Summary:", notes["summary"])
    print("Decisions:", notes["decisions"] or "(none detected)")
    print("Action items:")
    if notes["actionItems"]:
        for item in notes["actionItems"]:
            print(f"  - {item['task']}")
    else:
        print("  (none detected)")
    print("Topics:", ", ".join(notes["topics"]) or "(none detected)")

    duration = max((seg["end"] for seg in transcript), default=0)
    output = {
        "meeting": {
            "id": "solo_" + date.today().isoformat(),
            "title": args.title,
            "date": str(date.today()),
            "durationSeconds": round(duration),
            "speakerCount": 1,
        },
        "speakers": speakers,
        "transcript": transcript,
        "notes": notes,
    }

    out_path = Path(args.output) if args.output else output_dir / f"solo_{date.today().isoformat()}.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nSaved full result to: {out_path}")


if __name__ == "__main__":
    main()