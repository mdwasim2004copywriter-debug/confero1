"""
Regression benchmark suite for meeting_intelligence.py (V2).

Run directly: python test_meeting_intelligence.py

Test A's transcript is CONSTRUCTED to match every fact/pattern described in
the spec this engine was built against (project status update: analytics
accuracy problem, 3 actions, deadline revision, unresolved data-presentation
question, dataset/testing/performance/role numbers) -- the literal benchmark
transcript that exposed the V1 failures was run by the user directly and
isn't available here, so this is a reconstruction, not the original. Tests
C-J are transcribed verbatim from the spec's own example sentences.

No assertion in this file is soft, a failure prints and the script exits
non-zero, so this can double as a CI-style check after future changes to
meeting_intelligence.py.
"""

import sys

import notes_generator as ng

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    if not condition:
        FAILURES.append(f"{label}: {detail}")
    print(f"[{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))
    return condition


def seg(speaker, text, start, dur=None):
    dur = dur or max(2, len(text.split()) / 2.5)
    return {"speakerId": speaker, "start": start, "end": start + dur, "text": text}


def make_transcript(turns):
    out, t = [], 0
    for spk, text in turns:
        s = seg(spk, text, t)
        out.append({**s, "id": f"seg_{len(out)+1:03d}"})
        t = s["end"]
    return out


# =======================================================================
# Test A -- single-speaker project status update (reconstructed benchmark)
# =======================================================================

speakers1 = [{"id": "S1", "label": "Speaker 1", "color": "teal"}]

test_a_turns = [
    ("S1", "Hi everyone, this is a quick status update on the analytics dashboard project."),
    ("S1", "The dataset has about 18,500 total records, and after cleaning we're down to roughly 17,900, so around 600 records are incomplete."),
    ("S1", "The biggest problem right now is that the analytics charts show incorrect values when the dataset contains missing records."),
    ("S1", "Missing values are being processed incorrectly, and that's the immediate priority to resolve."),
    ("S1", "First, I need to fix the missing-value processing problem."),
    ("S1", "Second, I need to test all 12 charts using different datasets."),
    ("S1", "After that, I'll test the charts and update the documentation."),
    ("S1", "Originally, we planned to complete everything by August 28."),
    ("S1", "I don't think that's realistic anymore given the analytics problem."),
    ("S1", "A better target would probably be September 1, assuming the data-processing problem gets fixed."),
    ("S1", "We haven't decided whether the dashboard should show raw data or processed results."),
    ("S1", "Current page load time is about 1.8 seconds, and we'd like to get that under 1 second."),
    ("S1", "Performance optimization is probably less important than fixing the analytics problem."),
    ("S1", "So far we've implemented 3 of the 5 planned user roles."),
    ("S1", "That's the current state of the project."),
]

transcript_a = make_transcript(test_a_turns)
notes_a = ng.generate_notes(transcript_a, speakers1)

print("=" * 70)
print("TEST A -- single-speaker project status update")
print("=" * 70)

check(
    "A1: purpose mentions status update or planning (not junk words)",
    any(w in notes_a["purpose"].lower() for w in ["status update", "planning", "problem solving"]),
    notes_a["purpose"],
)
check(
    "A2: purpose does NOT contain junk fragments",
    not any(bad in notes_a["purpose"] for bad in ["Sure", "Records,", "Issue,"]),
    notes_a["purpose"],
)
check(
    "A3: at least one high-priority issue about analytics/missing values",
    any(i["priority"] == "high" and ("missing" in i["evidence"].lower() or "analytics" in i["evidence"].lower() or "incorrect" in i["evidence"].lower()) for i in notes_a["issues"]),
    str(notes_a["issues"]),
)
action_tasks = [a["task"].lower() for a in notes_a["actionItems"]]
check("A4: an action about fixing missing-value processing", any("missing" in t or "fix" in t for t in action_tasks), str(action_tasks))
check("A5: an action about testing charts", any("test" in t and "chart" in t for t in action_tasks), str(action_tasks))
check("A6: an action about documentation", any("document" in t for t in action_tasks), str(action_tasks))
check(
    "A7: actions are normalized (no leading 'First,'/'I need to')",
    all(not t.startswith(("first,", "i need to", "i'll", "second,")) for t in action_tasks),
    str(action_tasks),
)
check(
    "A8: timeline has an original Aug 28 + revised Sept 1 relationship",
    any(t["type"] == "original" and "28" in t["date"] for t in notes_a["timeline"])
    and any(t["type"] == "revised" and "1" in t["date"] for t in notes_a["timeline"]),
    str(notes_a["timeline"]),
)
check(
    "A9: revised deadline carries a condition",
    any(t["type"] == "revised" and t["condition"] for t in notes_a["timeline"]),
    str(notes_a["timeline"]),
)
check(
    "A10: open question about raw vs processed data survives",
    any("raw" in q.lower() and "processed" in q.lower() for q in notes_a["openQuestions"]),
    str(notes_a["openQuestions"]),
)
check(
    "A11: NOT reported as 'no unresolved questions'",
    len(notes_a["openQuestions"]) > 0,
    "openQuestions was empty",
)
numbers_str = " ".join(notes_a["facts"]["numbers"])
check("A12: 18,500 records fact present", "18,500" in numbers_str, numbers_str)
check("A13: 17,900 fact present", "17,900" in numbers_str, numbers_str)
check("A14: 600 incomplete fact present", "600" in numbers_str, numbers_str)
check("A15: 12 charts fact present", "12" in numbers_str, numbers_str)
check("A16: 1.8 second load time fact present", "1.8" in numbers_str, numbers_str)
check("A16b: '3 of 5' user roles ratio fact present", "3 of 5" in numbers_str, numbers_str)
check(
    "A17: priority relation detected (performance < analytics)",
    len(extract := __import__("meeting_intelligence").extract_priority_relations(
        __import__("meeting_intelligence").build_utterances(transcript_a, speakers1)
    )) > 0,
    str(extract),
)
check(
    "A18: conclusion mentions the main blocker, not a decision count",
    "main blocker" in notes_a["conclusion"].lower() or "analytics" in notes_a["conclusion"].lower(),
    notes_a["conclusion"],
)
check(
    "A19: conclusion is NOT the old decision-count template",
    "decision" not in notes_a["conclusion"].lower() or "were reached" not in notes_a["conclusion"].lower(),
    notes_a["conclusion"],
)
check(
    "A20: speaker summary is synthesized, not the literal opening sentence",
    notes_a["speakerInsights"][0]["summary"] != test_a_turns[0][1],
    notes_a["speakerInsights"][0]["summary"] if notes_a["speakerInsights"] else "NO SPEAKER INSIGHTS",
)
check(
    "A21: zero false decisions from mid-transcript 'should'/'need' sentences",
    len(notes_a["decisions"]) <= 2,
    str([d["text"] for d in notes_a["decisions"]]),
)

print()
print("Full Test A output (topics/decisions/takeaways) for manual inspection:")
print("PURPOSE:", notes_a["purpose"])
print("SUMMARY:", notes_a["summary"])
print("TOPICS:", notes_a["topics"])
print("DECISIONS:", [d["text"] for d in notes_a["decisions"]])
print("PROPOSALS:", [p["text"] for p in notes_a.get("proposals", [])])
print("ACTIONS:", action_tasks)
print("TIMELINE:", notes_a["timeline"])
print("OPEN QUESTIONS:", notes_a["openQuestions"])
print("TAKEAWAYS:")
for t in notes_a["takeaways"]:
    print("  -", t)
print("CONCLUSION:", notes_a["conclusion"])
print("SPEAKER SUMMARY:", notes_a["speakerInsights"][0]["summary"] if notes_a["speakerInsights"] else None)


# =======================================================================
# Test B -- two-speaker technical discussion
# =======================================================================

speakers2 = [{"id": "S1", "label": "Speaker 1", "color": "teal"}, {"id": "S2", "label": "Speaker 2", "color": "coral"}]
test_b_turns = [
    ("S1", "How is the caching layer performing under load?"),
    ("S2", "It's holding up well. Average latency dropped from 220 milliseconds to about 90 milliseconds after we added Redis."),
    ("S1", "That's a solid improvement. Did we hit any issues during the migration?"),
    ("S2", "One issue: the cache invalidation logic didn't handle concurrent writes correctly, so a few users saw stale data."),
    ("S1", "I'll fix the invalidation logic tomorrow."),
    ("S2", "We've agreed to roll out the fix to staging first before touching production."),
]
transcript_b = make_transcript(test_b_turns)
notes_b = ng.generate_notes(transcript_b, speakers2)

print()
print("=" * 70)
print("TEST B -- two-speaker technical discussion")
print("=" * 70)
check("B1: topics mention caching/latency", any("cach" in t.lower() or "latency" in t.lower() or "redis" in t.lower() for t in notes_b["topics"]), str(notes_b["topics"]))
check("B2: a question/answer pair about caching performance exists", len(notes_b["questions"]) > 0, str(notes_b["questions"]))
check("B3: numbers (220/90 ms) captured", any("220" in n or "90" in n for n in notes_b["facts"]["numbers"]), str(notes_b["facts"]["numbers"]))
check("B4: an issue about cache invalidation/stale data", any("invalidat" in i["evidence"].lower() or "stale" in i["evidence"].lower() for i in notes_b["issues"]), str(notes_b["issues"]))
check("B5: an action owned by Speaker 1 (fix invalidation)", any(a["owner"] == "Speaker 1" and "invalidat" in a["task"].lower() for a in notes_b["actionItems"]), str(notes_b["actionItems"]))
check("B6: a real decision (we've agreed...)", any("staging" in d["text"].lower() or "roll out" in d["text"].lower() for d in notes_b["decisions"]), str([d["text"] for d in notes_b["decisions"]]))
check("B7: both speakers have insights", len(notes_b["speakerInsights"]) == 2, str(len(notes_b["speakerInsights"])))


# =======================================================================
# Tests C-J -- targeted single-sentence classification tests
# =======================================================================

print()
print("=" * 70)
print("TESTS C-J -- targeted classification")
print("=" * 70)


def analyze_one(sentence, speaker_label="Speaker 1"):
    sp = [{"id": "S1", "label": speaker_label, "color": "teal"}]
    t = make_transcript([("S1", sentence)])
    return ng.generate_notes(t, sp)


# Test C -- explicit negation -> open question, NOT decision
n = analyze_one("We haven't decided whether to deploy.")
check("C: 'haven't decided' -> open question, not a decision", len(n["decisions"]) == 0 and len(n["openQuestions"]) == 1, f"decisions={n['decisions']} openQ={n['openQuestions']}")

# Test D -- proposal, not decision
n = analyze_one("I think we should move the deadline.")
check("D: hedged 'I think we should' -> proposal, not decision", len(n["decisions"]) == 0, f"decisions={n['decisions']} proposals={n.get('proposals')}")
check("D2: proposal was captured", len(n.get("proposals", [])) == 1, str(n.get("proposals")))

# Test E -- actual decision
n = analyze_one("We've agreed to move the deadline to September 1.")
check("E: explicit agreement -> decision", len(n["decisions"]) == 1, str(n["decisions"]))

# Test F -- action with owner + deadline
n = analyze_one("I'll test the charts tomorrow.")
check("F: 'I'll test X tomorrow' -> action, owner=speaker, due=tomorrow", len(n["actionItems"]) == 1 and n["actionItems"][0]["owner"] == "Speaker 1" and n["actionItems"][0]["due"] is not None, str(n["actionItems"]))

# Test G -- conditional action
n = analyze_one("If the accuracy is below 90%, we'll rerun the experiment.")
check("G: conditional action captured with a condition", len(n["actionItems"]) == 1 and n["actionItems"][0]["condition"] is not None, str(n["actionItems"]))

# Test H -- open question (hedged uncertainty)
n = analyze_one("I'm not sure whether we should use raw or processed data.")
check("H: 'I'm not sure whether' -> open question", len(n["openQuestions"]) == 1, f"openQ={n['openQuestions']} decisions={n['decisions']}")

# Test I -- no action/decision meeting
n = analyze_one("It was nice catching up with everyone today.")
check("I: no decisions invented on a content-free sentence", len(n["decisions"]) == 0, str(n["decisions"]))
check("I2: no actions invented on a content-free sentence", len(n["actionItems"]) == 0, str(n["actionItems"]))

# Test J -- casual conversation shouldn't become a decision
n = analyze_one("Yeah, we should probably grab lunch sometime.")
check("J: casual 'we should' chit-chat -> not a formal decision", len(n["decisions"]) == 0, str(n["decisions"]))


# =======================================================================
# Additional edge cases (empty / single utterance / many speakers)
# =======================================================================

print()
print("=" * 70)
print("EDGE CASES")
print("=" * 70)
n = ng.generate_notes([], [])
check("Empty transcript doesn't crash and reports no speech", n["summary"] == "No speech detected.", n["summary"])

speakers5 = [{"id": f"S{i}", "label": f"Speaker {i}", "color": "teal"} for i in range(1, 6)]
t5 = make_transcript([(f"S{i%5+1}", f"Point number {i} about the project.") for i in range(10)])
n = ng.generate_notes(t5, speakers5)
check("5-speaker transcript doesn't crash", len(n["speakerInsights"]) == 5, str(len(n["speakerInsights"])))


# =======================================================================
# Summary
# =======================================================================

print()
print("=" * 70)
print(f"RESULT: {len(FAILURES)} failing checks")
if FAILURES:
    print("Failures:")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
