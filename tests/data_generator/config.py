from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

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

    @property
    def base_time(self) -> datetime:
        parsed = datetime.strptime(str(self.seed), "%Y%m%d")
        # The seed labels the business date/file. With the shared 02:00 local
        # cutoff, transactions for that period occur on the previous UTC day.
        return parsed.replace(tzinfo=UTC) - timedelta(days=1)


@dataclass(frozen=True, slots=True)
class ScopeSpec:
    database: str
    filename: str
    region: Region
    tenant_id: str | None
    provider: str
    currency: str
    slug: str


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
        ),
        ScopeSpec(
            DATABASE_ASIA_ID,
            f"espay_asia-id_{config.seed}.csv",
            Region.ASIA,
            "asia-id",
            "ESPAY",
            "IDR",
            "asia-id",
        ),
        ScopeSpec(
            DATABASE_ASIA_PH,
            f"2c2p_asia-ph_{config.seed}.csv",
            Region.ASIA,
            "asia-ph",
            "2C2P",
            "PHP",
            "asia-ph",
        ),
        ScopeSpec(
            DATABASE_INDIA,
            f"razorpay_{config.seed}.csv",
            Region.INDIA,
            None,
            "RAZORPAY",
            "INR",
            "india",
        ),
    )


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

    # Asia refunds inherit ExternalTxnId from the payment at cycle position 0.
    if scope.region is Region.ASIA and mutation == "duplicate":
        txn_type = TxnType.REFUND
        parent_index = cycle * len(MUTATION_CYCLE)
        provider_txn_id = (
            f"{scope.provider.lower()}-{config.seed}-{parent_index:06d}"
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
        transaction_time=config.base_time + timedelta(seconds=index),
    )


def major_amount(case: BaseCase) -> Decimal:
    return Money.from_minor(case.amount_minor, case.scope.currency).amount
