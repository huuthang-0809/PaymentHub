from enum import Enum

class AmountStatus(Enum):
    VALID = "VALID"
    ASSUMED_CURRENCY = "ASSUMED_CURRENCY" #Case VN lack currency
    INVALID = "INVALID" # Case Asia mismatch cross-check

CURRENCY_EXPONENTS = {
    "VND": 0,
    "IDR": 0,
    "INR": 2,
    "PHP": 2,
    "USD": 2,
}