from fastapi import FastAPI

app = FastAPI(title="PaymentHub API")

@app.get("/health")
@app.get("/heath", include_in_schema=False)
def heath_check():
    return {"status" : "ok", "service": "paymenthub"}
