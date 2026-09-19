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

    def __post_init__(self):
        if isinstance(self.amount, float):
            raise TypeError("Strictly prohibited floating-point values for Money")

        if not isinstance(self.amount, Decimal):
            try:
                object.__setattr__(self, 'amount', Decimal(str(self.amount).strip()))
            except InvalidOperation:
                raise ValueError(f"Can not parse amount: {self.amount}")

        currency_upper = str(self.currency).strip().upper() if self.currency else ""
        object.__setattr__(self, 'currency', currency_upper)

        if currency_upper not in  CURRENCY_EXPONENTS:
            raise ValueError(f"Unsupported or unknow currency: {currency_upper}")

    @classmethod
    def from_minor(cls, amount_minor:int, currency: str) -> "Money":
        """
        Create a Money instance from minor units.
        """
        if isinstance(amount_minor, float):
            raise TypeError("Strictly prohibited floating-point values for Money")
        currency_upper = currency.strip().upper() if currency else ""
        if currency_upper not in CURRENCY_EXPONENTS:
            raise ValueError(f"Unsupported or unknow currency: {currency_upper}")

        exponent = CURRENCY_EXPONENTS[currency_upper]
        amount = Decimal(amount_minor) / (Decimal(10) ** exponent)
        return cls(amount = amount, currency = currency_upper)

    def to_minor(self) -> int:
        exponent = CURRENCY_EXPONENTS.get(self.currency, 2)
        scaled = self.amount * (Decimal(10) ** exponent)

        if scaled != scaled.to_integral_value(): #minor unit has no decimal part
            raise ValueError(f"The amount {self.amount} exceeds the permitted precision for {self.currency}")

        return int(scaled)

    def add(self, other: "Money") -> "Money":
        """Adds 2 money instances of the same currency"""
        if self.currency != other.currency:
            raise ValueError(f"Cannot add differnce currencies: {self.currency} and {other.currency}")

    def subtract(self, other: "Money") -> "Money":
        """Subtract 2 money instances"""
        if self.currency != other.currency:
            raise ValueError(f"Cannot subtract differnce currencies: {self.currency} and {other.currency}")