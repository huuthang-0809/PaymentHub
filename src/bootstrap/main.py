from fastapi import FastAPI

app = FastAPI(title="PaymentHub API")

def test1():
    print("test1")

@app.get("/heath")
def heath_check():
    return {"status" : "ok", "service": "paymenthub"}

def test2():
    print("test2")
