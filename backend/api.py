"""
Confero, Local API server (Module 7)

Wraps the transcription + speaker identification + notes pipeline behind
an HTTP endpoint the website can call directly, instead of you running a
CLI script by hand.

Run:
    uvicorn api:app --reload --port 8000

The frontend calls http://localhost:8000/api/process-meeting with an
uploaded audio recording and gets back JSON in the same shape as
frontend/src/data/mockMeeting.js, that's what lets the Transcript/Notes/
Reports pages show it with no layout changes, whether it's 1 speaker or 4.

Speaker separation uses resemblyzer + clustering (speaker_id.py), no
Hugging Face account, token, or gated model needed. It works for any
unknown speakers (not just a pre-enrolled group), by grouping similar-
sounding voices together. It identifies who's speaking during normal
turn-taking; it cannot separate two people talking at the exact same
time (that's a different, much harder problem).
"""

import os

# Must be set before huggingface_hub is imported (directly or via
# faster_whisper) anywhere below. Its default download cache tries to
# symlink files on disk, which Windows blocks without Developer Mode or
# admin rights, causing a hard crash the first time a new model (e.g.
# large-v3, downloaded on first mixed-language request) needs fetching.
# This makes it copy files instead, slightly more disk use, no crash.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

import json
import re
import shutil
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import auth
import name_detection
import storage
import streaming
from notes_generator import generate_notes
from speaker_id import cluster_speakers, load_audio

load_dotenv()
storage.init_db()

app = FastAPI(title="Confero API")

# Accepts requests from any localhost port (Vite sometimes picks 5174,
# 5175, etc. if 5173 is taken), any Vercel deployment (production and
# preview subdomains both match *.vercel.app), any Cloudflare "quick
# tunnel" (trycloudflare.com, used for one-off mobile testing straight from
# a local dev machine, no account/signup needed), and, if the frontend is
# hosted on a custom domain, whatever FRONTEND_ORIGIN is set to in the
# environment. Local dev is unaffected either way.
_frontend_origin = os.getenv("FRONTEND_ORIGIN")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+|https://.*\.vercel\.app|https://.*\.trycloudflare\.com",
    allow_origins=[_frontend_origin] if _frontend_origin else [],
    allow_methods=["*"],
    allow_headers=["*"],
)

_whisper_model = None
_live_whisper_model = None
_medium_whisper_model = None

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.on_event("startup")
def _prewarm_models():
    """Loads the small model synchronously (cheap, ~9s, every recording
    needs it anyway) so it's ready the instant the app opens. The
    mixed-language model is much larger (large-v3 takes ~83s to load) --
    loading that synchronously here would make every `--reload` restart
    during development painfully slow, so it warms up in a background
    thread instead. The server becomes usable immediately either way; a
    mixed-language request that arrives before that background load
    finishes still works, it just falls back to the normal lazy-load (the
    same wait as before this change), it only helps once the background
    load has actually completed."""
    import threading

    get_whisper_model()
    threading.Thread(target=get_medium_whisper_model, daemon=True).start()

    # Constructing the voice encoder object itself is fast (~0.1-0.2s), but
    # that's not the real cost -- measured directly: the *first* actual
    # embed_utterance() call pays a ~14s one-time PyTorch backend warmup
    # (thread pool / kernel init) that has nothing to do with model
    # loading, every call after that takes ~0.2-0.4s. That hidden first-
    # call cost is exactly the "fixed ~10s" speaker-clustering overhead
    # seen on every recording regardless of length -- merely constructing
    # the encoder (the previous version of this prewarm) never triggered
    # it. Running one throwaway embed_utterance() here pays that cost
    # once at startup instead of during someone's first real meeting.
    import speaker_id

    def _prewarm_speaker_encoder():
        encoder = speaker_id.get_encoder()
        try:
            encoder.embed_utterance(np.zeros(16000, dtype=np.float32))
        except Exception:
            pass

    threading.Thread(target=_prewarm_speaker_encoder, daemon=True).start()

    # Opens (and connection-pools) the Groq client ahead of time and fires
    # one tiny throwaway request, so the DNS lookup + TLS handshake for the
    # *first* real transcription isn't paid inline during someone's actual
    # meeting. Best-effort only: the OS/TLS layer may still drop the idle
    # connection before the first real request arrives, so this doesn't
    # guarantee a warm connection, just improves the odds. Skipped
    # entirely if no key is configured.
    groq_api_key = os.getenv("GROQ_API_KEY")
    if groq_api_key:
        threading.Thread(target=_prewarm_groq_client, args=(groq_api_key,), daemon=True).start()

# 8-16 characters, at least one lowercase, one uppercase, one digit,
# checked here in Python (not one giant regex) so the error message can
# say exactly which rule failed instead of just "invalid password".


def _password_errors(password: str) -> list[str]:
    errors = []
    if not (8 <= len(password) <= 16):
        errors.append("be 8-16 characters")
    if not re.search(r"[a-z]", password):
        errors.append("include a lowercase letter")
    if not re.search(r"[A-Z]", password):
        errors.append("include an uppercase letter")
    if not re.search(r"[0-9]", password):
        errors.append("include a number")
    return errors


class SignupRequest(BaseModel):
    email: str
    password: str
    captcha_answer: int
    captcha_token: str


class LoginRequest(BaseModel):
    email: str
    password: str


class RenameSpeakerRequest(BaseModel):
    label: str


# Measured directly on this machine: explicit cpu_threads cut decode time
# by ~18% vs CTranslate2's own default thread count (28.2s -> 23.0s on a
# 66s clip), same model, same beam search, purely a parallelism setting,
# not an accuracy tradeoff. os.cpu_count() falls back to 4 if it can't
# detect the core count for some reason.
_CPU_THREADS = os.cpu_count() or 4

# GPU support, additive and self-disabling: on a machine with a working
# NVIDIA/CUDA setup, this lets Whisper run dramatically faster than CPU;
# on every other machine (no GPU, an AMD/Intel GPU, missing/mismatched
# drivers -- this dev machine is exactly that case, confirmed via a real
# CUDA init failure caught during benchmarking) it's a no-op and every
# model loads exactly as before, CPU/int8, same code path, same output.
# ctranslate2.get_cuda_device_count() is used rather than
# torch.cuda.is_available() deliberately: torch here is pinned to the
# +cpu build (see requirements.txt) so torch's own CUDA check would
# always say "no" even on a real GPU machine, but ctranslate2 (the engine
# faster-whisper actually runs on) ships CUDA support independently of
# that torch build and detects the GPU correctly on its own.
try:
    import ctranslate2 as _ctranslate2

    _GPU_AVAILABLE = _ctranslate2.get_cuda_device_count() > 0
except Exception:
    _GPU_AVAILABLE = False


def _load_whisper_model(model_size: str, startup_message: str):
    """Shared loader for all three Whisper model tiers below. Tries GPU
    first only when one was actually detected; if construction on GPU
    still fails for any reason (driver mismatch, out of VRAM, etc.) this
    falls back to the exact CPU/int8 config this app has always used,
    so no machine can end up worse off or crashed than before this
    existed -- same fallback-tier philosophy already used everywhere
    else in this codebase (Groq -> local model, Gemini -> Groq -> Claude
    -> local notes engine)."""
    from faster_whisper import WhisperModel

    print(startup_message)
    if _GPU_AVAILABLE:
        try:
            model = WhisperModel(model_size, device="cuda", compute_type="float16")
            print(f"[info] '{model_size}' Whisper model running on GPU (CUDA).")
            return model
        except Exception as e:
            print(f"[info] GPU init failed for '{model_size}' model ({e}), using CPU instead.")
    return WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=_CPU_THREADS)


def get_whisper_model():
    """Loads the Whisper model once and reuses it across requests,
    loading it fresh every time would make each recording painfully slow."""
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = _load_whisper_model("small", "Loading Whisper model (first request only, be patient)...")
    return _whisper_model


def get_medium_whisper_model():
    """Bigger model, more real capacity for phonemes/vocabulary it saw less
    of in training, used only for recordings flagged "mixed language" (the
    same checkbox used for Tamil/Tanglish), since that's also exactly the
    condition where under-represented-language speech is hardest for a
    smaller model. Briefly tried "large-v3" here instead, measured
    (real test) at 83s to load vs. medium's 11s, from cache, both already
    downloaded, that's a disproportionate one-time tax for the short test
    clips this actually gets used with, with no verified accuracy benefit
    to show for it (never tested against real Tamil audio either way), so
    reverted back to "medium" rather than keep an unproven, expensive
    upgrade. English-only recordings are untouched either way, still on
    "small"."""
    global _medium_whisper_model
    if _medium_whisper_model is None:
        _medium_whisper_model = _load_whisper_model(
            "medium", "Loading medium Whisper model (first mixed-language request only)..."
        )
    return _medium_whisper_model


def get_live_whisper_model():
    """Small/fast model for the live-caption endpoint, separate from the
    main "small" model so near-real-time chunks during recording don't pay
    for the larger model's accuracy; the final transcript (used for notes
    and the saved report) still goes through get_whisper_model as before."""
    global _live_whisper_model
    if _live_whisper_model is None:
        _live_whisper_model = _load_whisper_model("tiny", "Loading tiny live-caption Whisper model...")
    return _live_whisper_model


@app.post("/api/live-transcribe")
def live_transcribe(audio: UploadFile = File(...), multilingual: str = "false"):
    """Transcribes one short in-progress audio chunk for live captions
    while recording. Text only, best-effort, no speaker labels (that
    clustering needs the full recording) and nothing is saved here; the
    accurate transcript/notes/speakers still come from /api/process-meeting
    once recording stops. No login required, this never touches storage."""
    suffix = Path(audio.filename or "chunk.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(audio.file, tmp)
        tmp_path = tmp.name

    try:
        wav = load_audio(tmp_path)
        if len(wav) < 16000 * 0.3:  # shorter than ~0.3s isn't worth transcribing
            return {"text": ""}

        # Stays on the fast "tiny" model for English. But "tiny" is too weak
        # for underrepresented languages like Tamil -- its output is
        # low-confidence enough that the hallucination filter below
        # discards it entirely, so the live preview showed nothing at all
        # for Tamil/Tanglish speech even though the final
        # /api/process-meeting pass (which uses "medium") produced real
        # text. Tried swapping in "medium" here too, but measured (real
        # test, this machine): medium takes ~20s to transcribe a 5s chunk,
        # 4x slower than real-time, so captions permanently fall behind and
        # never catch up during a live recording -- worse than showing
        # nothing. "small" measured at ~6s for a 5s chunk, close enough to
        # real-time to stay usable, and confident enough on Tamil to survive
        # the hallucination filter below, unlike "tiny". Reuses the same
        # already-loaded "small" model as the non-multilingual final pass
        # (get_whisper_model), no extra model to keep in memory.
        is_multilingual = multilingual.lower() in ("true", "1", "yes")
        model = get_whisper_model() if is_multilingual else get_live_whisper_model()
        segments, _info = model.transcribe(wav, beam_size=1, multilingual=is_multilingual)

        parts = []
        for seg in segments:
            # Same AND-not-OR reasoning as /api/process-meeting -- only
            # reject a segment when both confidence signals agree it's
            # likely hallucinated, not just one, so accented speech that
            # trips only one signal isn't silently dropped from the preview.
            if seg.no_speech_prob >= 0.6 and seg.avg_logprob <= -1.0:
                continue  # likely hallucinated from silence/noise
            text = seg.text.strip()
            if text:
                parts.append(text)
        return {"text": " ".join(parts)}
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.get("/api/health")
def health():
    """Quick check the server is actually running: visit
    http://localhost:8000/api/health in a browser, should return {"status":"ok"}."""
    return {"status": "ok"}


@app.get("/api/auth/captcha")
def get_captcha():
    """A simple math question, the frontend shows it, the answer travels
    inside a signed token (so the server doesn't need to remember which
    question it asked), and signup checks the two match."""
    import random

    a, b = random.randint(1, 9), random.randint(1, 9)
    token = auth.create_captcha_token(a + b)
    return {"question": f"What is {a} + {b}?", "token": token}


@app.post("/api/auth/signup")
def signup(body: SignupRequest):
    if not auth.verify_captcha(body.captcha_answer, body.captcha_token):
        raise HTTPException(status_code=400, detail="That answer wasn't right, try the new question below")

    email = body.email.strip()
    if not _EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address")

    password_problems = _password_errors(body.password)
    if password_problems:
        raise HTTPException(status_code=400, detail="Password must " + ", ".join(password_problems))

    if storage.get_user_by_email(email):
        raise HTTPException(status_code=400, detail="An account with this email already exists, try logging in instead")

    password_hash = auth.hash_password(body.password)
    user_id = storage.create_user(email, password_hash)
    token = auth.create_access_token(user_id, email)
    return {"token": token, "email": email}


@app.post("/api/auth/login")
def login(body: LoginRequest):
    email = body.email.strip()
    user = storage.get_user_by_email(email)
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        # Deliberately the same error for "no such user" and "wrong password",
        # telling them apart would let someone probe which emails have accounts.
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    token = auth.create_access_token(user["id"], user["email"])
    return {"token": token, "email": user["email"]}


@app.get("/api/meetings")
def get_meetings(user: dict = Depends(auth.get_current_user)):
    """Lightweight list for the Home page's 'your meetings', newest
    first, and only this logged-in user's own meetings."""
    return storage.list_meetings(user["id"])


@app.get("/api/meetings/metrics")
def get_meetings_metrics(user: dict = Depends(auth.get_current_user)):
    """Per-recording processing metrics (category, media type, file size,
    total processing time) for every meeting this user has, oldest first
    -- what the Dashboard page's charts are built from. Declared before
    the /api/meetings/{meeting_id} route below so "metrics" is never
    swallowed as a meeting_id path parameter."""
    return storage.list_meeting_metrics(user["id"])


@app.get("/api/meetings/{meeting_id}")
def get_meeting_detail(meeting_id: str, user: dict = Depends(auth.get_current_user)):
    """Full detail for one saved meeting, 404s if it doesn't exist OR if
    it belongs to someone else, so there's no way to view another user's
    meeting even by passing their exact meeting id."""
    result = storage.get_meeting(meeting_id, user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return result


@app.delete("/api/meetings/{meeting_id}")
def delete_meeting(meeting_id: str, user: dict = Depends(auth.get_current_user)):
    """Deletes one of this user's saved meetings. 404s if it doesn't exist
    or belongs to someone else, same not-found response either way, so
    there's no way to probe for other users' meeting ids."""
    deleted = storage.delete_meeting(meeting_id, user["id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return {"ok": True}


@app.patch("/api/meetings/{meeting_id}/speakers/{speaker_id}")
def rename_speaker(
    meeting_id: str, speaker_id: str, body: RenameSpeakerRequest, user: dict = Depends(auth.get_current_user)
):
    """Renames one speaker across an entire saved meeting (every line from
    them, plus the speakers list), and marks it labelSource: "manual" so
    automatic detection never overwrites it again. Same ownership check as
    get_meeting_detail/delete_meeting -- 404s whether the meeting doesn't
    exist or belongs to someone else."""
    result = storage.get_meeting(meeting_id, user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    label = body.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="Label cannot be empty")

    found = False
    for sp in result["speakers"]:
        if sp["id"] == speaker_id:
            sp["label"] = label
            sp["labelSource"] = "manual"
            found = True
    if not found:
        raise HTTPException(status_code=404, detail="Speaker not found")

    storage.save_meeting(result, user["id"])
    return {"speakers": result["speakers"]}


# Whisper (including Groq's hosted whisper-large-v3) decodes long audio in
# ~30s internal windows and, by default, conditions each window on the
# previous window's decoded text -- not something exposed as a toggle on
# Groq's API. Observed directly: a rough patch of audio (pause, cross-talk,
# unclear phrase) can make one window decode wrong, and because that wrong
# text then gets fed forward as context, the error compounds through every
# window after it for the rest of that single request -- an abrupt,
# non-recovering collapse partway through an otherwise-good recording, not
# a one-off garbled segment. Keeping each Groq request short bounds the
# blast radius: a bad patch can still corrupt its own chunk, but the next
# chunk is a fresh request with no poisoned prior-text context, so it isn't
# dragged down too. 60s (two Whisper windows per request) is short enough
# to contain this while staying well under Groq's free-tier 20 req/min cap
# for any recording in the range this app is actually used for; a FLAC
# chunk this short is also nowhere near the 25MB-per-request upload cap.
_GROQ_CHUNK_SECONDS = 60
_GROQ_NO_SPEECH_THRESHOLD = 0.6
_GROQ_LOW_CONFIDENCE_THRESHOLD = -1.0
# Looser than the two thresholds above on purpose -- those two together
# decide whether to reject a segment outright as likely hallucinated
# silence/noise. These decide whether to merely *flag* a kept segment as
# worth a second look in the UI. A real transcription failure (like a
# whole sentence coming out as fabricated nonsense, observed directly
# during testing) doesn't always trip the stricter AND-based reject
# threshold -- Whisper can be quite "confident" about wrong text -- but
# tends to at least look shakier on one of these two signals than a
# clean, correct segment does.
_GROQ_FLAG_NO_SPEECH_THRESHOLD = 0.3
_GROQ_FLAG_LOW_CONFIDENCE_THRESHOLD = -0.5
# Groq's free tier caps at 20 requests/minute. At 60s/chunk, a recording
# under ~15-18 minutes stays under that even if every chunk fires at once;
# a longer one may occasionally hit the rate limit, which _transcribe_with_
# groq already turns into a full fallback to the local model rather than a
# crash (see process_meeting). This cap also bounds memory/connection use
# for an unusually long recording.
_GROQ_MAX_CONCURRENT_CHUNKS = 6

# If Groq just failed (network block/outage, not a one-off blip), every
# recording processed in the next _GROQ_RETRY_COOLDOWN_SECONDS skips
# straight to the local model instead of paying the connect/upload/timeout
# cost of a Groq attempt that's very likely to fail again too -- on a
# network where Groq genuinely can't be reached, this is pure wasted time
# on every single recording otherwise. Process-wide (not per-recording) by
# design: the failure reason (blocked network, Groq outage) is also
# process-wide, not specific to one request. Worst case if this guesses
# wrong (a real transient blip, not a lasting block) is that one recording
# within the cooldown window uses the local model instead of Groq -- a
# missed speed-up, never a correctness or quality loss, since the local
# model is the same one already used as the normal fallback.
_GROQ_RETRY_COOLDOWN_SECONDS = 45
_groq_last_failure_time = 0.0

_groq_client = None


def _get_groq_client(api_key: str):
    """Reuses one Groq client (and its underlying HTTP connection pool)
    across requests instead of opening a fresh one every time, same
    caching pattern as the Whisper models above -- also what makes
    _prewarm_groq_client below actually useful, since prewarming a
    connection only helps if a later real request reuses that same client
    instance rather than opening its own new one."""
    global _groq_client
    if _groq_client is None:
        from groq import Groq

        # A full _GROQ_CHUNK_SECONDS chunk can still take a while to
        # upload on a slow line -- generous flat timeout so that upload
        # time, not an arbitrary deadline, is what decides whether a
        # chunk succeeds.
        _groq_client = Groq(api_key=api_key, timeout=240.0)
    return _groq_client


def _prewarm_groq_client(api_key: str):
    """Fires one tiny throwaway request at startup so the DNS lookup +
    TLS handshake for Groq happens in the background instead of inline
    during someone's first real transcription. Best-effort: the
    connection may still have gone idle/closed by the time a real request
    arrives, so this isn't a guarantee, just better odds. Any failure here
    (no network yet, key not valid, etc.) is silently ignored -- it's a
    head start, not something a real request depends on."""
    try:
        client = _get_groq_client(api_key)
        silence = np.zeros(int(16000 * 0.5), dtype=np.float32)
        client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=("warmup.flac", _flac_chunk_bytes(silence, 16000)),
            response_format="json",
        )
    except Exception:
        pass


def _flac_chunk_bytes(wav_slice: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Encodes a slice of already-decoded audio as FLAC (lossless -- same
    audio, same accuracy as the uncompressed PCM this replaced) in memory,
    just for the outbound API call. Uploading fewer bytes directly attacks
    the actual bottleneck: on a typical connection, the time this whole
    function spends waiting is upload bandwidth, not Groq's own
    processing. Uses soundfile (already an installed dependency, bundles
    its own encoder) rather than the stdlib `wave` module, which can only
    write uncompressed audio -- no ffmpeg or other external tool needed,
    same reasoning as load_audio's own PyAV-based decoder avoiding one."""
    import io
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, wav_slice, sample_rate, format="FLAC")
    return buf.getvalue()


def _transcribe_groq_chunk(client, chunk: np.ndarray, sample_rate: int, offset: float) -> list[tuple[float, float, str, bool]]:
    """Transcribes one chunk, returning its segments with timestamps
    already shifted onto the full recording's timeline. Each segment is a
    (start, end, text, low_confidence) 4-tuple -- the extra flag is new,
    internal to this module only, callers split it out before it reaches
    cluster_speakers (which still only accepts the original 3-tuple
    shape), so nothing downstream is affected by this addition."""
    response = client.audio.transcriptions.create(
        model="whisper-large-v3",
        file=("chunk.flac", _flac_chunk_bytes(chunk, sample_rate)),
        response_format="verbose_json",
        timestamp_granularities=["segment"],
    )
    kept = []
    for seg in getattr(response, "segments", None) or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        no_speech_prob = seg.get("no_speech_prob", 0.0)
        avg_logprob = seg.get("avg_logprob", 0.0)
        # Same AND-not-OR hallucination filter as the local path below --
        # reject outright only when both signals agree.
        if no_speech_prob >= _GROQ_NO_SPEECH_THRESHOLD and avg_logprob <= _GROQ_LOW_CONFIDENCE_THRESHOLD:
            continue
        # Looser OR check: flag (but still keep) a segment where just one
        # signal looks shaky, so it's visible in the UI instead of
        # presented with the same confidence as a clean segment.
        low_confidence = no_speech_prob >= _GROQ_FLAG_NO_SPEECH_THRESHOLD or avg_logprob <= _GROQ_FLAG_LOW_CONFIDENCE_THRESHOLD
        kept.append((offset + seg.get("start", 0.0), offset + seg.get("end", 0.0), text, low_confidence))
    return kept


def _transcribe_with_groq(wav: np.ndarray, groq_api_key: str) -> list[tuple[float, float, str, bool]]:
    """Sends the recording to Groq's hosted whisper-large-v3 instead of
    running Whisper locally. Same underlying model family as before, but
    Groq's own inference hardware runs it at roughly 100-300x real-time
    (measured: a 10-minute clip in ~4s), so a 1-2 hour meeting transcribes
    in well under a minute of actual Groq-side processing instead of
    taking as long as the recording itself on this machine's CPU. Free
    tier, reuses the same GROQ_API_KEY already configured for notes
    generation -- no separate signup.

    No `language` is passed, letting it auto-detect, since this is used
    for English, Tamil, and Tanglish recordings alike and large-v3 handles
    code-switching far better than the local "small"/"medium" models do.
    (A vocabulary-hint `prompt` param was tried here too, but reverted --
    observed directly to make Groq drift into fabricating whole English
    sentences unrelated to what was said, later in a recording after a
    rough patch of audio. Not worth the word-choice nudge it was meant
    to give.)

    Each returned tuple is (start, end, text, low_confidence) -- the last
    element flags segments worth a second look (see
    _GROQ_FLAG_NO_SPEECH_THRESHOLD) without discarding them outright. The
    caller (process_meeting) splits this before handing transcript_raw to
    cluster_speakers, which still only accepts the original 3-tuple shape.

    Chunked per _GROQ_CHUNK_SECONDS -- primarily to stop one rough patch of
    audio from dragging down everything decoded after it within the same
    request (see the comment on _GROQ_CHUNK_SECONDS), and incidentally
    also keeps each upload far under the free tier's per-request size cap.
    Multiple chunks are sent concurrently (bounded by
    _GROQ_MAX_CONCURRENT_CHUNKS) rather than one after another -- each
    chunk is transcribed independently of the others regardless (that's
    already true for the sequential case, concurrency doesn't change which
    chunk boundaries exist), so running them at the same time changes
    nothing about what gets transcribed, only how long it takes for every
    chunk's result to come back for a multi-chunk (11+ minute) recording.
    Raises on any failure (network, auth, rate limit, unexpected response
    shape) so the caller can fall back to the local model rather than
    silently returning an empty or partial transcript.
    """
    client = _get_groq_client(groq_api_key)
    sample_rate = 16000
    chunk_len = _GROQ_CHUNK_SECONDS * sample_rate

    chunks = []  # (offset_seconds, samples)
    for chunk_start in range(0, len(wav), chunk_len):
        chunk = wav[chunk_start : chunk_start + chunk_len]
        if len(chunk) < sample_rate * 0.3:  # trailing sliver from the split, not worth a request
            continue
        chunks.append((chunk_start / sample_rate, chunk))

    if not chunks:
        return []

    if len(chunks) == 1:
        # The common case (recording under _GROQ_CHUNK_SECONDS, a single
        # chunk) -- no thread pool needed for one request.
        offset, chunk = chunks[0]
        return _transcribe_groq_chunk(client, chunk, sample_rate, offset)

    from concurrent.futures import ThreadPoolExecutor

    results = [None] * len(chunks)
    with ThreadPoolExecutor(max_workers=_GROQ_MAX_CONCURRENT_CHUNKS) as pool:
        futures = {
            pool.submit(_transcribe_groq_chunk, client, chunk, sample_rate, offset): i
            for i, (offset, chunk) in enumerate(chunks)
        }
        # Any one chunk raising propagates here (via .result()) and aborts
        # the whole attempt -- same all-or-nothing contract as before,
        # `process_meeting` falls back to the local model on any failure.
        for future, i in futures.items():
            results[i] = future.result()

    transcript_raw = [seg for chunk_result in results for seg in chunk_result]
    transcript_raw.sort(key=lambda seg: seg[0])  # chunks can finish out of order
    return transcript_raw


def _transcribe_wav(wav: np.ndarray, multilingual: str = "false") -> tuple[list[tuple[float, float, str, bool]], float]:
    """Groq-first, local-CPU-fallback transcription for one already-
    decoded/normalized wav array. Extracted verbatim out of
    process_meeting() (same logic, same comments, same behavior) so the
    new streaming chunk endpoint (/api/stream/chunk, Phase B) can reuse
    it instead of a second, drifting implementation.

    Returns (transcript_raw, elapsed_seconds) -- the elapsed time covers
    whichever path actually ran (Groq, or local model incl. first-call
    model load and the no-VAD retry if either happened), same number the
    existing [timing] prints below already compute, just also handed back
    to the caller instead of only printed, so it can be stored as part of
    a recording's processing metrics."""
    import time as _time

    # multilingual=True re-detects language per segment instead of once
    # for the whole recording, fixes languages switching mid-recording
    # (e.g. Tamil-English code-switching) getting stuck on one language.
    # Off by default: it roughly doubles encoder work, so it's opt-in
    # (frontend "mixed language" checkbox) rather than always-on, most
    # recordings are single-language and don't need the extra cost.
    is_multilingual = multilingual.lower() in ("true", "1", "yes")

    # Groq's hosted whisper-large-v3 is tried first whenever a key is
    # configured -- same quality tier regardless of the mixed-language
    # checkbox (it auto-detects), and roughly 100-300x real-time since
    # it runs on Groq's own hardware instead of this machine's CPU.
    # transcript_raw stays None if this doesn't run or doesn't succeed,
    # which is the signal to fall through to the local model below,
    # exactly the same tiered-fallback shape notes_generator.py uses.
    _t_transcribe = _time.time()
    transcript_raw = None
    groq_api_key = os.getenv("GROQ_API_KEY")
    global _groq_last_failure_time
    _groq_cooling_down = (_time.time() - _groq_last_failure_time) < _GROQ_RETRY_COOLDOWN_SECONDS
    if groq_api_key and _groq_cooling_down:
        print("[!] Groq failed recently, skipping it for this recording (cooldown) and going straight to the local model.")
    if groq_api_key and not _groq_cooling_down:
        try:
            transcript_raw = _transcribe_with_groq(wav, groq_api_key)
            print(f"[timing] groq transcription (whisper-large-v3): {_time.time() - _t_transcribe:.1f}s")
        except Exception as e:
            print(f"[!] Groq transcription failed ({e}), falling back to the local model.")
            transcript_raw = None
            _groq_last_failure_time = _time.time()

    if transcript_raw is None:
        _t_model = _time.time()
        # The same checkbox that enables multilingual detection also swaps
        # in the larger "medium" model: fast/dense speech and underrepresented
        # languages like Tamil are both capacity-limited on "small", not
        # fixable by a filter, this is the one place that actually needs a
        # bigger model rather than a cheaper preprocessing trick.
        model = get_medium_whisper_model() if is_multilingual else get_whisper_model()
        print(f"[timing] whisper model load/ready: {_time.time() - _t_model:.1f}s")
        # vad_filter skips silent stretches before Whisper runs on them --
        # same model, same beam_size, same accuracy on actual speech; it
        # just avoids spending time transcribing silence (which the
        # no_speech_prob/avg_logprob filter below would discard anyway).
        # threshold lowered from the 0.5 default so quieter/whispered speech
        # is still recognized as speech rather than trimmed out as silence,
        # any extra noise this lets through gets cleaned up by the
        # no_speech_prob/avg_logprob hallucination filter below anyway.
        # model.transcribe() itself is lazy (returns a generator), the actual
        # decoding work happens when `segments` is iterated below, so timing
        # this call alone would show ~0s even when transcription is the slow
        # part, the real cost only shows up wrapped around the loop.
        segments, _info = model.transcribe(
            wav, beam_size=5, vad_filter=True, vad_parameters={"threshold": 0.35}, multilingual=is_multilingual
        )

        # Whisper doesn't reliably say "silence" on near-empty audio, it
        # can hallucinate a short, garbled phrase instead. Whisper reports
        # its own confidence per segment, so use that to filter hallucinated
        # junk out rather than trusting everything it returns:
        #   no_speech_prob close to 1.0  -> Whisper itself thinks this was silence
        #   avg_logprob very negative    -> low confidence in the words it chose
        # Requires BOTH signals together (AND, not OR) before rejecting a
        # segment. A real hallucination (decoded from actual silence/noise)
        # reliably trips both at once; genuine speech in an unfamiliar
        # accent often trips only one (Whisper is unsure of the exact
        # wording -- low avg_logprob -- while still being confident it's
        # speech, not silence -- low no_speech_prob, or vice versa). The old
        # either-trips-it rule was discarding real accented speech as if it
        # were hallucinated silence.
        NO_SPEECH_THRESHOLD = 0.6
        LOW_CONFIDENCE_THRESHOLD = -1.0
        # Same looser flag-only thresholds as the Groq path (see
        # _GROQ_FLAG_NO_SPEECH_THRESHOLD) -- kept the same values here
        # so a segment isn't flagged differently depending only on
        # which engine happened to transcribe it.
        FLAG_NO_SPEECH_THRESHOLD = _GROQ_FLAG_NO_SPEECH_THRESHOLD
        FLAG_LOW_CONFIDENCE_THRESHOLD = _GROQ_FLAG_LOW_CONFIDENCE_THRESHOLD

        def _keep_segments(segs):
            kept = []
            for seg in segs:
                text = seg.text.strip()
                if not text:
                    continue
                if seg.no_speech_prob >= NO_SPEECH_THRESHOLD and seg.avg_logprob <= LOW_CONFIDENCE_THRESHOLD:
                    continue  # likely hallucinated from silence/noise, not real speech
                low_confidence = (
                    seg.no_speech_prob >= FLAG_NO_SPEECH_THRESHOLD or seg.avg_logprob <= FLAG_LOW_CONFIDENCE_THRESHOLD
                )
                kept.append((seg.start, seg.end, text, low_confidence))
            return kept

        transcript_raw = _keep_segments(segments)
        print(f"[timing] whisper transcription (incl. model load if first run): {_time.time() - _t_transcribe:.1f}s")

        if not transcript_raw:
            # The VAD pass found nothing speech-like at all -- rather than
            # trust that verdict outright (VAD can misjudge quiet, accented,
            # or noisy speech as non-speech and strip it before Whisper ever
            # sees it), retry once with VAD off so Whisper decodes the whole
            # clip directly. Only runs on this empty-result fallback path,
            # so a normal recording's speed is unaffected.
            _t_retry = _time.time()
            retry_segments, _info2 = model.transcribe(wav, beam_size=5, vad_filter=False, multilingual=is_multilingual)
            transcript_raw = _keep_segments(retry_segments)
            print(f"[timing] no-VAD retry pass: {_time.time() - _t_retry:.1f}s ({len(transcript_raw)} segments recovered)")

    return transcript_raw, round(_time.time() - _t_transcribe, 1)


def _finalize_result(
    wav: np.ndarray,
    transcript_raw: list,
    title: str,
    meeting_type: str,
    user: dict | None,
    *,
    source: str = "upload",
    media_type: str = "audio",
    file_size_bytes: int = 0,
    decode_seconds: float = 0.0,
    transcription_seconds: float = 0.0,
) -> dict:
    """Diarization + notes + response assembly + conditional save.
    Extracted verbatim out of process_meeting() so /api/stream/finalize
    (Phase B) can reuse the exact same tail-end logic -- diarization and
    notes generation are untouched, not reimplemented.

    The keyword-only args capture how this recording was produced (mic /
    tab-capture / upload, audio/video, file size) and the timing already
    spent before this call (decode, transcription) -- combined below with
    the diarization/notes timing this function measures itself into one
    `metrics` dict on the result, saved alongside it. All default to the
    pre-this-feature values, so a caller that never passes them still
    gets a working result, just with a less specific "upload" category
    and a 0s head start on the timing total."""
    import time as _time

    if not transcript_raw:
        return {"error": "No speech detected in that recording. Try again, speak closer to the mic."}

    # transcript_raw carries a low_confidence flag as its 4th element
    # (from either the Groq or local path above) -- split it off here
    # since cluster_speakers only accepts the original (start, end,
    # text) 3-tuple shape; low_confidence rides along separately and
    # gets merged back onto the final per-segment dicts below.
    low_confidence_flags = [seg[3] for seg in transcript_raw]
    transcript_raw = [seg[:3] for seg in transcript_raw]

    _t_cluster = _time.time()
    transcript, speakers = cluster_speakers(wav, 16000, transcript_raw, low_confidence=low_confidence_flags)

    # Self-introductions ("I'm Adrian", "my name is Priya") replace "Speaker
    # N" with the real name wherever one was actually said -- runs before
    # notes generation so notes/report inherit the real name too, for free.
    speakers = name_detection.detect_speaker_names(transcript, speakers)
    diarization_seconds = _time.time() - _t_cluster
    print(f"[timing] speaker clustering + name detection: {diarization_seconds:.1f}s")

    _t_notes = _time.time()
    notes = generate_notes(transcript, speakers, meeting_type=meeting_type or None)
    notes_seconds = _time.time() - _t_notes
    print(f"[timing] notes generation: {notes_seconds:.1f}s")
    duration = max((seg["end"] for seg in transcript), default=0)

    total_seconds = decode_seconds + transcription_seconds + diarization_seconds + notes_seconds
    metrics = {
        "source": source,
        "mediaType": media_type,
        "fileSizeBytes": file_size_bytes,
        "audioDurationSeconds": round(duration, 1),
        "timingSeconds": {
            "decode": round(decode_seconds, 1),
            "transcription": round(transcription_seconds, 1),
            "diarization": round(diarization_seconds, 1),
            "notesGeneration": round(notes_seconds, 1),
            "total": round(total_seconds, 1),
        },
    }

    result = {
        "meeting": {
            # timestamp-based, not just the date, otherwise every
            # recording on the same day would overwrite the last one
            "id": "live_" + datetime.now().strftime("%Y%m%d_%H%M%S"),
            "title": title,
            "date": str(date.today()),
            "durationSeconds": round(duration),
            "speakerCount": len(speakers),
            "meetingType": meeting_type or None,
        },
        "speakers": speakers,
        "transcript": transcript,
        "notes": notes,
        "metrics": metrics,
    }

    if user is not None:
        storage.save_meeting(result, user["id"])
    return result


@app.post("/api/process-meeting")
def process_meeting(
    audio: UploadFile = File(...),
    title: str = "Live Recording",
    meeting_type: str = "",
    multilingual: str = "false",
    source: str = "upload",
    include_video: str = "false",
    user: dict | None = Depends(auth.get_current_user_optional),
):
    suffix = Path(audio.filename or "recording.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(audio.file, tmp)
        tmp_path = tmp.name

    file_size_bytes = Path(tmp_path).stat().st_size
    # 'upload' (the manual file picker) decides audio-vs-video from the
    # file's own content type; a live mic/tab session already knows this
    # itself (tab capture never has video, see RecordingContext.jsx) and
    # reports it directly via include_video instead.
    if source == "upload":
        media_type = "video" if (audio.content_type or "").startswith("video/") else "audio"
    elif source == "tab":
        media_type = "audio"
    else:
        media_type = "video" if include_video.lower() in ("true", "1", "yes") else "audio"

    try:
        import time as _time

        _t0 = _time.time()
        # Decode once, reuse for both transcription and speaker clustering
        wav = load_audio(tmp_path)

        # Peak-normalize loudness before either model sees the audio, a
        # whispered or far-from-mic recording can sit well below a normal
        # speaking volume, normalizing brings it up to the same level
        # Whisper/the voice encoder were mostly trained on, without changing
        # anything about what was actually said.
        peak = np.abs(wav).max()
        if peak > 1e-4:  # skip normalizing near-silent/empty audio, would just amplify noise
            wav = wav * (0.95 / peak)
        decode_seconds = _time.time() - _t0
        print(f"[timing] audio decode + normalize: {decode_seconds:.1f}s")

        # Transcription + diarization/notes/save are factored into
        # _transcribe_wav / _finalize_result (same logic as before this was
        # split out) so the Phase B streaming endpoints below can reuse them.
        transcript_raw, transcription_seconds = _transcribe_wav(wav, multilingual)
        result = _finalize_result(
            wav,
            transcript_raw,
            title,
            meeting_type,
            user,
            source=source,
            media_type=media_type,
            file_size_bytes=file_size_bytes,
            decode_seconds=decode_seconds,
            transcription_seconds=transcription_seconds,
        )
        print(f"[timing] TOTAL: {_time.time() - _t0:.1f}s")
        return result
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------
# Phase B: chunk-by-chunk streaming transcription during a recording.
# Purely additive -- /api/process-meeting and /api/live-transcribe above
# are unmodified in behavior and remain the mandatory fallback if a
# streaming session is never started, or its finalize fails.
# ---------------------------------------------------------------------

@app.post("/api/stream/start")
def stream_start(
    title: str = Form("Live Recording"),
    meeting_type: str = Form(""),
    multilingual: str = Form("false"),
    source: str = Form("mic"),
    include_video: str = Form("false"),
    user: dict | None = Depends(auth.get_current_user_optional),
):
    """Creates a new streaming session, returns its id. Works for guests
    too (user is None) -- same guest model /api/process-meeting already
    has: a guest session's id IS its capability token for the chunk/
    finalize calls that follow, nothing more protects it, exactly like a
    guest's finished meeting today is protected by nothing but holding
    the response."""
    is_multilingual = multilingual.lower() in ("true", "1", "yes")
    is_video = include_video.lower() in ("true", "1", "yes")
    stream_id = storage.create_stream_session(
        user["id"] if user else None, title, meeting_type, is_multilingual, source, is_video
    )
    return {"stream_id": stream_id, "chunk_seconds": streaming.CHUNK_SECONDS, "overlap_seconds": streaming.OVERLAP_SECONDS}


def _require_stream_session(stream_id: str, user: dict | None) -> dict:
    session = storage.get_stream_session(stream_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown stream_id")
    # A session created by a logged-in user can only be used by that same
    # user; a guest session (user_id is NULL) needs no login, same as
    # /api/process-meeting's existing guest path -- the stream_id itself
    # (an unguessable uuid4) is what protects it, not an auth header.
    if session["user_id"] is not None:
        if user is None or user["id"] != session["user_id"]:
            raise HTTPException(status_code=403, detail="This stream does not belong to you")
    return session


@app.post("/api/stream/chunk")
def stream_chunk(
    audio: UploadFile = File(...),
    stream_id: str = Form(...),
    sequence_number: int = Form(...),
    start_time: float = Form(...),
    end_time: float = Form(...),
    user: dict | None = Depends(auth.get_current_user_optional),
):
    """Transcribes one chunk, idempotently keyed on (stream_id,
    sequence_number): a retried upload of a chunk that already has a
    stored result returns that result instead of transcribing again."""
    session = _require_stream_session(stream_id, user)
    if session["status"] != "recording":
        raise HTTPException(status_code=409, detail=f"Stream is '{session['status']}', not accepting new chunks")

    existing = storage.get_stream_chunk(stream_id, sequence_number)
    if existing is not None:
        return {
            "sequence_number": sequence_number,
            "status": existing["status"],
            "segments": [{"start": s, "end": e, "text": t} for (s, e, t, _lc) in json.loads(existing["transcript_json"])]
            if existing["transcript_json"]
            else [],
            "replayed": True,
        }

    suffix = Path(audio.filename or "chunk.webm").suffix or ".webm"
    audio_path = streaming.chunk_audio_path(stream_id, sequence_number, suffix)
    with open(audio_path, "wb") as f:
        shutil.copyfileobj(audio.file, f)

    t_received = time.time()
    try:
        _t_decode = time.time()
        wav = load_audio(str(audio_path))
        peak = np.abs(wav).max()
        if peak > 1e-4:
            wav = wav * (0.95 / peak)
        decode_seconds = time.time() - _t_decode
        t_transcribe_start = time.time()
        transcript_raw, transcribe_seconds = _transcribe_wav(wav, "true" if session["multilingual"] else "false")
        t_transcribe_end = time.time()

        storage.upsert_stream_chunk(
            stream_id,
            sequence_number,
            "ok",
            start_time,
            end_time,
            str(audio_path),
            transcript_raw,
            decode_seconds=decode_seconds,
            transcribe_seconds=transcribe_seconds,
        )
        streaming.log_chunk_metrics(
            stream_id=stream_id,
            sequence_number=sequence_number,
            event="chunk",
            audio_duration=round(end_time - start_time, 3),
            server_received=round(t_received, 3),
            transcription_start=round(t_transcribe_start, 3),
            transcription_end=round(t_transcribe_end, 3),
            total_server_latency=round(t_transcribe_end - t_received, 3),
            success=True,
        )
        return {
            "sequence_number": sequence_number,
            "status": "ok",
            "segments": [{"start": s, "end": e, "text": t} for (s, e, t, _lc) in transcript_raw],
            "replayed": False,
        }
    except Exception as e:
        # A failed chunk is an expected, handled outcome the client needs
        # to see and can retry -- not a 500, and never silently lost: the
        # audio file on disk is left in place either way (finalize later
        # sees status='failed' and knows exactly which sequence numbers
        # have a real gap, rather than pretending nothing happened).
        storage.upsert_stream_chunk(
            stream_id, sequence_number, "failed", start_time, end_time, str(audio_path), None, error=str(e)
        )
        streaming.log_chunk_metrics(
            stream_id=stream_id,
            sequence_number=sequence_number,
            event="chunk",
            audio_duration=round(end_time - start_time, 3),
            server_received=round(t_received, 3),
            success=False,
            error=str(e),
        )
        return {"sequence_number": sequence_number, "status": "failed", "error": str(e), "replayed": False}


@app.post("/api/stream/finalize")
def stream_finalize(
    stream_id: str = Form(...),
    user: dict | None = Depends(auth.get_current_user_optional),
):
    """Reassembles every chunk in sequence order, merges their
    transcripts (overlap-aware, timestamp-based, see streaming.py), then
    runs the exact same diarization + notes + save logic the whole-file
    path uses (_finalize_result) -- diarization itself is never
    modified, it just now receives audio/text assembled from chunks
    instead of from one upload."""
    t_finalize_start = time.time()
    session = _require_stream_session(stream_id, user)
    storage.set_stream_session_status(stream_id, "finalizing")

    chunks = storage.list_stream_chunks(stream_id)
    failed = [c["sequence_number"] for c in chunks if c["status"] != "ok"]

    merge_inputs = [
        streaming.ChunkForMerge(
            sequence_number=c["sequence_number"],
            start_time=c["start_time"],
            transcript_raw=json.loads(c["transcript_json"]),
        )
        for c in chunks
        if c["status"] == "ok" and c["transcript_json"]
    ]
    transcript_raw = streaming.merge_chunk_transcripts(merge_inputs)
    wav = streaming.reassemble_audio(chunks)

    # Aggregated from each chunk's own already-measured timing/size (see
    # stream_chunk above) -- computed here, before the cleanup below
    # deletes the chunk audio files off disk, not after.
    ok_chunks = [c for c in chunks if c["status"] == "ok"]
    decode_seconds = sum(c.get("decode_seconds") or 0.0 for c in ok_chunks)
    transcription_seconds = sum(c.get("transcribe_seconds") or 0.0 for c in ok_chunks)
    file_size_bytes = sum(
        Path(c["audio_path"]).stat().st_size for c in ok_chunks if c["audio_path"] and Path(c["audio_path"]).exists()
    )
    source = session.get("source") or "mic"
    media_type = "audio" if source == "tab" else ("video" if session.get("include_video") else "audio")

    user_dict = {"id": session["user_id"]} if session["user_id"] else None
    result = _finalize_result(
        wav,
        transcript_raw,
        session["title"],
        session["meeting_type"],
        user_dict,
        source=source,
        media_type=media_type,
        file_size_bytes=file_size_bytes,
        decode_seconds=decode_seconds,
        transcription_seconds=transcription_seconds,
    )

    if "error" not in result:
        storage.set_stream_session_status(stream_id, "finalized", finalized_meeting_id=result["meeting"]["id"])
    else:
        storage.set_stream_session_status(stream_id, "failed")

    streaming.log_chunk_metrics(
        stream_id=stream_id,
        event="finalize",
        chunk_count=len(chunks),
        failed_chunk_count=len(failed),
        finalize_latency=round(time.time() - t_finalize_start, 3),
        success="error" not in result,
    )

    # Phase B.1 validation only: STREAM_DEBUG_PRESERVE, when set, skips
    # the normal post-finalize cleanup so the raw chunk audio/transcripts
    # survive for an A/B comparison against the legacy path. Unset (the
    # default, and production), this is exactly the original unconditional
    # cleanup -- temporary, reversible, removed once this validation is done.
    if not os.getenv("STREAM_DEBUG_PRESERVE"):
        streaming.cleanup_stream_dir(stream_id)
        storage.delete_stream_session(stream_id)

    if failed:
        result["failedChunks"] = failed
    return result