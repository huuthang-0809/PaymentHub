from canonical.domain import CanonicalTransaction
from shared_kernel import AmountStatus, TxnType

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

        for txn in system_txns:
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

        unmatched_sys, system_duplicates = self._deduplicate_system(system_candidates)
        unmatched_prov, provider_duplicates = self._deduplicate_provider(provider_txns)

        for txn in system_duplicates:
            results.append(self._build_sys_result(run_id, txn, ReconResultType.DUPLICATE))
        for txn in provider_duplicates:
            results.append(self._build_prov_result(run_id, txn, ReconResultType.DUPLICATE))

        # Step 1: provider transaction ID
        pairs, unmatched_sys, unmatched_prov = self._match_by_key(
            unmatched_sys, unmatched_prov, "provider_txn_id"
        )
        results.extend(self._evaluate_pairs(run_id, pairs, is_fuzzy=False))

        # Step 2: provider reference ID
        pairs, unmatched_sys, unmatched_prov = self._match_by_key(
            unmatched_sys, unmatched_prov, "provider_ref_id"
        )
        results.extend(self._evaluate_pairs(run_id, pairs, is_fuzzy=False))

        # Step 3: merchant reference
        pairs, unmatched_sys, unmatched_prov = self._match_by_key(
            unmatched_sys, unmatched_prov, "merchant_ref"
        )
        results.extend(self._evaluate_pairs(run_id, pairs, is_fuzzy=False))

        # Step 4: fuzzy match
        pairs, unmatched_sys, unmatched_prov = self._fuzzy_match(
            unmatched_sys, unmatched_prov
        )
        results.extend(self._evaluate_pairs(run_id, pairs, is_fuzzy=True))

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
        """Currency is excluded so an exact ID can report a currency mismatch."""
        region = getattr(txn.region, "value", txn.region)
        return (region, txn.tenant_id, txn.provider.strip().upper())

    def _match_by_key(
        self,
        system_txns: list[CanonicalTransaction],
        provider_txns: list[ProviderTransaction],
        key_name: str,
    ) -> tuple:
        pairs = []
        unmatched_sys = []
        unmatched_prov = list(provider_txns)

        for system_txn in system_txns:
            key = getattr(system_txn, key_name)
            match_index = next(
                (
                    index
                    for index, provider_txn in enumerate(unmatched_prov)
                    if key
                    and key == getattr(provider_txn, key_name)
                    and self._scope(system_txn) == self._scope(provider_txn)
                ),
                None,
            )
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

            system_time = system_txn.provider_created_at or system_txn.created_at
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
        system_time = system_txn.provider_created_at or system_txn.created_at
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
    ) -> list[ReconResult]:
        results = []
        for system_txn, provider_txn in pairs:
            if system_txn.currency != provider_txn.currency:
                result_type = ReconResultType.CURRENCY_MISMATCH
            elif system_txn.amount_minor != provider_txn.amount_minor:
                result_type = ReconResultType.AMOUNT_MISMATCH
            elif is_fuzzy:
                result_type = ReconResultType.FUZZY_MATCHED
            else:
                result_type = ReconResultType.MATCHED

            delta = None
            if system_txn.amount_minor is not None:
                delta = system_txn.amount_minor - provider_txn.amount_minor

            results.append(
                ReconResult(
                    run_id=run_id,
                    result_type=result_type,
                    region=system_txn.region,
                    tenant_id=system_txn.tenant_id,
                    provider=system_txn.provider,
                    currency=system_txn.currency,
                    txn_type=system_txn.txn_type.value,
                    system_txn_id=system_txn.txn_id,
                    system_amount_minor=system_txn.amount_minor,
                    provider_txn_id=provider_txn.provider_txn_id,
                    provider_amount_minor=provider_txn.amount_minor,
                    source_file=provider_txn.source_file,
                    source_row_number=provider_txn.source_row_number,
                    delta_amount=delta,
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
            key = (self._scope(txn), txn.provider_txn_id)
            if txn.provider_txn_id and key in seen:
                duplicates.append(txn)
            else:
                if txn.provider_txn_id:
                    seen.add(key)
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
            currency=txn.currency,
            txn_type=txn.txn_type.value,
            system_txn_id=txn.txn_id,
            system_amount_minor=txn.amount_minor,
            reason=reason,
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
            currency=txn.currency,
            provider_txn_id=txn.provider_txn_id,
            provider_amount_minor=txn.amount_minor,
            source_file=txn.source_file,
            source_row_number=txn.source_row_number,
            reason=reason,
        )
