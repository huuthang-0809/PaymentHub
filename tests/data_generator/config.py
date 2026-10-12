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
    profile: str = "baseline"
    time_span_hours: int = 24
    end_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.records_per_region <= 0:
            raise ValueError("records_per_region must be positive")
        if self.profile not in {"baseline", "monitoring-rich"}:
            raise ValueError("profile must be baseline or monitoring-rich")
        if not 1 <= self.time_span_hours <= 24 * 31:
            raise ValueError("time_span_hours must be between 1 and 744")
        if self.end_at is not None and self.end_at.tzinfo is None:
            raise ValueError("end_at must be timezone-aware")
        try:
            datetime.strptime(str(self.seed), "%Y%m%d")
        except ValueError as exc:
            raise ValueError("seed must use YYYYMMDD format") from exc

    @property
    def batch_id(self) -> str:
        return f"seed-{self.seed}"

    @property
    def base_time(self) -> datetime:
        parsed = datetime.strptime(str(self.seed), "%Y%m%d")
        return parsed.replace(tzinfo=UTC)

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

    amount_minor = 100_000 + index
    if config.profile == "monitoring-rich":
        amount_minor = 10_000 + (index * 7_919) % 2_000_000
        end_at = config.end_at or config.base_time + timedelta(days=1)
        span = timedelta(hours=config.time_span_hours)
        scope_records = config.records_per_region
        if scope.region is Region.ASIA:
            scope_records = (
                (config.records_per_region + 1) // 2
                if scope.tenant_id == "asia-id"
                else config.records_per_region // 2
            )
        fraction = index / max(1, scope_records - 1)
        if config.time_span_hours == 24:
            base_weights = (
                0.30, 0.24, 0.20, 0.18, 0.22, 0.35,
                0.70, 1.20, 1.55, 1.25, 1.05, 1.35,
                1.60, 1.25, 1.00, 0.95, 1.10, 1.35,
                1.70, 1.85, 1.65, 1.25, 0.80, 0.48,
            )
            provider_offset = {"ONEPAY": 11, "ESPAY": 29, "2C2P": 47, "RAZORPAY": 71}[scope.provider]
            weights = tuple(
                weight
                * (
                    0.85
                    + ((config.seed + hour * 17 + provider_offset * 13) % 31)
                    / 100
                )
                for hour, weight in enumerate(base_weights)
            )
            target = fraction * sum(weights)
            elapsed_weight = 0.0
            elapsed_hours = 0.0
            for weight in weights:
                if target <= elapsed_weight + weight:
                    elapsed_hours += (target - elapsed_weight) / weight
                    break
                elapsed_weight += weight
                elapsed_hours += 1
            transaction_time = end_at - span + timedelta(hours=elapsed_hours)
        else:
            transaction_time = end_at - span + span * fraction

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
        amount_minor=amount_minor,
        txn_type=txn_type,
        transaction_time=transaction_time,
    )


def major_amount(case: BaseCase) -> Decimal:
    return Money.from_minor(case.amount_minor, case.scope.currency).amount
