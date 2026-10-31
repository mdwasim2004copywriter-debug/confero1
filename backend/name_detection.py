"""
Automatic speaker name detection, no LLM.

Scans each speaker's own lines for a literal self-introduction ("I'm
Adrian", "my name is Priya", "call me Sam", "this is Jordan") and, if
found, uses it as that speaker's display label instead of "Speaker N" --
the same thing Meet/Zoom give you "for free" from the call roster, done
here from the words actually spoken since there is no roster.

Same philosophy as speaker_id.py / meeting_intelligence.py: plain regex
over the literal transcript text, nothing generated, so this can never
attribute a name that wasn't actually said. Deliberately self-introduction
only -- it does not try to infer a name from someone else addressing a
speaker by name ("thanks, Priya"), that's a materially less reliable
signal (the very next speaker isn't guaranteed to be the person named) and
was explicitly left out of scope rather than risk a confident-looking but
wrong label.
"""

import re

# Reused verbatim as the "don't treat this as a name" set for weekdays/
# months, same list meeting_intelligence.py already maintains for the same
# purpose (a capitalized weekday/month is not a person's name either).
_WEEKDAYS_MONTHS = frozenset(
    "monday tuesday wednesday thursday friday saturday sunday january february march "
    "april may june july august september october november december".split()
)

# Words that sometimes end up capitalized right after a trigger phrase but
# are never actually a name: short acronyms/interjections an ASR model can
# capitalize, plus the handful of commitment phrases that could otherwise
# slip past the "this is X" trigger (e.g. a rare mid-sentence capitalization
# right after a comma). Kept intentionally small -- the capitalization
# check below already rejects the vast majority of false positives, this
# is just a backstop for the rare exception.
_NOT_A_NAME = frozenset(
    "ai ok okay sure fine good great here back done sorry happy glad "
    "going trying still just really actually basically essentially "
    "exactly also true correct right wrong clear obvious enough".split()
)

_NAME_TOKEN = r"([A-Z][a-zA-Z'’-]{1,20})"
_SECOND_NAME_TOKEN = r"([A-Z][a-zA-Z'’-]{1,20})"
_NAME_PATTERN = rf"{_NAME_TOKEN}(?:\s+{_SECOND_NAME_TOKEN})?"

# Ordered by reliability -- not that it matters for matching (every
# segment is scanned against all of them), but it documents which ones are
# the strongest signal if this list ever needs trimming.
_INTRO_PATTERNS = [
    re.compile(rf"(?i:\bmy name(?:'s| is)\s+){_NAME_PATTERN}\b"),
    re.compile(rf"(?i:\bcall me\s+){_NAME_PATTERN}\b"),
    re.compile(rf"(?i:\bi'?m\s+){_NAME_PATTERN}\b"),
    re.compile(rf"(?i:\bi\s+am\s+){_NAME_PATTERN}\b"),
    re.compile(rf"(?i:\bthis is\s+){_NAME_PATTERN}\b"),
]


def _extract_candidate(text):
    """Returns the first plausible self-introduced name in `text`, or
    None. Tries each trigger pattern in order and keeps the first match
    that survives validation, rather than the first pattern that merely
    matches syntactically -- e.g. "This is important" matches the "this
    is" trigger syntactically but "Important" fails validation (not
    capitalized in practice, and even if it were, it's in no one's actual
    name)."""
    for pattern in _INTRO_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = match.group(1)
        if match.group(2):
            candidate = f"{candidate} {match.group(2)}"
        if _is_valid_name(candidate):
            return candidate
    return None


def _is_valid_name(candidate):
    first_word = candidate.split()[0]
    if first_word.lower() in _WEEKDAYS_MONTHS or first_word.lower() in _NOT_A_NAME:
        return False
    if first_word.isupper() and len(first_word) <= 3:
        return False  # short all-caps token (e.g. an acronym), not a name
    return True


def detect_speaker_names(transcript, speakers):
    """
    transcript: list of segment dicts with "speakerId" and "text" (the
        same shape cluster_speakers() returns).
    speakers: list of speaker dicts with "id"/"label"/"color" (same shape
        cluster_speakers() returns).

    Returns a NEW speakers list (input is never mutated, matching this
    codebase's existing convention elsewhere, e.g. cluster_speakers always
    building fresh dicts): a speaker whose own lines contain a valid
    self-introduction gets `label` replaced with the detected name and
    `labelSource: "detected"`; everyone else keeps their existing label
    and gets `labelSource: "default"`.
    """
    detected_names = {}
    for seg in transcript:
        speaker_id = seg["speakerId"]
        if speaker_id in detected_names:
            continue  # first valid self-intro per speaker wins (earliest = most reliable)
        candidate = _extract_candidate(seg["text"])
        if candidate:
            detected_names[speaker_id] = candidate

    updated = []
    for sp in speakers:
        name = detected_names.get(sp["id"])
        if name:
            updated.append({**sp, "label": name, "labelSource": "detected"})
        else:
            updated.append({**sp, "labelSource": "default"})
    return updated
