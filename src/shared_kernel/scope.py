from dataclasses import dataclass
from typing import Optional
from .enums import Region

@dataclass(frozen=True)
class GlobalTxnKey:
    """
    Global unique identity key for a transaction across regions.
    """

    region: Region
    tenant_id: Optional[str]
    tnx_id: str

    def __pos_init__(self) -> None:
        if not self.tnx_id or not self.tnx_id.strip():
            raise ValueError("tnx_id cannot be empty")

        if self.region == Region.ASIA and not self.tenant_id:
            raise ValueError("tenant_id is strictly required for ASIA.")

@dataclass(frozen=True)
class ReconScopeKey:
    """
    Scope restriction key for reconcilation matching operation.
    """
    region: Region
    tenant_id: Optional[str]
    provider: str
    currency: str

    def __pos_init__(self) -> None:
        if not self.provider or not self.provider.strip():
            raise ValueError("provider cannot be empty")
        if not self.currency or not self.currency.strip():
            raise ValueError("currency cannot be empty")

        object.__setattr__(self, "provider", self.provider.upper().strip())
        object.__setattr__(self, "currency", self.currency.upper().strip())