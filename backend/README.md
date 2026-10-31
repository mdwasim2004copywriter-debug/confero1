# Backend, v1 Pipeline Prototype

This proves out the real pipeline (Modules 2–5): take an audio file, figure
out who spoke when, transcribe exactly what they said, and generate meeting
notes. It's a command-line script, not yet wired into the website, that's
the deliberate next step, once this works reliably on your machine.

## Start here: `record_and_process.py` (single speaker, easiest)

Before dealing with multi-speaker diarization below, try the simpler script
first, it actually records from your microphone, transcribes it with
timestamps, and generates notes, all in one go. **No Hugging Face token
needed** (single speaker means there's nothing to tell apart).

```bash
cd ai-meeting-voice-system/backend
python -m venv venv
venv\Scripts\activate            # Windows, Mac/Linux: source venv/bin/activate
pip install -r requirements-solo.txt
python record_and_process.py
```

It'll record for up to 2 minutes (Ctrl+C to stop early), print your
transcript with timestamps straight to the terminal, then print a
summary/decisions/action items below it, and save the full result to
`output/solo_<date>.json`.

Everything below this section (`process_meeting.py`, `requirements.txt`) is
the **next step up**, multi-speaker diarization, which needs the Hugging
Face token setup. Come back to it once the solo version works.

**No dataset required.** This uses *pretrained* models (already trained by
their creators) to do inference, not training. You'd only need a dataset if
you were training a diarization/transcription model from scratch, nobody
does that for a project like this; it needs huge amounts of labeled audio
and serious compute. Datasets (AMI Corpus, ICSI Meeting Corpus) come back
later, for the research paper's evaluation section, see the bottom of this
file.

## Honesty check on this code

I wrote and syntax-checked this script, and unit-tested the speaker-matching
and notes-generation logic with fake data (both pass, see the logic is
straightforward Python, no ML involved in those two parts). What I could
**not** do is run the actual diarization/transcription models end-to-end,
because they download multi-hundred-MB weights from Hugging Face, and my
sandbox can't reach that domain. So: the plumbing is verified, the "call
into pyannote/Whisper" parts are written correctly against their documented
APIs but untested by me. If you hit an error there, paste it back to me,
that's normal for a first real run, not a sign anything is fundamentally
wrong.

## Setup

### 1. Install ffmpeg (not a Python package)
- Windows: `winget install ffmpeg` (or download from ffmpeg.org and add to PATH)
- Mac: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

Confirm it worked: run `ffmpeg -version` in a terminal, it should print a version, not "command not found."

### 2. Create a virtual environment
```bash
cd ai-meeting-voice-system/backend
python -m venv venv
```
Activate it:
- Windows: `venv\Scripts\activate`
- Mac/Linux: `source venv/bin/activate`

### 3. Install dependencies
```bash
pip install -r requirements.txt
```
This is a big install (pulls in PyTorch), can take several minutes and a few GB of disk.

### 4. Get a Hugging Face token (for diarization)
1. Create a free account at [huggingface.co](https://huggingface.co).
2. Go to **Settings → Access Tokens** → create a new token (read access is enough).
3. **Important, easy to miss:** visit these two pages while logged in and click "Agree and access repository" on each, the diarization model is gated and won't load without this:
   - [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
   - [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)

### 5. Set up your `.env`
Copy `.env.example` to a new file named `.env` in this same folder, and paste in your token:
```
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx
```
`ANTHROPIC_API_KEY` is optional, leave it blank to use the free built-in notes generator.

### 6. Get a test audio file
Either:
- Record 1–2 minutes of two or more people talking (phone voice memo app, Windows Voice Recorder, Zoom recording, Audacity, anything that exports wav/mp3/m4a), or
- Grab a short public sample from the [AMI Meeting Corpus](https://groups.inf.ed.ac.uk/ami/download/), real recorded meetings, free to download, made for exactly this kind of testing.

### 7. Run it
```bash
python process_meeting.py path/to/your_audio.wav --title "My Test Meeting"
```

First run will be slow, it's downloading the Whisper and pyannote model weights (one-time). Later runs are much faster.

If you just want to sanity-check transcription without dealing with the Hugging Face token yet:
```bash
python process_meeting.py path/to/your_audio.wav --skip-diarization
```

### 8. Check the output
Look in `backend/output/<your_file>.json`. Compare its shape to
`frontend/src/data/mockMeeting.js`, they match on purpose. That's what
makes wiring this into the actual website (instead of mock data) a small
step rather than a rewrite.

## What's next after this works

1. Wrap this same logic in a FastAPI endpoint (Module 7) instead of a CLI script.
2. Have the frontend's "Record Meeting" page upload real audio to that endpoint instead of just showing a mock timer.
3. Replace `mockMeeting.js` with the real API response.

## For the research paper stage later

Once this pipeline is stable, benchmark it against public meeting datasets:
- **AMI Corpus** and **ICSI Meeting Corpus**, real annotated meetings, standard for this kind of evaluation.
- Diarization accuracy → **DER** (Diarization Error Rate).
- Transcription accuracy → **WER** (Word Error Rate).
- Notes quality → **ROUGE score** against human-written reference summaries, plus human evaluation.

That evaluation, not this code, is what actually becomes the paper's results section.
