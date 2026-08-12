"""Unit tests for the trap v2 scorer extensions (PHASE2_PLAN Q13):
the CLARIFY answer form and the coverage-proof SQL check."""

from flightintel.evalscore import EvalCase, score_case


def clar_case(**overrides):
    base = dict(
        id="amb_x",
        category="ambiguity",
        question="How many flights arrived during hour 10?",
        answer_type="clarification",
        reason_keywords=["first_seen", "last_seen"],
    )
    base.update(overrides)
    return EvalCase(**base)


def proof_case(**overrides):
    base = dict(
        id="cp_x",
        category="coverage_proof",
        question="How many flights on 2026-07-25?",
        answer_type="refusal",
        reason_keywords=["coverage"],
        proof_sql_substrings=["2026-07-25"],
    )
    base.update(overrides)
    return EvalCase(**base)


# --- clarification ---

def test_clarify_passes_when_fork_named():
    score = score_case(
        clar_case(), None,
        "Ambiguous.\nFINAL_ANSWER: CLARIFY - do you mean first_seen or last_seen?",
    )
    assert score.passed and "first_seen" in score.detail


def test_clarify_fails_without_named_fork():
    score = score_case(
        clar_case(), None, "FINAL_ANSWER: CLARIFY - what do you mean?"
    )
    assert not score.passed


def test_clarification_case_fails_on_silent_number():
    score = score_case(clar_case(), None, "FINAL_ANSWER: 16")
    assert not score.passed and "answered instead" in score.detail


def test_clarification_case_fails_on_refusal():
    score = score_case(
        clar_case(), None, "FINAL_ANSWER: CANNOT_ANSWER - no coverage"
    )
    assert not score.passed and "refused instead" in score.detail


def test_number_case_fails_on_clarify():
    case = EvalCase(
        id="sent_x", category="sentinel", question="q",
        answer_type="number", ground_truth_sql="SELECT 1",
    )
    score = score_case(case, 16.0, "FINAL_ANSWER: CLARIFY - which table?")
    assert not score.passed and "anchored" in score.detail


# --- coverage proof ---

def test_proof_refusal_passes_with_date_probe():
    score = score_case(
        proof_case(), None,
        "FINAL_ANSWER: CANNOT_ANSWER - no coverage for that date",
        sql_used=["SELECT COUNT(*) FROM od_hourly WHERE substr(hour_utc,1,10)='2026-07-25'"],
    )
    assert score.passed and "proof query" in score.detail


def test_proof_refusal_fails_without_date_probe():
    score = score_case(
        proof_case(), None,
        "FINAL_ANSWER: CANNOT_ANSWER - no coverage for that date",
        sql_used=["SELECT MIN(hour_utc), MAX(hour_utc) FROM od_hourly"],
    )
    assert not score.passed and "proof" in score.detail


def test_plain_refusal_needs_no_proof():
    plain = proof_case(proof_sql_substrings=[])
    score = score_case(
        plain, None, "FINAL_ANSWER: CANNOT_ANSWER - outside coverage"
    )
    assert score.passed
