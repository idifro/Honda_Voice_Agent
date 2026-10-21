"""Demo EMI (SPEC §20.4). Pure: a plan + an ex-showroom price -> monthly instalment. Spoken only from tool
output, like prices (rule 4); the plans are demo data, never a real lender's offer."""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class Scheme:
    id: str
    name: str
    interest_rate_pct: float
    tenure_months: tuple[int, ...]
    min_down_payment_pct: float
    processing_fee_inr: int = 0


@dataclass(frozen=True)
class EmiExample:
    scheme_id: str
    scheme_name: str
    price_inr: int
    down_payment_inr: int
    loan_inr: int
    tenure_months: int
    interest_rate_pct: float
    emi_inr: int
    processing_fee_inr: int


def emi(principal: int, annual_rate_pct: float, months: int) -> int:
    """Standard reducing-balance EMI, rounded to the rupee: P·r·(1+r)^n / ((1+r)^n − 1), r = monthly rate."""
    if principal < 0 or months <= 0:
        raise ValueError("principal must be >= 0 and months > 0")
    p = Decimal(principal)
    r = Decimal(str(annual_rate_pct)) / Decimal(1200)
    if r == 0:
        value = p / months
    else:
        growth = (1 + r) ** months
        value = p * r * growth / (growth - 1)
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def examples(scheme: Scheme, price_inr: int) -> list[EmiExample]:
    """One example per tenure, with the plan's minimum down payment (rounded up to the rupee)."""
    share = Decimal(str(scheme.min_down_payment_pct)) / 100
    down = int((Decimal(price_inr) * share).to_integral_value(ROUND_CEILING))
    loan = price_inr - down
    return [
        EmiExample(
            scheme_id=scheme.id,
            scheme_name=scheme.name,
            price_inr=price_inr,
            down_payment_inr=down,
            loan_inr=loan,
            tenure_months=n,
            interest_rate_pct=scheme.interest_rate_pct,
            emi_inr=emi(loan, scheme.interest_rate_pct, n),
            processing_fee_inr=scheme.processing_fee_inr,
        )
        for n in scheme.tenure_months
    ]
