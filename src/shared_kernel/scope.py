from dataclasses import dataclass
from typing import Optional
from .enums import Region

def _validate_region_tenant(region: Region, tenant_id: str | None) -> None:
    if region == Region.ASIA and not tenant_id:
        raise ValueError("tenant_id is strictly required for ASIA.")

@dataclass(frozen=True)
class GlobalTxnKey:
    """
    Global unique identity key for a transaction across regions.
    """

    region: Region
    tenant_id: Optional[str]
    tnx_id: str

    def __post_init__(self) -> None:
        if isinstance(self.region, str) and not isinstance(self.region, Region):
            try:
                clean_region = self.region.upper().strip()
                object.__setattr__(self, "region", Region(clean_region))
            except ValueError:
                raise ValueError(f"Unsupported region: {self.region}")
        _validate_region_tenant(self.region, self.tenant_id)
        if not self.tnx_id or not self.tnx_id.strip():
            raise ValueError("tnx_id cannot be empty")

@dataclass(frozen=True)
class ReconScopeKey:
    """
    Scope restriction key for reconcilation matching operation.
    """
    region: Region
    tenant_id: Optional[str]
    provider: str
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.region, str) and not isinstance(self.region, Region):
            try:
                clean_region = self.region.upper().strip()
                object.__setattr__(self, "region", Region(clean_region))
            except ValueError:
                raise ValueError(f"Unsupported region: {self.region}")
        _validate_region_tenant(self.region, self.tenant_id)
        if not self.provider or not self.provider.strip():
            raise ValueError("provider cannot be empty")
        if not self.currency or not self.currency.strip():
            raise ValueError("currency cannot be empty")

        object.__setattr__(self, "provider", self.provider.upper().strip())
        object.__setattr__(self, "currency", self.currency.upper().strip())