from flightintel.evalscore import (
    EvalCase,
    extract_final_line,
    parse_number,
    score_case,
)


def _number_case(**kw):
    defaults = dict(
        id="c1",
        category="grain",
        question="q",
        answer_type="number",
        ground_truth_sql="SELECT 1",
    )
    defaults.update(kw)
    return EvalCase(**defaults)


def _refusal_case(**kw):
    defaults = dict(
        id="c2",
        category="refusal",
        question="q",
        answer_type="refusal",
        reason_keywords=["captur", "coverage"],
    )
    defaults.update(kw)
    return EvalCase(**defaults)


def test_extract_final_line_takes_last_match():
    text = "thinking...\nFINAL_ANSWER: 5\nwait no\nFINAL_ANSWER: 7"
    assert extract_final_line(text) == "7"


def test_parse_number_handles_commas_and_floats():
    assert parse_number("1,301 flights") == 1301
    assert parse_number("about 3.5") == 3.5
    assert parse_number("none") is None


def test_correct_number_passes():
    s = score_case(_number_case(), 22, "blah\nFINAL_ANSWER: 22")
    assert s.passed and s.extracted_number == 22


def test_wrong_number_fails_with_detail():
    s = score_case(_number_case(), 22, "FINAL_ANSWER: 485")
    assert not s.passed
    assert "expected 22" in s.detail


def test_tolerance_applies_to_floats():
    s = score_case(_number_case(tolerance=0.1), 3.14, "FINAL_ANSWER: 3.1")
    assert s.passed


def test_refusing_an_answerable_question_fails():
    s = score_case(_number_case(), 22, "FINAL_ANSWER: CANNOT_ANSWER - no idea")
    assert not s.passed
    assert "refused" in s.detail


def test_missing_final_line_fails():
    s = score_case(_number_case(), 22, "The answer is twenty-two.")
    assert not s.passed
    assert "no FINAL_ANSWER" in s.detail


def test_refusal_with_correct_reason_passes():
    s = score_case(
        _refusal_case(),
        None,
        "FINAL_ANSWER: CANNOT_ANSWER - that date was not captured",
    )
    assert s.passed


def test_refusal_with_wrong_reason_fails():
    s = score_case(
        _refusal_case(),
        None,
        "FINAL_ANSWER: CANNOT_ANSWER - the query timed out",
    )
    assert not s.passed


def test_number_given_to_refusal_case_fails():
    s = score_case(_refusal_case(), None, "FINAL_ANSWER: 0")
    assert not s.passed
    assert "answered instead" in s.detail
