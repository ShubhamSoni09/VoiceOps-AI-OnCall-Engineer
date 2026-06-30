PYTHON ?= python
NPM ?= npm
HOST ?= 127.0.0.1
API_PORT ?= 8001
WEB_PORT ?= 5191
WORKSPACE ?= $(CURDIR)/sandbox
BACKEND_ENV ?= $(CURDIR)/backend/.env.local
COLLAB_SQLITE ?= $(CURDIR)/backend/data/collaboration.local.sqlite3

.PHONY: dev demo-reset check

dev: demo-reset
	@echo "VoiceOps UI: http://$(HOST):$(WEB_PORT)"
	@(cd backend && $(PYTHON) -m uvicorn app.main:app --host $(HOST) --port $(API_PORT)) & \
	backend_pid=$$!; \
	(cd frontend && VITE_API_TARGET=http://$(HOST):$(API_PORT) VITE_DEV_PORT=$(WEB_PORT) $(NPM) run dev -- --host $(HOST)) & \
	frontend_pid=$$!; \
	trap 'kill $$backend_pid $$frontend_pid 2>/dev/null' INT TERM EXIT; \
	wait $$backend_pid $$frontend_pid

demo-reset:
	@cd backend && $(PYTHON) scripts/bootstrap_local_runtime.py \
		--env-file "$(BACKEND_ENV)" \
		--workspace "$(WORKSPACE)" \
		--collab-sqlite "$(COLLAB_SQLITE)" \
		--force \
		--replace-event-store \
		--seed-demo-room

check:
	@cd backend && pytest tests/test_config.py tests/test_bootstrap_local_runtime_script.py
	@cd frontend && $(NPM) run test:unit -- src/App.test.jsx src/styleTokens.test.js src/components/RightRail.test.jsx src/components/PushToTalk.test.jsx
	@cd frontend && $(NPM) run build
