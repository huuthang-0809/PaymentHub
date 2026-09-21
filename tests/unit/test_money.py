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

@pytest.mark.parametrize(
    ("minor_input", "currency", "expected_amount"),
    [
        (1000000000000000, "IDR", "1000000000000000"),
        (1, "PHP", "0.01"),
        (999, "INR", "9.99")
    ],
)
def test_money_from_minor(minor_input: int, currency: str, expected_amount: str) -> None:
    # Khởi tạo đối tượng từ minor
    money = Money.from_minor(minor_input, currency)
    
    # Kiểm tra amount và currency xem có tạo ra đúng như kỳ vọng không
    assert money.amount == Decimal(expected_amount)
    assert money.currency == currency

@pytest.mark.parametrize("value", [1.2, True, "NaN", "Infinity", object()])
def test_reject_unsafe_amount(value:object) -> None:
    with pytest.raises((TypeError, ValueError)):
        Money(value, "PHP")

def test_precision_anf_currency() -> None:
    with pytest.raises(ValueError):
        Money("1.001", "PHP")
    with pytest.raises(ValueError):
        Money("1", "XYZ")
    with pytest.raises(TypeError):
        Money(1.0, "PHP")
    with pytest.raises(TypeError):
        Money.to_minor(1, 2)

def test_value_object_invariants() -> None:
    with pytest.raises(ValueError):
        Money(1, "XYZ")
    with pytest.raises(ValueError):
        Money(Decimal("1"), -1)



def test_currency_normalization() -> None:
    money = Money("10.5", " usd ")
    assert money.currency == "USD"
    assert money.amount == Decimal("10.5")
    
    # Test from_minor
    money_from_minor = Money.from_minor(1050, " usd ")
    assert money_from_minor.currency == "USD"
    assert money_from_minor.amount == Decimal("10.5")

def test_missing_or_empty_currency() -> None:
    """Test nhánh `if self.currency else ""` khi currency là None hoặc chuỗi rỗng"""
    with pytest.raises(ValueError, match="Unsupported or unknown currency"):
        Money("10", None)
    with pytest.raises(ValueError, match="Unsupported or unknown currency"):
        Money("10", "")
        
    with pytest.raises(ValueError, match="Unsupported or unknown currency"):
        Money.from_minor(100, None)
    with pytest.raises(ValueError, match="Unsupported or unknown currency"):
        Money.from_minor(100, "")

def test_from_minor_exceptions() -> None:
    with pytest.raises(TypeError, match="amount_minor must be an integer"):
        Money.from_minor(1.5, "USD") # type: ignore
        
    with pytest.raises(ValueError, match="Unsupported or unknown currency: XYZ"):
        Money.from_minor(100, "XYZ")
