# Confero, AI Voice Intelligence for Meetings

Confero listens to a meeting, tells every speaker's voice apart, keeps an exact
record of what each person said, and turns that record into meeting notes and
a shareable report. It's built so the same pipeline can sit behind any
meeting tool (Zoom, Teams, Meet, or a plain room mic).

This repo is organized so each module of the system lives in its own place.
Right now, **Module 1 (Frontend / Product Shell)** is complete and is what's
inside `frontend/`. The AI modules (diarization, transcription, summarization,
report generation) are being built next, see `docs/module-breakdown.md` for
exact status, and `docs/architecture.md` for how they will plug into this UI.

## What's in this repo so far

```
ai-meeting-voice-system/
├── README.md                     ← you are here
├── docs/
│   ├── module-breakdown.md       ← full module list + completion status (for review)
│   ├── log-note-template.md      ← fill this in before you meet your mentor
│   └── architecture.md           ← how frontend + upcoming AI backend connect
└── frontend/                     ← Module 1: the product website (DONE)
    ├── package.json
    ├── vite.config.js
    ├── index.html
    └── src/
        ├── main.jsx
        ├── App.jsx
        ├── styles.css
        ├── data/mockMeeting.js   ← sample meeting data (stands in for real AI output)
        ├── components/
        │   ├── Navbar.jsx
        │   ├── SpeakerTrack.jsx  ← renders a diarized, speaker-labeled line
        │   └── PipelineStrip.jsx
        └── pages/
            ├── Home.jsx
            ├── RecordMeeting.jsx
            ├── Transcript.jsx
            ├── Notes.jsx
            └── Reports.jsx
```

## Running the website

You need [Node.js](https://nodejs.org) 18+ installed. Then, in VS Code's
terminal:

```bash
cd ai-meeting-voice-system/frontend
npm install
npm run dev
```

Open the URL it prints (usually `http://localhost:5173`). You'll get:

- **Home**, product pitch + the 5-stage pipeline (Capture → Diarize →
  Transcribe → Summarize → Report) + recent meetings.
- **Record Meeting**, the capture screen (mic/upload UI, live "on-air" state).
- **Transcript**, a diarized transcript: every line labeled by speaker,
  with the exact words and a timestamp. Speaker labels are click-to-rename,
  which is exactly the UX the real diarization model will feed once it's wired
  in (Module 3).
- **Meeting Notes**, the auto-generated summary, decisions, and action items
  that Module 4 (NLP summarization) will eventually produce for real.
- **Reports**, the exportable meeting report (client-side print/export for
  now; server-side PDF/DOCX generation is Module 6).

Everything currently on screen is driven by `src/data/mockMeeting.js`, a
realistic stand-in for what the AI backend will return once it's built. This
is intentional: it lets the whole product experience be demoed and reviewed
*before* the ML modules are finished, and it defines the exact data contract
those modules need to satisfy.

## Why this shape

For the mini-project review, "done" needs to be demonstrable, not just
described. A working, navigable product shell (with a real, thought-through
data model) is something a panel can click through, which is a stronger
signal of progress than a partially-working script. It also fixes the API
contract (`docs/architecture.md`) that every later module has to match,
which cuts down on rework in the following weeks.

## Roadmap toward a paper

Once the AI modules are complete, the same `mockMeeting.js` shape becomes the
real output format, which means the frontend needs no rework. For the
research-paper stage, the plan is to benchmark diarization accuracy (DER),
transcription accuracy (WER), and summarization quality (ROUGE / human eval)
against public meeting datasets (AMI Corpus, ICSI Meeting Corpus), and write
up the pipeline + evaluation as the paper's core contribution.
