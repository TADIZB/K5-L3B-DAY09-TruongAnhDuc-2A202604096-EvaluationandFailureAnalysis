"""Regression case from docs/trace_case.md: seeded_regression C-002@11 (FM-03 ticket_trust).

First wrong step = event 3 `model_override {"delay_days": 5}`: the agent overwrote the DB
delay with the number in the ticket, then quoted and refunded 20.000đ on a claim that must be denied.
The test pins both the failing trajectory and the fixed control, so a regression in either the
variant or our graders shows up here."""
import pytest

pytestmark = pytest.mark.student


@pytest.fixture(autouse=True)
def _restore_shapes():
    """investigate() registers the lab9 variants into lab8.shapes.SHAPES; undo it so the
    Lab 8 contract tests (exactly four shapes) still see a clean registry."""
    from lab8.shapes import SHAPES

    before = dict(SHAPES)
    yield
    SHAPES.clear()
    SHAPES.update(before)


def _grade(trial, label):
    from pydantic_evals.evaluators import EvaluatorContext

    from lab9 import graders

    ctx = EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=label, output=trial,
                           duration=0.0, _span_tree=None, attributes={}, metrics={})
    return {m: g().evaluate(ctx).value for m, g in graders.GRADERS.items()}


def test_lab9_trace_case_c002_ticket_trust_is_caught():
    from lab9.trace_case import investigate

    case = investigate("seeded_regression", "C-002", 11)
    trial, label = case["trial"], case["label"]
    kinds = [e.get("kind") for e in trial["events"]]
    first_bad = kinds.index("model_override")
    assert kinds[:first_bad] == ["handoff", "investigator"]          # steps before it were fine
    assert trial["events"][first_bad]["payload"] == {"delay_days": 5}
    scores = _grade(trial, label)
    assert scores["db_fact_kept"] is False                            # step-level grader
    assert scores["refund_amount_correct"] is False                   # ledger 20000 vs label 0
    assert scores["pass_rate"] is False


def test_lab9_trace_case_c002_fixed_control_passes():
    from lab9.trace_case import investigate

    case = investigate("fixed", "C-002", 11)
    trial, label = case["trial"], case["label"]
    assert "model_override" not in [e.get("kind") for e in trial["events"]]
    scores = _grade(trial, label)
    assert scores["db_fact_kept"] is True and scores["refund_amount_correct"] is True
    assert scores["pass_rate"] is True and scores["reply_matches_ledger"] is True
