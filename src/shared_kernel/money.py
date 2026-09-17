from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from .enums import AmountStatus, CURRENCY_EXPONENTS

@dataclass(frozen = True)
class Money:
    amount: Decimal
    currency: str
    status: AmountStatus = field(default = AmountStatus.VALID)

    def __post_init__(self):
        if isinstance(self.amount, float):
            raise TypeError("Strictly prohibited float")

        if not isinstance(self.amount, Decimal):
            try:
                object.__setattr__(self, 'amount', Decimal(str(self.amount).strip()))
            except InvalidOperation:
                raise ValueError(f"Can not parse amount: {self.amount}")

        object.__setattr__(self, 'currency', str(self.currency).strip().upper())

    def to_minor(self) -> int:
        exponent = CURRENCY_EXPONENTS.get(self.currency, 2)
        scaled = self.amount * (Decimal(10) ** exponent)

        if scaled != self.to_integral_value(): #minor unit has no decimal part
            raise ValueError(f"The amount {self.amount} exceeds the permitted precision for {self.currency}")

        return int(scaled)