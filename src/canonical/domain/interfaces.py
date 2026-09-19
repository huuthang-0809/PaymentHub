from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from .entities import CanonicalTransaction, ProviderTransaction
from shared_kernel.scope import ReconScopeKey

class CannonicalRepositoryInterface(ABC):
    """
    Port interface for accessing system canonical transactions.
    """
    @abstractmethod
    def get_by_key(self, region:str, tenant_id: Optional[str], txn_id: str) -> Optional[CanonicalTransaction]:
        pass

    @abstractmethod
    def fetch_by_scope(self, scope: ReconScopeKey, start_time: datetime, end_time: datetime) -> List[CanonicalTransaction]:
        pass

class ProviderTransactionRepositoryInterface(ABC):
    """
    Port interface for accessing parsed provider statement records.
    """

    @abstractmethod
    def save_batch(self, records: List[ProviderTransaction]) -> None:
        pass

    @abstractmethod
    def fetch_by_scope(self, scope: ReconScopeKey, start_time: datetime, end_time: datetime) -> List[ProviderTransaction]:
        pass