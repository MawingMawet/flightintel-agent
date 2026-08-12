"""Eval case schema, answer extraction, and deterministic scoring.

The runner appends EVAL_SUFFIX to each question so the agent ends with a
machine-readable FINAL_ANSWER line (PHASE1_PLAN Q9); scoring never guesses
numbers out of prose. Refusal cases pass only when the agent declines AND
the FINAL_ANSWER line names an accepted reason (Q5).
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

# Neutral wording on purpose: run 2 (2026-08-10) showed that announcing
# "for automated evaluation" invited extra self-verification rounds.
# A third CLARIFY form was tried for trap v2 and reverted the same day
# after two wordings both collapsed the loop into step-limit aborts:
# with a clarify option present, the model investigates every reading
# by SQL instead of choosing an answer form (incident + stopping rule
# in PHASE2_PLAN Q13). The clarification scorer path below stays for a
# stronger-model retest; this suffix stays the two-form phase 1
# instrument. Suffix changes re-baseline every run under them.
EVAL_SUFFIX = (
    "\n\nEnd your reply with exactly one line:\n"
    "FINAL_ANSWER: <number>\n"
    "if the question is answerable with a number, or\n"
    "FINAL_ANSWER: CANNOT_ANSWER - <short reason>\n"
    "if the data cannot answer it."
)

_FINAL_RE = re.compile(r"FINAL_ANSWER:\s*(.+)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")


class EvalCase(BaseModel):
    id: str
    category: str
    question: str
    answer_type: Literal["number", "refusal", "clarification"]
    ground_truth_sql: str | None = None
    tolerance: float = 0.0
    reason_keywords: list[str] = Field(default_factory=list)
    # coverage_proof (Q13): a refusal passes only if some executed SQL
    # contains one of these substrings (the conclusive per-date probe).
    proof_sql_substrings: list[str] = Field(default_factory=list)
    notes: str | None = None


class CaseScore(BaseModel):
    passed: bool
    detail: str
    final_line: str | None = None
    extracted_number: float | None = None


def extract_final_line(answer: str) -> str | None:
    matches = _FINAL_RE.findall(answer)
    return matches[-1].strip() if matches else None


def parse_number(text: str) -> float | None:
    m = _NUMBER_RE.search(text.replace(",", ""))
    return float(m.group()) if m else None


def score_case(
    case: EvalCase,
    expected: float | None,
    answer: str,
    sql_used: list[str] | None = None,
) -> CaseScore:
    final = extract_final_line(answer)
    if final is None:
        return CaseScore(passed=False, detail="no FINAL_ANSWER line in reply")

    refused = "CANNOT_ANSWER" in final.upper()
    clarified = final.upper().startswith("CLARIFY")

    if case.answer_type == "clarification":
        if not clarified:
            return CaseScore(
                passed=False,
                detail="refused instead of asking for clarification"
                if refused else "answered instead of asking for clarification",
                final_line=final,
                extracted_number=None if refused else parse_number(final),
            )
        reason = final.casefold()
        hit = next(
            (k for k in case.reason_keywords if k.casefold() in reason), None
        )
        if hit is None:
            return CaseScore(
                passed=False,
                detail="clarified but named no accepted fork "
                f"(wanted one of {case.reason_keywords})",
                final_line=final,
            )
        return CaseScore(
            passed=True,
            detail=f"asked for clarification naming '{hit}'",
            final_line=final,
        )

    if case.answer_type == "number":
        if refused:
            return CaseScore(
                passed=False,
                detail="refused an answerable question",
                final_line=final,
            )
        if clarified:
            return CaseScore(
                passed=False,
                detail="asked for clarification on an anchored question",
                final_line=final,
            )
        got = parse_number(final)
        if got is None:
            return CaseScore(
                passed=False,
                detail="no number in FINAL_ANSWER line",
                final_line=final,
            )
        ok = expected is not None and abs(got - expected) <= case.tolerance
        return CaseScore(
            passed=ok,
            detail="exact match" if ok else f"expected {expected}, got {got}",
            final_line=final,
            extracted_number=got,
        )

    # refusal case
    if not refused:
        return CaseScore(
            passed=False,
            detail="clarified instead of refusing"
            if clarified else "answered instead of refusing",
            final_line=final,
            extracted_number=None if clarified else parse_number(final),
        )
    reason = final.casefold()
    hit = next((k for k in case.reason_keywords if k.casefold() in reason), None)
    if hit is None:
        return CaseScore(
            passed=False,
            detail="refused but named no accepted reason "
            f"(wanted one of {case.reason_keywords})",
            final_line=final,
        )
    if case.proof_sql_substrings:
        executed = " ".join(sql_used or []).casefold()
        proof = next(
            (s for s in case.proof_sql_substrings if s.casefold() in executed),
            None,
        )
        if proof is None:
            return CaseScore(
                passed=False,
                detail="refused without running a per-date proof query "
                f"(no executed SQL contains any of {case.proof_sql_substrings})",
                final_line=final,
            )
        return CaseScore(
            passed=True,
            detail=f"refused with reason '{hit}' and proof query on '{proof}'",
            final_line=final,
        )
    return CaseScore(
        passed=True, detail=f"refused with reason keyword '{hit}'", final_line=final
    )
