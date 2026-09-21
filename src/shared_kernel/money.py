from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from .enums import AmountStatus

CURRENCY_EXPONENTS = {
    "VND": 0,
    "IDR": 0,
    "INR": 2,
    "PHP": 2,
    "USD": 2
}

@dataclass(frozen = True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.amount, float):
            raise TypeError(
                "Strictly prohibited floating-point values for Money"
            )

        try:
            parsed_amount = (
                self.amount
                if isinstance(self.amount, Decimal)
                else Decimal(str(self.amount).strip())
            )
        except (InvalidOperation, ValueError):
            raise ValueError(f"Cannot parse amount: {self.amount}")

        if not parsed_amount.is_finite():
            raise ValueError(
                f"Amount must be a finite number but got {parsed_amount}"
            )

        currency_upper = (
            str(self.currency).strip().upper()
            if self.currency
            else ""
        )

        if currency_upper not in CURRENCY_EXPONENTS:
            raise ValueError(
                f"Unsupported or unknown currency: {currency_upper}"
            )

        object.__setattr__(self, "amount", parsed_amount)
        object.__setattr__(self, "currency", currency_upper)

        self.to_minor()

    @classmethod
    def from_minor(cls, amount_minor:int, currency: str) -> "Money":
        """
        Create a Money instance from minor units.
        """
        if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
            raise TypeError("amount_minor must be an integer")
        currency_upper = currency.strip().upper() if currency else ""
        if currency_upper not in CURRENCY_EXPONENTS:
            raise ValueError(f"Unsupported or unknown currency: {currency_upper}")

        exponent = CURRENCY_EXPONENTS[currency_upper]
        amount = Decimal(amount_minor) / (Decimal(10) ** exponent)
        return cls(amount = amount, currency = currency_upper)

    def to_minor(self) -> int:
        exponent = CURRENCY_EXPONENTS.get(self.currency, 2)
        scaled = self.amount * (Decimal(10) ** exponent)

        if scaled != scaled.to_integral_value(): #minor unit has no decimal part
            raise ValueError(f"The amount {self.amount} exceeds the permitted precision for {self.currency}")

        return int(scaled)