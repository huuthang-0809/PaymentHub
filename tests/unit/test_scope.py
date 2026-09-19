from shared_kernel import GlobalTxnKey, ReconScopeKey, Region
import pytest
def test_scope_upper_region() -> None:
    idr = ReconScopeKey(Region.ASIA, "asia-id", "ESPAY", "IDR")
    assert idr == ReconScopeKey("Asia", "asia-id", "ESPAY", "IDR")

@pytest.mark.parametrize(
    ("region", "tenant", "provider", "currency"),
    [
        ("EU", "eu-id", "ESPAY", "EUR"),
        ("ASIA", None, "ESPAY", "IDR"),
        ("INDIA", None, " ", "INR"),
    ],   
)
def test_invalid_scope(region: Region, tenant: str | None, provider: str, currency: str) -> None:
    with pytest.raises(ValueError):
        ReconScopeKey(region, tenant, provider, currency)

def test_scope_global_upper_region() -> None:
    idr = GlobalTxnKey(Region.ASIA, "asia-id", "same-id")
    assert idr == GlobalTxnKey("Asia", "asia-id", "same-id")


@pytest.mark.parametrize(
    ("region", "tenant", "txn_id"),
    [
        ("EU", "eu-id", "tx"),
        ("ASIA", None, "tx"),
        ("INDIA", None, " "),
    ],   
)
def test_invalid_global_scope(region: Region, tenant: str | None, txn_id: str) -> None:
    with pytest.raises(ValueError):
        GlobalTxnKey(region, tenant, txn_id)