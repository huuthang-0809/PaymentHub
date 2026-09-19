from decimal import Decimal
import pytest
from shared_kernel.money import Money

@pytest.mark.parametrize(
    ("value", "currency", "minor"),
    [
        ("1000000000000000", "IDR", 1000000000000000),
        ("0.01", "PHP", 1),
        (Decimal("9.99"), "INR", 999)
    ],
)
def test_money(value: object, currency: str, minor: int) -> None:
    assert Money(value, currency).to_minor() == minor