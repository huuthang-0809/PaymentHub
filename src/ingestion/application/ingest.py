from typing import Protocol

from ingestion.application.events import IngestEvent

class IngestRepository(Protocol):
    def save(self, event: IngestEvent) -> bool: ...

class IngestUseCase:
    def __init__(self, repository: IngestRepository) -> None:
        self.repository = repository

    def execute(self, event: IngestEvent) -> bool:
        return self.repository.save(event)