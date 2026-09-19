from fastapi import FastAPI

app = FastAPI(title="PaymentHub API")

@app.get("/heath")
def heath_check():
    return {"status" : "ok", "service": "paymenthub"}
