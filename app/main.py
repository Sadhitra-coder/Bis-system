from fastapi import FastAPI

app = FastAPI(title="BIS RAG Engine")


@app.get("/health")
def health_check():
    return {"status": "ok"}
