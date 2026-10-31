# Module Breakdown, Confero (AI Voice Meeting Intelligence)

Use this page directly when the panel asks "what modules, how many done, what's
pending." Status is honest as of this review, don't inflate it.

| # | Module | What it does | Status |
|---|--------|--------------|--------|
| 1 | **Frontend / Product Shell** | The website: record screen, diarized transcript view, notes view, report view, navigation. Built against a fixed mock data contract. | ✅ **Done** |
| 2 | **Audio Capture & Preprocessing** | Record from mic / accept uploaded audio (wav/mp3), resample, noise reduction, chunking for streaming. | 🔲 Pending |
| 3 | **Speaker Diarization** | "Who spoke when", cluster audio into speaker segments and assign consistent labels (Speaker 1, 2, 3…) across the whole meeting. Planned: `pyannote.audio`. | 🔲 Pending |
| 4 | **Speech-to-Text (Transcription)** | Convert each diarized audio segment into exact text. Planned: OpenAI Whisper (local, `whisper` / `faster-whisper`). | 🔲 Pending |
| 5 | **NLP Meeting-Notes Generation** | Turn the raw speaker-labeled transcript into a summary, key decisions, and action items (owner + due date extraction). Planned: transformer summarization model / LLM prompting over the transcript. | 🔲 Pending |
| 6 | **Report Generation** | Turn notes + transcript into a shareable PDF/DOCX report with meeting metadata. | 🔲 Pending |
| 7 | **Backend API & Storage** | Ties modules 2–6 together behind an API the frontend calls; stores meetings/transcripts (DB). | 🔲 Pending |
| 8 | **Deployment / "usable anywhere"** | Package so it can run against any meeting source (uploaded recording first, live capture later); host frontend + API. | 🔲 Pending |
| 9 | **Evaluation & Research Write-up** | Benchmark diarization (DER), transcription (WER) and summarization quality on a public meeting dataset (AMI/ICSI); write up as a paper. | 🔲 Pending, planned for after modules 2–7 |

**Completed this cycle:** Module 1 (Frontend/Product Shell), fully navigable,
defines the data contract every other module will produce/consume.

**In progress / next up:** Module 2 (Audio Capture) and Module 3 (Diarization),
these are next because every later module depends on their output shape.

**Why frontend first:** the UI encodes exactly what data each AI module must
return (see `architecture.md`). Building it first turned "figure out the
format as we go" into a fixed target, so modules 2–6 can be built and tested
independently against it.

## One-line answers for the panel

- *"How many modules complete?"* → 1 out of 9 fully done (Frontend), with the
  data contract for the remaining 8 already specified.
- *"What's in progress?"* → Audio capture + diarization research/prototyping.
- *"What's pending?"* → Transcription, summarization, report generation,
  backend/API, deployment, and the evaluation/paper write-up.
