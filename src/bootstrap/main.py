from fastapi import FastAPI

app = FastAPI(title="PaymentHub API")


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "paymenthub"}