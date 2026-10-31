"""
Meeting notes generation, the last step of the pipeline.

Five tiers, tried in order, each falling back to the next on any failure.
Gemini and Groq both retry transient server errors a few times in-process
before giving up to the next tier, so a brief provider-side overload
doesn't silently downgrade a request all the way to the local engine.

1. GEMINI_API_KEY set: sends the transcript to Google's Gemini (free tier)
   and asks for the summary/decisions/action items/topics directly,
   tailored to the meeting's selected type if one was given. This is the
   default, best-quality tier now that a free Gemini key is configured.

2. GROQ_API_KEY set: same idea, via Groq's free-tier open-weight models.
   Only reached if Gemini isn't configured or its call (with retries)
   still fails, a second independent AI provider so a Gemini-side outage
   doesn't drop straight to the rule-based engine.

3. ANTHROPIC_API_KEY set: same idea, via Claude instead. Only reached if
   neither Gemini nor Groq served the request. Costs a small amount per
   meeting and needs internet. Opt-in, nothing in this file connects to
   it by default.

4. No API key at all (or all LLM tiers failed): a fully local,
   deterministic engine (meeting_intelligence.py), sentence-level
   marker/pattern detection, TF-IDF topic/centrality scoring, and
   template composition over the extracted data. No language model
   involved, so nothing here can hallucinate a decision/action/deadline
   that wasn't actually said. Kept as the automatic safety net so an AI
   provider outage never breaks note generation.

5. Free extractive fallback (TextRank + keyword heuristics), used only if
   tier 4 itself raises for some unexpected reason. Belt-and-suspenders;
   tier 4 has no model download/network/OOM failure mode the way a real
   model would, so this should rarely if ever trigger.
"""

import os
import re

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

import meeting_intelligence


def generate_notes(transcript, speakers, meeting_type=None):
    """Runtime diagnostic only (added to trace which tier actually served a
    given request, after finding a stale server process was silently
    running an old FLAN-T5-based build) -- the tier selection logic itself
    is unchanged from before this print was added, aside from adding the
    Gemini tier ahead of Claude, and later racing Gemini+Groq (see
    _race_gemini_and_groq)."""
    gemini_key = os.getenv("GEMINI_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")
    notes = _race_gemini_and_groq(transcript, speakers, meeting_type, gemini_key, groq_key)
    if notes is not None:
        return _ground_against_transcript(notes, transcript)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        try:
            notes = _normalize_notes(_generate_notes_llm(transcript, speakers, api_key, meeting_type))
            print("[notes-engine] used: claude")
            return _ground_against_transcript(notes, transcript)
        except Exception as e:
            print(f"[!] Claude notes generation failed ({e}), falling back to the local engine.")

    try:
        notes = _normalize_notes(meeting_intelligence.analyze_transcript(transcript, speakers, meeting_type))
        print("[notes-engine] used: meeting_intelligence_v2")
        return _ground_against_transcript(notes, transcript)
    except Exception as e:
        print(f"[!] Local intelligence engine failed ({e}), falling back to the basic extractive summarizer.")

    notes = _normalize_notes(_generate_notes_extractive(transcript, speakers))
    print("[notes-engine] used: legacy_fallback")
    return _ground_against_transcript(notes, transcript)


def _race_gemini_and_groq(transcript, speakers, meeting_type, gemini_key, groq_key):
    """Tries Gemini and Groq at the same time instead of strictly one
    after another. Measured directly during development: Gemini's free
    tier can hang until it times out (one real run took 42s before
    falling back) or come back with a 503 "high demand" error -- under
    the old strictly-sequential order, that whole wait blocked Groq from
    even being attempted. Racing them means whichever provider actually
    answers first wins on a given day, and the other's still-in-flight
    call is simply left to finish in the background and its result
    discarded -- no accuracy difference either way, both tiers already
    existed and produced the same normalized shape, this only changes
    which one a given request ends up using and how long that takes.
    Returns None (never raises) if both fail, so the caller falls through
    to the next tier exactly as before. Skips the thread pool entirely
    when only one (or neither) key is configured, so the single-provider
    case behaves exactly as it did before this change."""
    if gemini_key and groq_key:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(_generate_notes_gemini, transcript, speakers, gemini_key, meeting_type): "gemini",
                pool.submit(_generate_notes_groq, transcript, speakers, groq_key, meeting_type): "groq",
            }
            errors = {}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    notes = _normalize_notes(future.result())
                    print(f"[notes-engine] used: {name} (raced)")
                    return notes
                except Exception as e:
                    errors[name] = e
            print(f"[!] Both Gemini and Groq notes generation failed ({errors}), falling back to the next tier.")
        return None

    if gemini_key:
        try:
            notes = _normalize_notes(_generate_notes_gemini(transcript, speakers, gemini_key, meeting_type))
            print("[notes-engine] used: gemini")
            return notes
        except Exception as e:
            print(f"[!] Gemini notes generation failed ({e}), falling back to the next tier.")

    if groq_key:
        try:
            notes = _normalize_notes(_generate_notes_groq(transcript, speakers, groq_key, meeting_type))
            print("[notes-engine] used: groq")
            return notes
        except Exception as e:
            print(f"[!] Groq notes generation failed ({e}), falling back to the next tier.")

    return None


def _normalize_notes(notes):
    """Ensures every tier's output has the same shape, so the frontend can
    always rely on every field being present.

    Each raw decision now carries a "type" (decision/planned/proposal/
    request) -- decision/planned are real forward commitments and stay in
    "decisions"; proposal/request are tentative and get routed into
    "proposals" instead, an already-existing field (meeting_intelligence.py,
    the local engine, has populated it for a while) that the LLM tiers
    just never used before. This is a plain Python list split, not a UI
    change -- the frontend was already only rendering "decisions", so
    moving tentative language out of it is a real reduction of
    tentative-language-labeled-as-decided, with no new field for it to
    read. A plain string (the Claude tier's older shape, or the local
    engine's own already-plain-string paths) has no type info, so it's
    trusted as a real decision, same behavior as before this change."""
    decisions_raw = notes.get("decisions", [])
    decisions, proposals = [], list(notes.get("proposals", []))
    for d in decisions_raw:
        if isinstance(d, str):
            decisions.append({"text": d, "speaker": None, "type": "decision"})
            continue
        d_type = d.get("type", "decision")
        entry = {**d, "type": d_type}
        (proposals if d_type in ("proposal", "request") else decisions).append(entry)

    # The LLM tiers emit dates/numbers/technicalTerms as flat top-level
    # fields (simpler for the model to produce than a nested object, fewer
    # output tokens), the local engine (meeting_intelligence.py) already
    # returns them nested under "facts" -- accept either shape.
    facts = notes.get("facts") or {
        "numbers": notes.get("numbers", []),
        "dates": notes.get("dates", []),
        "technicalTerms": notes.get("technicalTerms", []),
    }

    defaults = {
        "purpose": "",
        "takeaways": [],
        "proposals": [],
        "questions": [],
        "openQuestions": [],
        "issues": [],
        "facts": {"numbers": [], "dates": [], "technicalTerms": []},
        "timeline": [],
        "speakerInsights": [],
        "conclusion": "",
    }
    result = {**defaults, **notes}
    result["decisions"] = decisions
    result["proposals"] = proposals
    result["facts"] = {**defaults["facts"], **facts}
    # Drop the flat versions now that they're folded into "facts" -- keeping
    # both would just be redundant, unused duplicate data in the response.
    result.pop("numbers", None)
    result.pop("dates", None)
    result.pop("technicalTerms", None)
    return result


def _full_text(transcript):
    return " ".join(seg["text"] for seg in transcript)


_GROUNDING_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "will", "team", "meeting",
    "action", "items", "item", "task", "tasks", "need", "needs", "share",
    "update", "summarize", "summary", "discuss", "discussed", "about",
}


def _ground_against_transcript(notes, transcript):
    """Cheap, local, non-LLM safety net against hallucinated content --
    pure string matching over the actual segments, no network call, so
    this adds no real latency. Drops an action item/decision/proposal/
    issue only when it shares essentially no real words with what was
    actually said (a strong signal of fabrication -- e.g. a generic
    "summarize and share action items" entry that doesn't trace to
    anything in the transcript, seen in real testing). Requires 2 shared
    substantive words when the phrase has enough of them to check (a
    single coincidentally-shared word isn't reliable -- verified
    directly: a fabricated "launch an ad campaign in Tokyo" survived a
    1-word bar purely because "launch" also appeared in an unrelated real
    sentence about launching a sprint), falling back to the 1 word
    available for a genuinely short real phrase so that isn't unfairly
    rejected.

    Also attaches the best-matching segment's start time as "sourceStart"
    on each kept entry -- backend data only, computed from string
    matching already being done anyway (just tracked per-segment instead
    of only against the flattened whole), nothing in the frontend reads
    it yet so this doesn't change anything currently displayed.
    """
    segment_words = [
        (seg["start"], set(re.findall(r"[a-zA-Z஀-௿]{3,}", seg["text"].lower())) - _GROUNDING_STOPWORDS)
        for seg in transcript
    ]
    transcript_words = set()
    for _, words in segment_words:
        transcript_words |= words

    def _check(text):
        words = set(re.findall(r"[a-zA-Z஀-௿]{3,}", (text or "").lower())) - _GROUNDING_STOPWORDS
        if not words:
            # Nothing but generic filler words left -- that's exactly the
            # content-free, self-referential-about-the-notes-process
            # pattern worth dropping, not a reason to trust it by default.
            return False, None
        required = min(2, len(words))
        if len(words & transcript_words) < required:
            return False, None
        best_start, best_overlap = None, -1
        for start, seg_words in segment_words:
            overlap = len(words & seg_words)
            if overlap > best_overlap:
                best_overlap, best_start = overlap, start
        return True, best_start

    def _filter_and_tag(entries, get_text):
        kept = []
        for entry in entries:
            grounded, start = _check(get_text(entry))
            if not grounded:
                continue
            kept.append({**entry, "sourceStart": start} if isinstance(entry, dict) else entry)
        return kept

    notes["actionItems"] = _filter_and_tag(notes.get("actionItems", []), lambda item: item.get("task"))
    notes["decisions"] = _filter_and_tag(
        notes.get("decisions", []), lambda d: d.get("text") if isinstance(d, dict) else d
    )
    notes["proposals"] = _filter_and_tag(
        notes.get("proposals", []), lambda p: p.get("text") if isinstance(p, dict) else p
    )
    notes["issues"] = _filter_and_tag(
        notes.get("issues", []),
        lambda i: f"{i.get('issue', '')} {i.get('evidence', '')}" if isinstance(i, dict) else i,
    )
    return notes


def _speaker_label(speakers, speaker_id):
    for s in speakers:
        if s["id"] == speaker_id:
            return s["label"]
    return speaker_id


def _split_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _naive_summary(text, max_sentences=4):
    """Fallback with zero dependencies, used only if TextRank can't run
    (e.g. a transcript too short/repetitive for TF-IDF to work with)."""
    sentences = _split_sentences(text)
    if len(sentences) <= max_sentences:
        return " ".join(sentences)
    first, last = sentences[0], sentences[-1]
    middle_ranked = sorted(sentences[1:-1], key=len, reverse=True)
    keep = {first, last, *middle_ranked[: max(0, max_sentences - 2)]}
    ordered = [s for s in sentences if s in keep]
    return " ".join(ordered)


def _textrank_summary(text, max_sentences=4):
    """Real extractive summarization: scores each sentence by how similar
    it is to the rest of the transcript (TF-IDF + cosine similarity), then
    runs a PageRank-style ranking over that similarity graph, a sentence
    that echoes the conversation's main themes scores higher, regardless
    of its length. This is the same underlying idea as the original
    TextRank algorithm (Mihalcea & Tarau, 2004)."""
    sentences = _split_sentences(text)
    if len(sentences) <= max_sentences:
        return " ".join(sentences)

    try:
        vectorizer = TfidfVectorizer(stop_words="english")
        tfidf = vectorizer.fit_transform(sentences)
        sim_matrix = cosine_similarity(tfidf)
        np.fill_diagonal(sim_matrix, 0)

        row_sums = sim_matrix.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1
        transition = sim_matrix / row_sums

        scores = np.ones(len(sentences)) / len(sentences)
        damping = 0.85
        for _ in range(30):
            scores = (1 - damping) / len(sentences) + damping * transition.T.dot(scores)

        ranked_idx = sorted(np.argsort(-scores)[:max_sentences])  # top N, restored to original order
        return " ".join(sentences[i] for i in ranked_idx)
    except Exception:
        return _naive_summary(text, max_sentences)


# Due-date phrases worth flagging next to an action item, not a real
# parsed date, just a text hint pulled from what was actually said.
_DUE_DATE_PATTERN = re.compile(
    r"\b(by\s+(?:tomorrow|today|tonight|next\s+week|end\s+of\s+(?:day|week|month)|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)"
    r"(?:\s+\d{1,2}(?:st|nd|rd|th)?)?)\b",
    re.IGNORECASE,
)


def _extract_due_hint(text):
    match = _DUE_DATE_PATTERN.search(text)
    return match.group(0).strip().capitalize() if match else None


def _extract_topics(text):
    """TF-IDF top terms, used only by the tier-3 fallback below. The
    default (tier 2) path has its own richer version in
    meeting_intelligence.py."""
    topics = []
    try:
        sentences = _split_sentences(text)
        if len(sentences) >= 2:
            vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=30)
            tfidf = vectorizer.fit_transform(sentences)
            scores = np.asarray(tfidf.sum(axis=0)).flatten()
            terms = vectorizer.get_feature_names_out()
            ranked = sorted(zip(terms, scores), key=lambda t: -t[1])
            topics = [t.title() for t, _ in ranked[:5] if len(t) > 3]
    except Exception:
        pass
    if not topics:
        # fallback: plain frequency count, zero dependencies
        stopwords = {
            "about", "would", "could", "should", "there", "their", "which", "where",
            "these", "those", "going", "think", "really", "actually", "because",
        }
        words = [w for w in re.findall(r"[a-zA-Z]{5,}", text.lower()) if w not in stopwords]
        counts = {}
        for w in words:
            counts[w] = counts.get(w, 0) + 1
        topics = [w.capitalize() for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:5]]
    return topics


def _generate_notes_extractive(transcript, speakers):
    text = _full_text(transcript)
    summary = _textrank_summary(text) if text else "No speech detected."

    decision_keywords = [
        "decided", "agreed", "let's lock", "we'll go with", "final call",
        "confirmed", "locked to", "settled on", "going with", "approved",
    ]
    action_keywords = [
        "i'll", "i will", "can you", "let's", "need to", "will own",
        "follow up", "check in", "going to", "should", "must", "have to",
        "make sure", "reach out", "send over", "put together",
    ]

    decisions, action_items, seen = [], [], set()
    for seg in transcript:
        lower = seg["text"].lower()
        if any(k in lower for k in decision_keywords) and seg["text"] not in seen:
            decisions.append(seg["text"])
            seen.add(seg["text"])
        if any(k in lower for k in action_keywords) and seg["text"] not in seen:
            action_items.append(
                {
                    "owner": _speaker_label(speakers, seg["speakerId"]),
                    "task": seg["text"],
                    "due": _extract_due_hint(seg["text"]),
                }
            )
            seen.add(seg["text"])

    return {
        "summary": summary,
        "decisions": decisions[:5],
        "actionItems": action_items[:8],
        "topics": _extract_topics(text),
    }


# Shared across all three LLM tiers (Claude/Groq/Gemini) so their outputs
# stay consistent regardless of which one actually answers a given
# request. Counts are capped ("up to 3") deliberately -- this asks for
# real additional structure without leaving any field open-ended, keeping
# the extra output (and therefore extra generation time) bounded rather
# than growing arbitrarily with a longer transcript.
_NOTES_INSTRUCTIONS = """This transcript may mix Tamil and English naturally (code-switching) -- interpret meaning across both languages together, don't treat them as unrelated fragments.

- purpose: one concise sentence stating what this meeting was actually about, inferred from its real content (e.g. "Review project status, discuss a performance concern, and align on integration and timeline next steps."). Leave empty only if the transcript truly has too little content to infer one.
- decisions: each tagged with a type -- "decision" (truly decided/agreed, e.g. "we've agreed to...", "X will happen"), "planned" (a genuine forward commitment stated as a plan, e.g. "we can start integration Monday"), "proposal" (a tentative suggestion, e.g. "I think we could...", "we should..."), or "request" (asking for something that still needs confirmation, e.g. "I'll ask for two weeks"). Do not upgrade a proposal or request into "decision" just because it sounds confident -- only "we've agreed"/"we will" -style committed language is "decision". Leave the list empty if nothing was truly decided or planned.
- actionItems: owner is the actual responsible person/team named in the transcript (e.g. "Backend team", "Design team", or a person's name) -- not just the diarized speaker label unless that's genuinely who's responsible. due is the deadline actually mentioned (e.g. "Friday", "Monday morning", "tomorrow evening") -- null only if none was stated.
- takeaways: up to 3 short, genuinely notable takeaways, if any real ones exist.
- issues: up to 3 real problems/blockers/client concerns/schedule or deadline risks/dependencies actually raised (e.g. a performance complaint, a deadline that may not be met) -- each with a short "issue" label and a one-sentence "evidence" describing what was actually said that supports it. Leave empty if nothing like this was actually raised -- never invent one.
- questions: up to 3 short unresolved/open questions, if any.
- dates: specific dates/deadlines/time references actually mentioned (e.g. "Friday", "end of this month").
- numbers: notable numbers actually mentioned (e.g. "2 weeks", "7.8%").
- technicalTerms: notable technical/business terms actually mentioned.

Leave a list empty rather than inventing an entry -- only include something if it is clearly supported by the transcript."""


def _generate_notes_llm(transcript, speakers, api_key, meeting_type=None):
    import json as _json
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    transcript_text = "\n".join(
        f"{_speaker_label(speakers, seg['speakerId'])}: {seg['text']}" for seg in transcript
    )

    style_hint = ""
    if meeting_type:
        style_hint = f"\nThis was tagged as a **{meeting_type}** meeting, match the summary's tone and the action items' level of formality to that context.\n"

    prompt = f"""Here is a meeting transcript, labeled by speaker:

{transcript_text}
{style_hint}
{_NOTES_INSTRUCTIONS}

Return ONLY valid JSON (no markdown fences, no commentary) in exactly this shape:
{{
  "summary": "2-4 sentence summary of the meeting",
  "purpose": "one concise sentence stating what the meeting was about",
  "decisions": [{{"text": "decision 1", "type": "decision"}}],
  "actionItems": [{{"owner": "who's responsible", "task": "what they need to do", "due": null}}],
  "topics": ["topic 1", "topic 2", "topic 3"],
  "takeaways": ["takeaway 1"],
  "issues": [{{"issue": "short label", "evidence": "one sentence from the transcript supporting it"}}],
  "questions": ["question 1"],
  "dates": ["date/deadline mentioned"],
  "numbers": ["number mentioned"],
  "technicalTerms": ["term mentioned"]
}}"""

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```json|```$", "", raw, flags=re.MULTILINE).strip()
    return _json.loads(raw)


_GEMINI_NOTES_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "purpose": {"type": "STRING"},
        "decisions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING"},
                    "type": {"type": "STRING", "enum": ["decision", "planned", "proposal", "request"]},
                },
                "required": ["text", "type"],
            },
        },
        "actionItems": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "owner": {"type": "STRING"},
                    "task": {"type": "STRING"},
                    "due": {"type": "STRING", "nullable": True},
                },
                "required": ["owner", "task"],
            },
        },
        "topics": {"type": "ARRAY", "items": {"type": "STRING"}},
        "takeaways": {"type": "ARRAY", "items": {"type": "STRING"}},
        "issues": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "issue": {"type": "STRING"},
                    "evidence": {"type": "STRING"},
                },
                "required": ["issue", "evidence"],
            },
        },
        "questions": {"type": "ARRAY", "items": {"type": "STRING"}},
        "dates": {"type": "ARRAY", "items": {"type": "STRING"}},
        "numbers": {"type": "ARRAY", "items": {"type": "STRING"}},
        "technicalTerms": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["summary", "decisions", "actionItems", "topics"],
}


def _generate_notes_gemini(transcript, speakers, api_key, meeting_type=None):
    import json as _json
    import time

    from google import genai
    from google.genai import types
    from google.genai import errors as genai_errors

    # Explicit short timeout -- without this, a slow/unresponsive network
    # falls back on the SDK's own (much longer) default, and combined with
    # retries below that could stack up to minutes before ever reaching the
    # local fallback engine. Gemini itself rejects anything under 10s
    # ("Minimum allowed deadline is 10s"), so 10s is the floor, not a choice.
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=10_000))

    transcript_text = "\n".join(
        f"{_speaker_label(speakers, seg['speakerId'])}: {seg['text']}" for seg in transcript
    )

    style_hint = ""
    if meeting_type:
        style_hint = f"\nThis was tagged as a **{meeting_type}** meeting, match the summary's tone and the action items' level of formality to that context.\n"

    prompt = f"""Here is a meeting transcript, labeled by speaker:

{transcript_text}
{style_hint}
{_NOTES_INSTRUCTIONS}

Write every field in English regardless of what language the meeting was
actually conducted in (e.g. Tamil, Tanglish, or any other language), only
quote a name or term in its original language if translating it would
lose meaning."""

    # Structured output (response_schema) instead of prompt-only JSON
    # instructions, Gemini enforces the shape itself rather than relying on
    # the model to follow a text instruction, so this can't come back
    # malformed the way a plain-text-JSON request occasionally can.
    #
    # The free-tier model occasionally returns a transient 503 "high demand"
    # error that clears within a few seconds, retry a couple of times
    # in-process before giving up and letting generate_notes() fall through
    # to the next tier -- this is what actually stops a brief Google-side
    # overload from silently downgrading every request to the local engine.
    last_error = None
    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_GEMINI_NOTES_SCHEMA,
                ),
            )
            return _json.loads(response.text)
        except genai_errors.ServerError as e:
            last_error = e
            if attempt < 1:
                time.sleep(1)
    raise last_error


def _generate_notes_groq(transcript, speakers, api_key, meeting_type=None):
    import json as _json
    import time

    import groq as groq_sdk
    from groq import Groq

    # Explicit short timeout, same reasoning as the Gemini tier above.
    client = Groq(api_key=api_key, timeout=8.0)

    transcript_text = "\n".join(
        f"{_speaker_label(speakers, seg['speakerId'])}: {seg['text']}" for seg in transcript
    )

    style_hint = ""
    if meeting_type:
        style_hint = f"\nThis was tagged as a **{meeting_type}** meeting, match the summary's tone and the action items' level of formality to that context.\n"

    prompt = f"""Here is a meeting transcript, labeled by speaker:

{transcript_text}
{style_hint}
{_NOTES_INSTRUCTIONS}

Return ONLY valid JSON (no markdown fences, no commentary) in exactly this shape:
{{
  "summary": "2-4 sentence summary of the meeting",
  "purpose": "one concise sentence stating what the meeting was about",
  "decisions": [{{"text": "decision 1", "type": "decision"}}],
  "actionItems": [{{"owner": "who's responsible", "task": "what they need to do", "due": null}}],
  "topics": ["topic 1", "topic 2", "topic 3"],
  "takeaways": ["takeaway 1"],
  "issues": [{{"issue": "short label", "evidence": "one sentence from the transcript supporting it"}}],
  "questions": ["question 1"],
  "dates": ["date/deadline mentioned"],
  "numbers": ["number mentioned"],
  "technicalTerms": ["term mentioned"]
}}
"type" must be exactly one of: "decision", "planned", "proposal", "request".
Write every field in English regardless of what language the meeting was
actually conducted in (e.g. Tamil, Tanglish, or any other language), only
quote a name or term in its original language if translating it would
lose meaning."""

    # Same retry-on-transient-error pattern as the Gemini tier above, a
    # free-tier provider's own load spikes shouldn't be what decides
    # whether this meeting gets an AI summary or the rule-based one.
    last_error = None
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[
                    {"role": "system", "content": "Return ONLY valid JSON, no markdown fences, no commentary. Always write the summary/decisions/topics fields in English, even if the source transcript is in another language."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content.strip()
            return _json.loads(raw)
        except (groq_sdk.APIStatusError, groq_sdk.APIConnectionError) as e:
            last_error = e
            is_retryable = isinstance(e, groq_sdk.APIConnectionError) or getattr(e, "status_code", 0) >= 500
            if attempt < 1 and is_retryable:
                time.sleep(1)
            elif not is_retryable:
                raise
    raise last_error
