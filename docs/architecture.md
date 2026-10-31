# System Architecture

## Meeting-tool support

"Audio in" (above) isn't limited to a direct mic recording. Google Meet is
supported two ways today: (a) live, by capturing a Meet tab's audio via the
browser's own tab-share picker ("Capture a tab" on the Record page, see
`frontend/src/context/RecordingContext.jsx::startTabCapture`) and feeding
it through the same streaming pipeline as a mic recording; (b) after the
fact, by uploading the `.mp4` Meet already saves when the host records a
call, through the same upload flow as any other file (the backend's audio
decoder already pulls the audio track out of a video container, no
Meet-specific code needed). Zoom/Teams work the same way via (b) today;
(a) only works for whichever tab/window the browser lets you share audio
from.

## Pipeline

```
  Audio in            Module 2           Module 3            Module 4             Module 5              Module 6
(mic / upload)  →  Capture &      →   Speaker        →    Speech-to-Text   →   Notes Generation   →   Report
                    Preprocessing      Diarization          (Whisper)           (summary/action        (PDF/DOCX)
                    (resample,         (pyannote.audio,      per diarized        items via LLM/          
                    denoise,           "who spoke           segment)            transformer)
                    chunk)             when")
                                                                                                       
                                              ↓ all of the above are exposed to the frontend as JSON, via:
                                              
                                        Module 7, Backend API (FastAPI) + Storage (SQLite/Postgres)
                                              
                                              ↓
                                        
                                        Module 1, Frontend (this repo's `frontend/`)
```

## Data contract

This is the shape the frontend already expects (see
`frontend/src/data/mockMeeting.js`). Every backend module should produce data
that fits this shape, so the frontend can swap mock data for real API
responses with no rewrite.

```json
{
  "meeting": {
    "id": "meet_001",
    "title": "Q3 Product Launch Sync",
    "date": "2026-08-18",
    "durationSeconds": 1872,
    "speakerCount": 4
  },
  "speakers": [
    { "id": "S1", "label": "Speaker 1", "color": "teal", "labelSource": "default" }
  ],
  "transcript": [
    {
      "id": "seg_001",
      "speakerId": "S1",
      "startSeconds": 4,
      "endSeconds": 11,
      "text": "Let's finalize the Q3 launch date before we lose the week."
    }
  ],
  "notes": {
    "summary": "Team aligned on shipping in week 3 of Q3...",
    "decisions": ["Ship date locked to Aug 18", "Marketing owns the teaser"],
    "actionItems": [
      { "owner": "Priya", "task": "Finalize press kit", "due": "2026-08-10" }
    ],
    "topics": ["Launch timeline", "Marketing plan", "Support readiness"]
  }
}
```

`labelSource` is one of `"default"` (still "Speaker N"), `"detected"` (a
self-introduction like "I'm Adrian" was found automatically, see
`backend/name_detection.py`), or `"manual"` (the user corrected/renamed it
via `PATCH /api/meetings/{id}/speakers/{speakerId}`, which always wins and
is never overwritten by detection again).

## Planned backend stack

- **API:** FastAPI (Python), matches the ML libraries (pyannote, Whisper)
  and keeps modules 2–6 in one language.
- **Diarization:** `pyannote.audio` pretrained pipeline.
- **Transcription:** `faster-whisper` (Whisper model, runs locally, no API
  cost, important since this needs to run "anywhere").
- **Summarization:** transformer summarization model or local LLM prompt
  over the diarized transcript, constrained to return the JSON shape above.
- **Report generation:** `reportlab` or `python-docx` to render the notes +
  transcript into a shareable file.
- **Storage:** SQLite for the project stage; swappable for Postgres later.

## Why this split

Each module can be developed and unit-tested independently (e.g., diarization
accuracy can be measured against the AMI Corpus without the frontend or
Whisper being involved at all), then wired together behind the API. That
independence is also what next stage's evaluation section needs: each module
gets its own metric (DER for diarization, WER for transcription, ROUGE/human
eval for notes).
