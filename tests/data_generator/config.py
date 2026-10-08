from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache
from zoneinfo import ZoneInfo

from canonical.adapters.asia import AsiaMapping
from canonical.adapters.vn import VNMapping
from shared_kernel import Money, Region, TxnType
from shared_kernel.enums import Instrument


DATABASE_VN = "paymenthub_vn"
DATABASE_ASIA_ID = "paymenthub_asia_id"
DATABASE_ASIA_PH = "paymenthub_asia_ph"
DATABASE_INDIA = "paymenthub_india"
COLLECTION = "payment_transactions"

STATEMENT_COLUMNS = (
    "provider",
    "region",
    "tenant_id",
    "provider_txn_id",
    "provider_ref_id",
    "merchant_ref",
    "merchant_id",
    "amount_minor",
    "currency",
    "status",
    "txn_type",
    "transaction_time",
)

MUTATION_CYCLE = (
    "exact-match",
    "amount-mismatch",
    "currency-mismatch",
    "missing-in-provider",
    "duplicate",
    "fuzzy-match",
    "not-reconcilable",
    "invalid-data",
)

VN_MAPPING = VNMapping(
    instrument_by_method={"CARD": Instrument.CARD},
    card_link_methods=frozenset({"LINKCARD"}),
)
ASIA_MAPPING = AsiaMapping(
    instrument_code={
        "card": Instrument.CARD,
        "qr": Instrument.QR,
        "virtual-account": Instrument.VIRTUAL_ACCOUNT,
    },
    synthetic_flag_value={"synthetic": True},
)


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    seed: int = 20260923
    records_per_region: int = 5_000

    def __post_init__(self) -> None:
        if self.records_per_region <= 0:
            raise ValueError("records_per_region must be positive")
        try:
            datetime.strptime(str(self.seed), "%Y%m%d")
        except ValueError as exc:
            raise ValueError("seed must use YYYYMMDD format") from exc

    @property
    def batch_id(self) -> str:
        return f"seed-{self.seed}"

@dataclass(frozen=True, slots=True)
class ScopeSpec:
    database: str
    filename: str
    region: Region
    tenant_id: str | None
    provider: str
    currency: str
    slug: str
    business_timezone: str


@dataclass(frozen=True, slots=True)
class BaseCase:
    scope: ScopeSpec
    index: int
    mutation: str
    case_id: str
    system_txn_id: str
    provider_txn_id: str
    provider_ref_id: str | None
    merchant_ref: str | None
    merchant_id: str
    amount_minor: int
    txn_type: TxnType
    transaction_time: datetime


@dataclass(frozen=True, slots=True)
class ProviderFixtureRow:
    fixture_id: str
    case_id: str
    values: dict[str, object]


def scope_specs(config: GeneratorConfig) -> tuple[ScopeSpec, ...]:
    return (
        ScopeSpec(
            DATABASE_VN,
            f"onepay_{config.seed}.csv",
            Region.VN,
            None,
            "ONEPAY",
            "VND",
            "vn",
            "Asia/Ho_Chi_Minh",
        ),
        ScopeSpec(
            DATABASE_ASIA_ID,
            f"espay_asia-id_{config.seed}.csv",
            Region.ASIA,
            "asia-id",
            "ESPAY",
            "IDR",
            "asia-id",
            "Asia/Jakarta",
        ),
        ScopeSpec(
            DATABASE_ASIA_PH,
            f"2c2p_asia-ph_{config.seed}.csv",
            Region.ASIA,
            "asia-ph",
            "2C2P",
            "PHP",
            "asia-ph",
            "Asia/Manila",
        ),
        ScopeSpec(
            DATABASE_INDIA,
            f"razorpay_{config.seed}.csv",
            Region.INDIA,
            None,
            "RAZORPAY",
            "INR",
            "india",
            "Asia/Kolkata",
        ),
    )


def scope_document_count(config: GeneratorConfig, scope: ScopeSpec) -> int:
    if scope.region is not Region.ASIA:
        return config.records_per_region
    if scope.tenant_id == "asia-id":
        return (config.records_per_region + 1) // 2
    return config.records_per_region // 2


@lru_cache(maxsize=None)
def scope_period(config: GeneratorConfig, scope: ScopeSpec) -> tuple[datetime, datetime]:
    business_date = datetime.strptime(str(config.seed), "%Y%m%d").date()
    timezone = ZoneInfo(scope.business_timezone)
    start = datetime.combine(
        business_date - timedelta(days=1), time(2), timezone
    ).astimezone(UTC)
    end = datetime.combine(business_date, time(2), timezone).astimezone(UTC)
    return start, end


def base_case(config: GeneratorConfig, scope: ScopeSpec, index: int) -> BaseCase:
    mutation = MUTATION_CYCLE[index % len(MUTATION_CYCLE)]
    cycle = index // len(MUTATION_CYCLE)
    system_txn_id = f"{scope.slug}-{config.seed}-{index:06d}"
    provider_txn_id = f"{scope.provider.lower()}-{config.seed}-{index:06d}"
    txn_type = TxnType.PAYMENT
    if mutation == "not-reconcilable":
        txn_type = (
            TxnType.TOKENIZATION
            if scope.region is Region.INDIA
            else TxnType.CARD_LINK
        )

    # Each cycle contains one refund linked to its payment at position 0.
    if mutation == "duplicate":
        txn_type = TxnType.REFUND
        # Asia identifies refunds by PaymentRefId, so its ExternalTxnId mirrors
        # the parent. VN and India keep a distinct provider transaction ID.
        if scope.region is Region.ASIA:
            parent_index = cycle * len(MUTATION_CYCLE)
            provider_txn_id = (
                f"{scope.provider.lower()}-{config.seed}-{parent_index:06d}"
            )

    start, end = scope_period(config, scope)
    count = scope_document_count(config, scope)
    period_microseconds = int((end - start).total_seconds() * 1_000_000)
    transaction_time = start + timedelta(
        microseconds=index * period_microseconds // count
    )

    return BaseCase(
        scope=scope,
        index=index,
        mutation=mutation,
        case_id=f"{mutation}-{cycle:04d}",
        system_txn_id=system_txn_id,
        provider_txn_id=provider_txn_id,
        provider_ref_id=(
            f"{scope.slug}-ref-{config.seed}-{index:06d}"
            if scope.region is Region.ASIA
            else None
        ),
        merchant_ref=None if scope.region is Region.INDIA else system_txn_id,
        merchant_id=f"{scope.slug}-merchant-{config.seed}-{index:06d}",
        amount_minor=100_000 + index,
        txn_type=txn_type,
        transaction_time=transaction_time,
    )


def major_amount(case: BaseCase) -> Decimal:
    return Money.from_minor(case.amount_minor, case.scope.currency).amount
