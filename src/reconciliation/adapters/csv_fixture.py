import csv
from io import StringIO

from reconciliation.application.ports import (
    ParseReport,
    ValidationCode,
    ValidationIssue,
)
from reconciliation.domain import ProviderTransaction
from shared_kernel import Money, Region, TxnType, parse_iso_utc

REQUIRED_COLUMNS = (
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


class RowError(ValueError):
    def __init__(self, code: ValidationCode, field: str | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


def _optional(value: str) -> str | None:
    return value or None


class CsvFixtureParser:
    """Parser for the internal comma-separated provider fixture format."""

    def parse(self, content: bytes, source_file: str) -> ParseReport:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            return self._file_error(ValidationCode.INVALID_ENCODING, str(exc))

        if not source_file.strip():
            return self._file_error(
                ValidationCode.INVALID_TYPE, "source_file is required", "source_file"
            )

        try:
            dialect = csv.Sniffer().sniff("\n".join(text.splitlines()[:10]), delimiters=",;|\t")
        except csv.Error:
            return self._file_error(
                ValidationCode.INVALID_DELIMITER, "cannot detect CSV delimiter"
            )
        if dialect.delimiter != ",":
            return self._file_error(
                ValidationCode.INVALID_DELIMITER, "fixture delimiter must be comma"
            )

        csv_rows = csv.reader(StringIO(text), dialect)
        header = next(csv_rows, [])
        missing = [column for column in REQUIRED_COLUMNS if column not in header]
        if missing:
            return self._file_error(
                ValidationCode.MISSING_COLUMN,
                f"missing columns: {', '.join(missing)}",
            )

        rows: list[ProviderTransaction] = []
        issues: list[ValidationIssue] = []
        seen: set[tuple[str, ...]] = set()

        for row_number, values in enumerate(csv_rows, start=2):
            clean_values = tuple(value.strip() for value in values)
            if not clean_values or not any(clean_values):
                issues.append(
                    ValidationIssue(
                        row_number, None, ValidationCode.EMPTY_ROW, "empty row"
                    )
                )
                continue
            if len(clean_values) != len(header):
                issues.append(
                    ValidationIssue(
                        row_number,
                        None,
                        ValidationCode.INVALID_TYPE,
                        "column count does not match header",
                    )
                )
                continue
            if clean_values in seen:
                issues.append(
                    ValidationIssue(
                        row_number,
                        None,
                        ValidationCode.DUPLICATE_ROW,
                        "duplicate provider row",
                    )
                )
                continue
            seen.add(clean_values)
            raw = dict(zip(header, clean_values, strict=True))

            try:
                rows.append(self._to_transaction(raw, source_file, row_number))
            except RowError as exc:
                issues.append(
                    ValidationIssue(row_number, exc.field, exc.code, str(exc))
                )

        return ParseReport(tuple(rows), tuple(issues))

    @staticmethod
    def _to_transaction(
        row: dict[str, str], source_file: str, row_number: int
    ) -> ProviderTransaction:
        for field in (
            "provider",
            "region",
            "amount_minor",
            "currency",
            "status",
            "txn_type",
            "transaction_time",
        ):
            if not row[field]:
                raise RowError(
                    ValidationCode.MISSING_COLUMN, field, f"{field} is required"
                )

        try:
            region = Region(row["region"].upper())
        except ValueError as exc:
            raise RowError(
                ValidationCode.INVALID_TYPE, "region", "unsupported region"
            ) from exc

        try:
            txn_type = TxnType(row["txn_type"].upper())
        except ValueError as exc:
            raise RowError(
                ValidationCode.INVALID_TYPE, "txn_type", "unsupported transaction type"
            ) from exc

        try:
            amount_minor = int(row["amount_minor"])
        except ValueError as exc:
            raise RowError(
                ValidationCode.INVALID_TYPE,
                "amount_minor",
                "amount_minor must be an integer",
            ) from exc
        if amount_minor < 0:
            raise RowError(
                ValidationCode.NEGATIVE_AMOUNT,
                "amount_minor",
                "amount_minor cannot be negative",
            )

        currency = row["currency"].upper()
        try:
            Money.from_minor(amount_minor, currency)
        except ValueError as exc:
            raise RowError(
                ValidationCode.UNKNOWN_CURRENCY, "currency", str(exc)
            ) from exc

        try:
            transaction_time = parse_iso_utc(row["transaction_time"])
        except ValueError as exc:
            raise RowError(
                ValidationCode.INVALID_DATE,
                "transaction_time",
                "transaction_time must be an ISO timezone-aware datetime",
            ) from exc

        try:
            return ProviderTransaction(
                provider=row["provider"],
                region=region,
                tenant_id=_optional(row["tenant_id"]),
                provider_txn_id=_optional(row["provider_txn_id"]),
                provider_ref_id=_optional(row["provider_ref_id"]),
                merchant_ref=_optional(row["merchant_ref"]),
                merchant_id=_optional(row["merchant_id"]),
                amount_minor=amount_minor,
                currency=currency,
                status=row["status"],
                txn_type=txn_type,
                transaction_time=transaction_time,
                source_file=source_file,
                source_row_number=row_number,
                raw=row,
            )
        except (TypeError, ValueError) as exc:
            raise RowError(ValidationCode.INVALID_TYPE, None, str(exc)) from exc

    @staticmethod
    def _file_error(
        code: ValidationCode, message: str, field: str | None = None
    ) -> ParseReport:
        return ParseReport((), (ValidationIssue(None, field, code, message),))
