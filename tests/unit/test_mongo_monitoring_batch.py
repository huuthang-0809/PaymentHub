from copy import deepcopy

from monitoring.adapters.mongo import MongoCanonicalBatchSource
from shared_kernel import Region
from tests.data_generator.generator import GeneratorConfig, generate_dataset


class _Cursor(list):
    def sort(self, key, direction):
        return _Cursor(sorted(self, key=lambda row: str(row.get(key, ""))))


class _Collection:
    def __init__(self, documents):
        self.documents = documents

    def find(self, predicate):
        batch_id = predicate["testBatchId"]
        return _Cursor(
            deepcopy(row)
            for row in self.documents
            if row.get("testBatchId") == batch_id
        )


class _Database:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return _Collection(self.collections.get(name, ()))


class _Client:
    def __init__(self, datasets):
        self.datasets = datasets

    def __getitem__(self, database):
        return _Database({"payment_transactions": self.datasets.get(database, ())})


def test_batch_source_reads_four_scopes_and_maps_region_totals() -> None:
    config = GeneratorConfig(seed=20260923, records_per_region=4)
    datasets, _ = generate_dataset(config)

    result = MongoCanonicalBatchSource(_Client(datasets)).read(config.batch_id)

    assert result.read == 12
    assert len(result.events) == 12
    assert result.failures == ()
    assert result.documents_by_region[Region.VN] == 4
    assert result.documents_by_region[Region.ASIA] == 4
    assert result.documents_by_region[Region.INDIA] == 4
    assert len({event.event_id for event in result.events}) == 12
    assert all(event.event_id.startswith(f"{config.batch_id}|") for event in result.events)


def test_batch_source_classifies_mapping_failure_without_raw_payload() -> None:
    config = GeneratorConfig(seed=20260923, records_per_region=2)
    datasets, _ = generate_dataset(config)
    datasets["paymenthub_vn"][0].pop("CreatedOn")

    result = MongoCanonicalBatchSource(_Client(datasets)).read(config.batch_id)

    assert result.read == 6
    assert len(result.events) == 5
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.database == "paymenthub_vn"
    assert failure.code == "MAPPING_VALIDATION"
    assert failure.message == "missing CreatedOn"
    assert "Amount" not in failure.message
