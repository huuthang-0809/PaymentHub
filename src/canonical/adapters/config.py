"""Shared canonical mapping configuration for PaymentHub sources."""

from canonical.adapters.asia import AsiaMapping
from canonical.adapters.vn import VNMapping
from shared_kernel.enums import Instrument


def paymenthub_vn_mapping() -> VNMapping:
    return VNMapping(
        instrument_by_method={"CARD": Instrument.CARD},
        card_link_methods=frozenset({"LINKCARD"}),
    )


def paymenthub_asia_mapping() -> AsiaMapping:
    return AsiaMapping(
        instrument_code={
            "card": Instrument.CARD,
            "qr": Instrument.QR,
            "virtual-account": Instrument.VIRTUAL_ACCOUNT,
        },
        synthetic_flag_value={"synthetic": True},
    )
