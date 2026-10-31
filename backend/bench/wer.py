"""Minimal word-error-rate calculator, stdlib only (no jiwer dependency).

Standard Levenshtein edit distance over whitespace-tokenized words,
space-optimized to two DP rows so long (1-2 hour meeting) transcripts
don't blow up memory the way a full n*m matrix would.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


def _normalize_words(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^\w\s']", " ", text)  # strip punctuation, keep apostrophes
    return text.split()


@dataclass
class WerResult:
    wer: float
    substitutions: int
    deletions: int
    insertions: int
    ref_word_count: int


def word_error_rate(reference: str, hypothesis: str) -> WerResult:
    ref = _normalize_words(reference)
    hyp = _normalize_words(hypothesis)
    n, m = len(ref), len(hyp)

    if n == 0:
        return WerResult(wer=float(m > 0), substitutions=0, deletions=0, insertions=m, ref_word_count=0)

    # dp[j] = (cost, subs, dels, ins) for aligning ref[:i] with hyp[:j]
    dp = [(j, 0, 0, j) for j in range(m + 1)]  # row i=0: all insertions
    for i in range(1, n + 1):
        prev_row = dp
        dp = [(i, 0, i, 0)] + [(0, 0, 0, 0)] * m  # col j=0: all deletions
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[j] = prev_row[j - 1]
            else:
                sub_cost, sub_s, sub_d, sub_i = prev_row[j - 1]
                del_cost, del_s, del_d, del_i = prev_row[j]
                ins_cost, ins_s, ins_d, ins_i = dp[j - 1]
                best = min(
                    (sub_cost + 1, sub_s + 1, sub_d, sub_i),
                    (del_cost + 1, del_s, del_d + 1, del_i),
                    (ins_cost + 1, ins_s, ins_d, ins_i + 1),
                    key=lambda t: t[0],
                )
                dp[j] = best

    cost, subs, dels, ins = dp[m]
    return WerResult(wer=round(cost / n, 4), substitutions=subs, deletions=dels, insertions=ins, ref_word_count=n)
