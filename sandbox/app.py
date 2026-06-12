"""Simple checkout API — missing /health on purpose for agent testing."""

from fastapi import FastAPI

app = FastAPI(title="checkout-api")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "checkout-api", "status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}