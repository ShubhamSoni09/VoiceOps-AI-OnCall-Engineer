"""checkout-api sandbox — intentional bugs for VoiceOps agent testing."""

from fastapi import FastAPI

app = FastAPI(title="checkout-api")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "checkout-api", "status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> dict[str, float]:
    return {"error_rate": 0.47, "p99_latency_ms": 1840.0}


@app.get("/v2/charge")
def charge(amount: float) -> dict[str, str]:
    return {"status": "charged", "amount": str(amount)}