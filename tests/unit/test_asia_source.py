from datetime import UTC, date, datetime

from canonical.adapters.asia import AsiaMapping, MongoAsiaSource
from shared_kernel import Region, TxnType

class Collection:
    def __init__(self, document):
        self.documents = document
        self.last_query = None
    def find(self, query):
        self.last_query = query
        return self.documents

def test_all_tenants_are_read_and_kept_distinct():
    first = Collection(
        [
            {
                "_id": "same-id",
                "CreatedAt": datetime(2026, 9, 21, tzinfo=UTC),
                "Type": 7,
                "Status": 2,
                "Currency": "IDR",
                "AmountAsDecimal": "10",
                "Amount": "10"
            }
        ]
    )
    second = Collection(
        [
            {
                "_id": "same-id",
                "CreatedAt": datetime(2026, 9, 21, tzinfo=UTC),
                "Type": 7,
                "Status": 2,
                "Currency": "PHP",
                "AmountAsDecimal": "10.00",
                "Amount": "10.00"
            }
        ]
    )

    client = {"db_id" : {"transactions": first},
              "db_ph" : {"transactions": second}}
    source = MongoAsiaSource(
        client,
        {"asia-id": "db_id", "asia-ph": "db_ph"},
        "transactions",
        AsiaMapping(type_code={7: TxnType.PAYMENT})
    )
    rows = source.get_all_tenants(date(2026, 9, 21))
    assert len(rows) == 2 and rows[0].global_key != rows[1].global_key