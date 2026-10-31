"""
Regression checks for name_detection.py.

Run directly: python test_name_detection.py

No assertion in this file is soft, a failure prints and the script exits
non-zero, matching test_meeting_intelligence.py's convention.
"""

import sys

import name_detection as nd

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    if not condition:
        FAILURES.append(f"{label}: {detail}")
    print(f"[{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))
    return condition


def seg(speaker, text):
    return {"speakerId": speaker, "text": text}


# --- Positive cases: a real self-introduction, in several phrasings and
# cases, should be detected. ---

check(
    "catches 'I'm Adrian'",
    nd._extract_candidate("Hi everyone, I'm Adrian and I'll be leading today.") == "Adrian",
)
check(
    "catches 'My name is Priya' (capitalized trigger)",
    nd._extract_candidate("My name is Priya, nice to meet you all.") == "Priya",
)
check(
    "catches lowercase 'this is Jordan'",
    nd._extract_candidate("this is Jordan speaking from the Austin office.") == "Jordan",
)
check(
    "catches 'Call me Sam'",
    nd._extract_candidate("Call me Sam, everyone does.") == "Sam",
)
check(
    "catches two-word name 'I am Maria Garcia'",
    nd._extract_candidate("I am Maria Garcia, I'll take the notes.") == "Maria Garcia",
)

# --- Negative cases: common phrasings that must NOT be mistaken for a name. ---

check("rejects 'I'm not sure'", nd._extract_candidate("I'm not sure about that.") is None)
check("rejects 'I'm going to...'", nd._extract_candidate("I'm going to start with the budget.") is None)
check("rejects 'this is important'", nd._extract_candidate("This is important to note.") is None)
check(
    "rejects 'this is the Bletchley Declaration'",
    nd._extract_candidate("This is the Bletchley Declaration from a month ago.") is None,
)
check(
    "rejects plain opinion sentence with no trigger",
    nd._extract_candidate("While I agree with my friends that AI has the capacity to replicate.") is None,
)

# --- Full detect_speaker_names pass: only the speaker who actually
# self-introduces gets renamed; everyone else keeps "Speaker N". ---

transcript = [
    seg("S1", "While I agree with my friends that AI has the capacity to replicate."),
    seg("S2", "They will be replacing us, but I think the AI has the capacity."),
    seg("S3", "Hello everyone, I'm Adrian, good afternoon."),
    seg("S1", "And I believe that in the history of human development."),
]
speakers = [
    {"id": "S1", "label": "Speaker 1", "color": "teal"},
    {"id": "S2", "label": "Speaker 2", "color": "coral"},
    {"id": "S3", "label": "Speaker 3", "color": "periwinkle"},
]
result = {s["id"]: s for s in nd.detect_speaker_names(transcript, speakers)}

check("S1 (never introduces self) keeps 'Speaker 1'", result["S1"]["label"] == "Speaker 1")
check("S1 labelSource is 'default'", result["S1"]["labelSource"] == "default")
check("S2 (never introduces self) keeps 'Speaker 2'", result["S2"]["label"] == "Speaker 2")
check("S3 (says 'I'm Adrian') is renamed to 'Adrian'", result["S3"]["label"] == "Adrian")
check("S3 labelSource is 'detected'", result["S3"]["labelSource"] == "detected")
check(
    "input speakers list is not mutated",
    speakers[2]["label"] == "Speaker 3" and "labelSource" not in speakers[2],
)

if FAILURES:
    print(f"\n{len(FAILURES)} failure(s):")
    for f in FAILURES:
        print(f" - {f}")
    sys.exit(1)
print("\nAll checks passed.")
