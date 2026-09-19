from enum import Enum
 
 
class Region(str, Enum):
    """Supported transaction regions."""
 
    VN = "VN"
    ASIA = "ASIA"
    INDIA = "INDIA"
 
 
class TxnType(str, Enum):
    """Canonical transaction types."""
 
    PAYMENT = "PAYMENT"
    LOAD = "LOAD"
    REFUND = "REFUND"
    CARD_LINK = "CARD_LINK"
    TOKENIZATION = "TOKENIZATION"
    UNKNOWN = "UNKNOWN"
 
 
class CanonicalStatus(str, Enum):
    """Canonical transaction status mapped across all regions."""
 
    CREATED = "CREATED"
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    PARTIAL_REFUNDED = "PARTIAL_REFUNDED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"
 
 
class AmountStatus(str, Enum):
    """Status indicating validity and confidence of transaction amount/currency."""
 
    OK = "OK"
    ASSUMED_CURRENCY = "ASSUMED_CURRENCY"
    NO_CURRENCY = "NO_CURRENCY"
    INVALID = "INVALID"
    NOT_APPLICABLE = "NOT_APPLICABLE"
 
 
class ReconResult(str, Enum):
    """The 9 mandatory reconciliation outcome classifications for P2 engine."""
 
    MATCHED = "MATCHED"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    MISSING_IN_SYSTEM = "MISSING_IN_SYSTEM"
    MISSING_IN_PROVIDER = "MISSING_IN_PROVIDER"
    DUPLICATE = "DUPLICATE"
    FUZZY_MATCHED = "FUZZY_MATCHED"
    NOT_RECONCILABLE = "NOT_RECONCILABLE"
    INVALID_DATA = "INVALID_DATA"

class Instrument(str, Enum):
    CARD= "CARD"
    VIRTUAL_ACCOUNT = "VIRTUAL_ACCOUNT"
    EWALLET = "EWALLET"
    QR = "QR"
    PAYMENT_LINK = "PAYMENT_LINK"
    DIRECT_DEBIT = "DIRECT_DEBIT"