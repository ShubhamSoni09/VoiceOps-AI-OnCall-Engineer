"""Simple checkout API used by VoiceOps demo agent tests."""

from fastapi import FastAPI

app = FastAPI(title="checkout-api")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "checkout-api", "status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
