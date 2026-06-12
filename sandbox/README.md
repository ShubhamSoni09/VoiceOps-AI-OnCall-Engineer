# VoiceOps sandbox workspace

**checkout-api** demo with intentional bugs for agent testing:

| Issue | Symptom | Fix |
|-------|---------|-----|
| Missing `/health` | `test_health` → 404 | Add `GET /health` returning `{"status": "ok"}` |
| Missing `/v2/charge` | `test_charge_endpoint` → 404 | Add charge handler |
| Wrong metrics schema | `test_metrics_schema` fails | Rename `error_rate_pct` → `error_rate` |

```bash
cd sandbox
pip install -r requirements.txt
pytest   # 3 failures, 1 pass — until agent patches app.py
```

## Voice commands to try

1. *"Investigate what's failing in sandbox"*
2. *"Fix the health endpoint"*
3. *"Fix the charge endpoint"*
4. *"Fix the metrics schema"*

## MCP + dashboard

Set `VOICEOPS_WORKSPACE=sandbox` in `backend/.env` and `.cursor/mcp.json`.
