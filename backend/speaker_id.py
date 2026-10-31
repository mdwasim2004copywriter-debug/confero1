"""
Speaker identification via voice clustering, no Hugging Face required.

This replaces pyannote's gated diarization pipeline with:
  1. resemblyzer's pretrained voice encoder, weights are bundled directly
     inside the pip package itself (confirmed: resemblyzer/pretrained.pt
     ships with `pip install resemblyzer`), so there's no download step,
     no account, no token, no "accept the terms" page.
  2. standard unsupervised clustering (scikit-learn) to group similar
     voices together, without knowing who anyone is in advance.

This works for ANY unknown speakers (open-set), not just a pre-enrolled
group, because resemblyzer's encoder was itself trained on thousands of
diverse speakers (that's how it learned "voice identity" as a general
concept), the same underlying idea as pyannote, just packaged without the
gate.

Honest limitation, same as any diarization approach: this identifies who's
speaking during normal turn-taking. It cannot separate two people talking
at the exact same time, their voices are physically mixed in the audio
wave by then, which is a different, much harder problem (speech source
separation), not diarization.
"""

import numpy as np
from resemblyzer import VoiceEncoder
from faster_whisper.audio import decode_audio
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import silhouette_score
from sklearn.metrics.pairwise import cosine_distances

# Tried instead of trusting one fixed global distance_threshold for every
# recording, see _best_clustering() below.
_DISTANCE_THRESHOLD_CANDIDATES = [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55]

SPEAKER_COLORS = ["teal", "coral", "periwinkle", "moss", "amber"]

MIN_SEGMENT_SECONDS = 1.5  # only segments at least this long are trusted to SEED a
# cluster (i.e. feed the initial AgglomerativeClustering fit), short/noisy clips
# here could spuriously form their own cluster and split one speaker into two.
MIN_EMBEDDABLE_SECONDS = 0.3  # below this there's essentially no audio to extract a
# voiceprint from at all; these fall back to inheriting the previous segment's
# speaker since there's no signal to compare.
DEFAULT_DISTANCE_THRESHOLD = 0.4  # loosened from 0.25 (tuned on a clean synthetic 3-voice
# test), real single-mic audio has enough natural pitch/pace variation within one
# speaker (e.g. trailing off at the end of a sentence) that 0.25 was splitting one
# person into two clusters. If it starts merging genuinely different speakers
# together instead, lower this back down.

MIN_SILHOUETTE_FOR_SPLIT = 0.15  # _best_clustering below can only compare splits
# that produce 2+ clusters (silhouette_score is undefined for 1), so without this
# floor it has no way to ever conclude "actually, this is one speaker" -- it would
# always report whichever forced multi-speaker split scored least badly, even when
# every candidate is really just one person's normal sentence-to-sentence voice
# variation. A genuine second speaker's voice is a much larger, clearer acoustic
# gap and comfortably clears this bar; a single speaker split by natural variance
# almost always falls well under it. If this starts merging two real speakers into
# one, lower it; if single-speaker recordings are still getting split, raise it.

_encoder = None


def _smooth_isolated_flips(cluster_ids):
    """Fixes isolated single-segment mislabels that interrupt an otherwise
    consistent run of the same speaker. The clustering above has no idea two
    adjacent segments belong to the same conversational turn, it's pure
    acoustic similarity per segment, so one sentence in the middle of
    someone's monologue occasionally gets pulled into the other cluster by
    acoustic similarity alone.

    Only ever touches a run of length exactly 1 sitting strictly BETWEEN two
    other runs that agree with each other, so:
      - a real back-and-forth exchange (2+ consecutive segments from the
        same speaker) is never altered.
      - a segment at the very start or end of the recording is never
        touched either, even if it's a lone segment after/before a long
        run, because that's indistinguishable from a real short opening or
        closing remark (e.g. "Yeah, yeah, good." after someone's long
        answer) without more evidence than run length alone. An earlier
        version of this function flipped those too and a test using this
        project's own example transcript caught it doing exactly that,
        turning a correct final line into a wrong one, so that part was
        deliberately left out rather than risk downgrading a currently-
        correct result. Fixing that case safely would need a
        distance-aware check (e.g. "is this segment actually closer to the
        other cluster's centroid than its own"), not implemented here.

    Doesn't touch distance_threshold, MIN_SEGMENT_SECONDS, or re-cluster
    anything, so it can't affect (or fix) the separate "collapsed into one
    speaker" failure mode; it only cleans up flips within an already-
    correct multi-speaker split."""
    n = len(cluster_ids)
    if n < 3:
        return cluster_ids

    runs = []  # [label, start_idx, end_idx] for each run of consecutive identical labels
    start = 0
    for i in range(1, n + 1):
        if i == n or cluster_ids[i] != cluster_ids[start]:
            runs.append([cluster_ids[start], start, i - 1])
            start = i

    for r, (label, s, e) in enumerate(runs):
        if e != s:
            continue  # only isolated single-segment runs are ever touched
        if r == 0 or r == len(runs) - 1:
            continue  # never touch the very first or very last run
        left, right = runs[r - 1], runs[r + 1]
        if left[0] == right[0]:
            runs[r][0] = left[0]

    smoothed = list(cluster_ids)
    for label, s, e in runs:
        for i in range(s, e + 1):
            smoothed[i] = label
    return smoothed


def _best_clustering(embeddings_matrix, fallback_threshold):
    """Tries a small range of distance thresholds and keeps whichever
    produces the most internally-consistent speaker groups (silhouette
    score), instead of trusting one fixed global threshold for every
    recording. A noisy far-field clip and a clean close-mic one don't
    necessarily want the same threshold; this lets each recording's own
    embeddings decide, so it's less likely to either merge two real
    speakers into one (threshold too loose) or split one speaker into two
    (threshold too tight) than a single static number ever could be."""
    n = len(embeddings_matrix)
    best_labels, best_score = None, -2.0
    for threshold in _DISTANCE_THRESHOLD_CANDIDATES:
        clustering = AgglomerativeClustering(
            n_clusters=None, distance_threshold=threshold, metric="cosine", linkage="average",
        )
        labels = clustering.fit_predict(embeddings_matrix)
        n_labels = len(set(labels))
        if n_labels < 2 or n_labels > n - 1:
            continue  # silhouette score is undefined outside 2..n-1 clusters
        score = silhouette_score(embeddings_matrix, labels, metric="cosine")
        if score > best_score:
            best_score, best_labels = score, labels
    if best_labels is not None:
        if best_score < MIN_SILHOUETTE_FOR_SPLIT:
            # None of the candidate splits found a confident separation --
            # most likely this is one speaker, not several who just happen
            # to cluster weakly. Collapse to a single cluster rather than
            # report a low-confidence, probably-wrong split.
            return np.zeros(n, dtype=int)
        return best_labels
    # Every candidate was degenerate (e.g. too few reliable segments to form
    # a meaningful silhouette comparison), fall back to the fixed default
    # rather than fail outright.
    clustering = AgglomerativeClustering(
        n_clusters=None, distance_threshold=fallback_threshold, metric="cosine", linkage="average",
    )
    return clustering.fit_predict(embeddings_matrix)


def get_encoder():
    """Loads the voice encoder once and reuses it across requests."""
    global _encoder
    if _encoder is None:
        print("Loading voice encoder (first request only)...")
        _encoder = VoiceEncoder()
    return _encoder


def load_audio(path: str) -> np.ndarray:
    """Loads and resamples an audio file to the 16kHz mono format both
    Whisper and the voice encoder expect. Uses faster-whisper's own
    decoder (PyAV-based, weights/codecs bundled in the pip package) rather
    than resemblyzer's librosa-based loader, the latter needs a separate
    system install of ffmpeg to handle browser recordings (.webm), which
    this avoids entirely. The file only gets decoded once, reused for both
    transcription and speaker clustering."""
    return decode_audio(path, sampling_rate=16000)


def cluster_speakers(
    wav: np.ndarray, sample_rate: int, transcript_raw, distance_threshold=DEFAULT_DISTANCE_THRESHOLD, low_confidence=None
):
    """
    wav: full recording as a 1D numpy float array (16kHz, from load_audio)
    sample_rate: sample rate of `wav` (16000 if it came from load_audio)
    transcript_raw: list of (start_seconds, end_seconds, text) from Whisper
    low_confidence: optional list of booleans, same length/order as
        transcript_raw, flagging segments worth a second look in the UI.
        Purely additive -- when omitted (the default), output is byte-for-
        byte identical to before this parameter existed.
    Returns (transcript, speakers) in the shape the rest of the app expects.
    """
    encoder = get_encoder()
    n = len(transcript_raw)

    embeddings = [None] * n
    reliable_indices = []  # long enough to trust for SEEDING cluster formation

    for i, (start, end, _text) in enumerate(transcript_raw):
        start_sample = max(0, int(start * sample_rate))
        end_sample = min(len(wav), int(end * sample_rate))
        clip = wav[start_sample:end_sample]

        if len(clip) < MIN_EMBEDDABLE_SECONDS * sample_rate:
            continue  # essentially no audio, no voiceprint signal to extract

        try:
            embeddings[i] = encoder.embed_utterance(clip)
        except Exception:
            continue

        if len(clip) >= MIN_SEGMENT_SECONDS * sample_rate:
            reliable_indices.append(i)

    cluster_ids = [None] * n

    if len(reliable_indices) >= 2:
        labels = _best_clustering(np.array([embeddings[i] for i in reliable_indices]), distance_threshold)
        for idx, label in zip(reliable_indices, labels):
            cluster_ids[idx] = int(label)
    elif len(reliable_indices) == 1:
        cluster_ids[reliable_indices[0]] = 0

    # Centroid of each cluster, built only from the reliable (long) segments,
    # this is what short segments below get compared against.
    centroids = {}
    for i in reliable_indices:
        centroids.setdefault(cluster_ids[i], []).append(embeddings[i])
    centroids = {cid: np.mean(embs, axis=0) for cid, embs in centroids.items()}

    # Segments too short to trust for SEEDING a cluster, but long enough to embed,
    # get matched to whichever already-established speaker they sound closest to.
    # This is what correctly identifies a short interjection ("Exactly.", "Correct.",
    # "Good.") as the OTHER speaker instead of assuming it's a continuation of
    # whoever spoke immediately before, that positional-inheritance assumption is
    # only used as a last resort now, for clips with no embedding at all.
    last = 0
    for i in range(n):
        if cluster_ids[i] is not None:
            last = cluster_ids[i]
            continue
        if embeddings[i] is not None and centroids:
            cids = list(centroids.keys())
            dists = cosine_distances([embeddings[i]], [centroids[c] for c in cids])[0]
            cluster_ids[i] = cids[int(np.argmin(dists))]
        else:
            cluster_ids[i] = last
        last = cluster_ids[i]

    cluster_ids = _smooth_isolated_flips(cluster_ids)

    # Map cluster ids -> friendly speaker ids, in order of first appearance
    cluster_to_friendly = {}
    transcript = []
    for i, (start, end, text) in enumerate(transcript_raw):
        cid = cluster_ids[i]
        if cid not in cluster_to_friendly:
            cluster_to_friendly[cid] = f"S{len(cluster_to_friendly) + 1}"
        entry = {
            "id": f"seg_{i + 1:03d}",
            "speakerId": cluster_to_friendly[cid],
            "start": round(start, 1),
            "end": round(end, 1),
            "text": text,
        }
        if low_confidence is not None:
            entry["lowConfidence"] = bool(low_confidence[i])
        transcript.append(entry)

    speakers = [
        {"id": friendly, "label": f"Speaker {friendly[1:]}", "color": SPEAKER_COLORS[i % len(SPEAKER_COLORS)]}
        for i, friendly in enumerate(cluster_to_friendly.values())
    ]

    return transcript, speakers