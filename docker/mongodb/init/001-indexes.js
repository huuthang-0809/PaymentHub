const sources = [
  {
    database: "paymenthub_vn",
    indexes: [
      { UpdatedOn: 1 },
      { CreatedOn: 1 },
      { Status: 1 },
      { PaymentTransactionId: 1 },
      { testBatchId: 1 },
    ],
  },
  {
    database: "paymenthub_asia_id",
    indexes: [
      { UpdatedAt: 1 },
      { CreatedAt: 1 },
      { Status: 1 },
      { ExternalTxnId: 1 },
      { PaymentRefId: 1 },
      { testBatchId: 1 },
    ],
  },
  {
    database: "paymenthub_asia_ph",
    indexes: [
      { UpdatedAt: 1 },
      { CreatedAt: 1 },
      { Status: 1 },
      { ExternalTxnId: 1 },
      { PaymentRefId: 1 },
      { testBatchId: 1 },
    ],
  },
  {
    database: "paymenthub_india",
    indexes: [
      { updatedAt: 1 },
      { createdAt: 1 },
      { type: 1, status: 1 },
      { transactionId: 1 },
      { testBatchId: 1 },
    ],
  },
];

for (const source of sources) {
  const collection = db
    .getSiblingDB(source.database)
    .getCollection("payment_transactions");
  for (const index of source.indexes) {
    collection.createIndex(index);
  }
}
