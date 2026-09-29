from dataclasses import replace

from canonical.domain import CanonicalTransaction
from shared_kernel import AmountStatus, Region, TxnType

from .models import ProviderTransaction, ReconResult, ReconResultType


class MatchingEngine:
    def __init__(self, time_window_second: int = 300):
        self.time_window_second = time_window_second

    def match(
        self,
        run_id: str,
        provider_txns: list[ProviderTransaction],
        system_txns: list[CanonicalTransaction],
    ) -> list[ReconResult]:
        results = []
        system_candidates = []

        unmatched_sys, system_duplicates = self._deduplicate_system(system_txns)
        normalized_provider_txns = [
            self._normalize_provider_txn(txn) for txn in provider_txns
        ]
        unmatched_prov, provider_duplicates = self._deduplicate_provider(
            normalized_provider_txns
        )

        for txn in system_duplicates:
            results.append(self._build_sys_result(run_id, txn, ReconResultType.DUPLICATE))
        for txn in provider_duplicates:
            results.append(self._build_prov_result(run_id, txn, ReconResultType.DUPLICATE))

        for txn in unmatched_sys:
            if not txn.is_reconcilable or txn.txn_type in {
                TxnType.CARD_LINK,
                TxnType.TOKENIZATION,
            }:
                results.append(
                    self._build_sys_result(
                        run_id, txn, ReconResultType.NOT_RECONCILABLE
                    )
                )
            elif txn.amount_status in {AmountStatus.INVALID, AmountStatus.NO_CURRENCY}:
                results.append(
                    self._build_sys_result(run_id, txn, ReconResultType.INVALID_DATA)
                )
            else:
                system_candidates.append(txn)

        unmatched_sys = system_candidates

        # Steps 1–3: exact identifiers within the full currency scope.
        exact_keys = ("provider_txn_id", "provider_ref_id", "merchant_ref")
        for key_name in exact_keys:
            pairs, unmatched_sys, unmatched_prov = self._match_by_key(
                unmatched_sys, unmatched_prov, key_name,
                asia_only=key_name == "provider_ref_id",
            )
            results.extend(self._evaluate_pairs(run_id, pairs, False, key_name))

        # Step 4: fuzzy match
        pairs, unmatched_sys, unmatched_prov = self._fuzzy_match(
            unmatched_sys, unmatched_prov
        )
        results.extend(self._evaluate_pairs(run_id, pairs, is_fuzzy=True))

        # Diagnose currency differences only after all full-scope matching.
        for key_name in exact_keys:
            pairs, unmatched_sys, unmatched_prov = self._match_by_key(
                unmatched_sys, unmatched_prov, key_name,
                asia_only=key_name == "provider_ref_id",
                currency_mismatch=True,
            )
            results.extend(self._evaluate_pairs(run_id, pairs, False, key_name))

        for txn in unmatched_sys:
            results.append(
                self._build_sys_result(run_id, txn, ReconResultType.MISSING_IN_PROVIDER)
            )
        for txn in unmatched_prov:
            results.append(
                self._build_prov_result(run_id, txn, ReconResultType.MISSING_IN_SYSTEM)
            )
        return results

    @staticmethod
    def _scope(txn: CanonicalTransaction | ProviderTransaction) -> tuple:
        """Full scope used by deduplication and fuzzy matching."""
        return (
            txn.region,
            txn.tenant_id,
            txn.provider.strip().upper() if txn.provider else "",
            txn.currency.strip().upper() if txn.currency else "",
        )

    @staticmethod
    def _identity_scope(txn: CanonicalTransaction | ProviderTransaction) -> tuple:
        """Base scope used only to correlate an exact-ID currency mismatch."""
        return (
            txn.region,
            txn.tenant_id,
            txn.provider.strip().upper() if txn.provider else "",
        )

    @staticmethod
    def _normalize_provider_txn(txn: ProviderTransaction) -> ProviderTransaction:
        """Ignore the parent ExternalTxnId inherited by legacy Asia refunds."""
        if txn.region == Region.ASIA and txn.txn_type == TxnType.REFUND:
            return replace(txn, provider_txn_id=None)
        return txn

    def _match_by_key(
        self,
        system_txns: list[CanonicalTransaction],
        provider_txns: list[ProviderTransaction],
        key_name: str,
        asia_only: bool = False,
        currency_mismatch: bool = False,
    ) -> tuple:
        pairs = []
        unmatched_sys = []
        unmatched_prov = list(provider_txns)

        for system_txn in system_txns:
            if asia_only and system_txn.region != Region.ASIA:
                unmatched_sys.append(system_txn)
                continue

            key = getattr(system_txn, key_name)
            if not key:
                unmatched_sys.append(system_txn)
                continue

            candidate_indexes = [
                index
                for index, provider_txn in enumerate(unmatched_prov)
                if key == getattr(provider_txn, key_name)
                and self._identity_scope(system_txn)
                == self._identity_scope(provider_txn)
                and (
                    (self._scope(system_txn) != self._scope(provider_txn))
                    if currency_mismatch
                    else (self._scope(system_txn) == self._scope(provider_txn))
                )
            ]
            match_index = candidate_indexes[0] if candidate_indexes else None
            if match_index is None:
                unmatched_sys.append(system_txn)
            else:
                pairs.append((system_txn, unmatched_prov.pop(match_index)))

        return pairs, unmatched_sys, unmatched_prov

    def _fuzzy_match(
        self,
        system_txns: list[CanonicalTransaction],
        provider_txns: list[ProviderTransaction],
    ) -> tuple:
        pairs = []
        unmatched_sys = []
        unmatched_prov = list(provider_txns)

        for system_txn in system_txns:
            possible_matches = [
                (index, provider_txn)
                for index, provider_txn in enumerate(unmatched_prov)
                if self._is_fuzzy_match(system_txn, provider_txn)
            ]
            if not possible_matches:
                unmatched_sys.append(system_txn)
                continue

            system_time = system_txn.created_at
            match_index = possible_matches[0][0]
            smallest_difference = None
            for index, provider_txn in possible_matches:
                difference = abs(
                    (system_time - provider_txn.transaction_time).total_seconds()
                )
                if smallest_difference is None or difference < smallest_difference:
                    match_index = index
                    smallest_difference = difference

            pairs.append((system_txn, unmatched_prov.pop(match_index)))

        return pairs, unmatched_sys, unmatched_prov

    def _is_fuzzy_match(
        self,
        system_txn: CanonicalTransaction,
        provider_txn: ProviderTransaction,
    ) -> bool:
        system_time = system_txn.created_at
        time_difference = abs(
            (system_time - provider_txn.transaction_time).total_seconds()
        )
        return (
            system_txn.reconciliation_scope == provider_txn.reconciliation_scope
            and system_txn.merchant_id is not None
            and system_txn.merchant_id == provider_txn.merchant_id
            and system_txn.amount_minor == provider_txn.amount_minor
            and time_difference <= self.time_window_second
        )

    def _evaluate_pairs(
        self,
        run_id: str,
        pairs: list[tuple[CanonicalTransaction, ProviderTransaction]],
        is_fuzzy: bool,
        matched_by: str = "composite",
    ) -> list[ReconResult]:
        results = []
        for system_txn, provider_txn in pairs:
            same_currency = system_txn.currency == provider_txn.currency
            if not same_currency:
                result_type = ReconResultType.CURRENCY_MISMATCH
            elif system_txn.amount_minor != provider_txn.amount_minor:
                result_type = ReconResultType.AMOUNT_MISMATCH
            elif is_fuzzy:
                result_type = ReconResultType.FUZZY_MATCHED
            else:
                result_type = ReconResultType.MATCHED

            delta = None
            if (
                same_currency
                and system_txn.amount_minor is not None
                and provider_txn.amount_minor is not None
            ):
                delta = system_txn.amount_minor - provider_txn.amount_minor

            results.append(
                ReconResult(
                    run_id=run_id,
                    result_type=result_type,
                    region=system_txn.region,
                    tenant_id=system_txn.tenant_id,
                    provider=system_txn.provider,
                    txn_type=system_txn.txn_type,
                    system_txn_id=system_txn.txn_id,
                    system_provider_txn_id=system_txn.provider_txn_id,
                    system_status=system_txn.status,
                    system_amount_minor=system_txn.amount_minor,
                    system_currency=system_txn.currency,
                    refund_of=system_txn.refund_of,
                    refunded_amount_minor=system_txn.refunded_amount_minor,
                    provider_txn_id=provider_txn.provider_txn_id,
                    provider_amount_minor=provider_txn.amount_minor,
                    provider_currency=provider_txn.currency,
                    source_file=provider_txn.source_file,
                    source_row_number=provider_txn.source_row_number,
                    delta_amount=delta,
                    reason=f"{result_type.value}: matched by {matched_by}",
                )
            )
        return results

    @staticmethod
    def _deduplicate_system(
        txns: list[CanonicalTransaction],
    ) -> tuple[list[CanonicalTransaction], list[CanonicalTransaction]]:
        seen = set()
        clean = []
        duplicates = []
        for txn in txns:
            if txn.global_key in seen:
                duplicates.append(txn)
            else:
                seen.add(txn.global_key)
                clean.append(txn)
        return clean, duplicates

    def _deduplicate_provider(
        self,
        txns: list[ProviderTransaction],
    ) -> tuple[list[ProviderTransaction], list[ProviderTransaction]]:
        seen = set()
        clean = []
        duplicates = []
        for txn in txns:
            key_name = "provider_txn_id"
            if not txn.provider_txn_id:
                key_name = (
                    "provider_ref_id"
                    if txn.region == Region.ASIA and txn.provider_ref_id
                    else "merchant_ref"
                )
            identifier = getattr(txn, key_name)
            key = (self._scope(txn), key_name, identifier)
            if identifier:
                if key in seen:
                    duplicates.append(txn)
                else:
                    seen.add(key)
                    clean.append(txn)
            else:
                clean.append(txn)
        return clean, duplicates

    @staticmethod
    def _build_sys_result(
        run_id: str,
        txn: CanonicalTransaction,
        result_type: ReconResultType,
        reason: str | None = None,
    ) -> ReconResult:
        return ReconResult(
            run_id=run_id,
            result_type=result_type,
            region=txn.region,
            tenant_id=txn.tenant_id,
            provider=txn.provider,
            txn_type=txn.txn_type,
            system_txn_id=txn.txn_id,
            system_provider_txn_id=txn.provider_txn_id,
            system_status=txn.status,
            system_amount_minor=txn.amount_minor,
            system_currency=txn.currency,
            refund_of=txn.refund_of,
            refunded_amount_minor=txn.refunded_amount_minor,
            reason=reason or {
                ReconResultType.DUPLICATE: "Repeated system (region, tenant_id, txn_id)",
                ReconResultType.INVALID_DATA: f"amount_status={txn.amount_status.value}",
                ReconResultType.NOT_RECONCILABLE: "Transaction is outside reconciliation scope",
                ReconResultType.MISSING_IN_PROVIDER: "No eligible provider counterpart found",
            }.get(result_type),
        )

    @staticmethod
    def _build_prov_result(
        run_id: str,
        txn: ProviderTransaction,
        result_type: ReconResultType,
        reason: str | None = None,
    ) -> ReconResult:
        return ReconResult(
            run_id=run_id,
            result_type=result_type,
            region=txn.region,
            tenant_id=txn.tenant_id,
            provider=txn.provider,
            txn_type=txn.txn_type,
            provider_txn_id=txn.provider_txn_id,
            provider_amount_minor=txn.amount_minor,
            provider_currency=txn.currency,
            source_file=txn.source_file,
            source_row_number=txn.source_row_number,
            reason=reason or {
                ReconResultType.DUPLICATE: "Repeated provider identifier within currency scope",
                ReconResultType.MISSING_IN_SYSTEM: "No eligible system counterpart found",
            }.get(result_type),
        )
