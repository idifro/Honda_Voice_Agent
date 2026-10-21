"""Demo EMI (domain/finance.py) and the ease score (domain/recommend.py)."""

import pytest

from honda_voice.domain.finance import Scheme, emi, examples
from honda_voice.domain.recommend import ease_score


@pytest.mark.parametrize(
    ("principal", "rate", "months", "expected"),
    [
        (100_000, 10, 12, 8_792),  # textbook figure: 8,791.59
        (500_000, 9, 60, 10_379),  # common bank-calculator example: 10,379.18
        (1_000_000, 8.5, 240, 8_678),  # home-loan table: 8,678.23
        (60_000, 0, 12, 5_000),  # zero interest: equal parts
    ],
)
def test_emi_matches_published_figures(principal, rate, months, expected):
    assert emi(principal, rate, months) == expected


def test_emi_rejects_bad_input():
    with pytest.raises(ValueError):
        emi(10_000, 10, 0)


def test_examples_use_min_down_payment_per_tenure():
    plan = Scheme("demo-standard", "Demo plan, standard", 11.5, (24, 36), 15, 1499)
    out = examples(plan, 91_679)  # Activa 125 DLX, Delhi ex-showroom
    assert [e.tenure_months for e in out] == [24, 36]
    first = out[0]
    assert first.down_payment_inr == 13_752  # 15 % of 91,679 = 13,751.85, rounded up
    assert first.loan_inr == 91_679 - 13_752
    assert first.emi_inr == emi(first.loan_inr, 11.5, 24)
    assert out[1].emi_inr < first.emi_inr  # longer tenure, smaller instalment


@pytest.mark.parametrize(
    ("kerb", "seat", "expected"),
    [
        (106, 764, 5),  # Activa 110
        (124, 796, 4),  # CB125 Hornet
        (148, 810, 3),  # NX200
        (199, 830, 1),  # NX500: heavy and tall seat
        (393, 745, 1),  # Gold Wing
        (None, 770, 4),  # no weight: seat alone
    ],
)
def test_ease_score(kerb, seat, expected):
    assert ease_score(kerb, seat) == expected


def test_ease_score_needs_something():
    with pytest.raises(ValueError):
        ease_score(None, None)
