# VISION dev commands. Run from the project root.
#
#   make backend   — start FastAPI on :8000 (auto-reload)
#   make frontend  — start Next.js on :3000
#   make dev       — start both, backend in background; Ctrl+C kills frontend
#   make stop      — kill any lingering uvicorn / next dev processes
#   make install   — install Python deps + frontend deps
#   make clean     — drop caches (DuckDB/SQLite, .next, __pycache__)
#
# Requires the venv at .venv/ (created during initial setup).

PY     := .venv/bin/python
PIP    := .venv/bin/pip
UVI    := .venv/bin/uvicorn
PORT   := 8000
FE_PORT:= 3000

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "VISION targets:"
	@echo "  make backend     — start FastAPI on :$(PORT)"
	@echo "  make frontend    — start Next.js on :$(FE_PORT)"
	@echo "  make dev         — both (backend in background)"
	@echo "  make stop        — kill running uvicorn / next dev"
	@echo "  make install     — install all deps"
	@echo "  make clean       — drop caches"

.PHONY: backend
backend:
	@test -x $(UVI) || { echo "ERR: $(UVI) not found. Run: make install"; exit 1; }
	$(UVI) vision.api:app --reload --port $(PORT)

.PHONY: frontend
frontend:
	cd frontend && npm run dev

.PHONY: dev
dev: stop
	@echo "Starting backend on :$(PORT) (logs → /tmp/vision_api.log)"
	@$(UVI) vision.api:app --reload --port $(PORT) > /tmp/vision_api.log 2>&1 &
	@sleep 2
	@echo "Starting frontend on :$(FE_PORT) (Ctrl+C to stop both)"
	@trap 'make stop' INT TERM; cd frontend && npm run dev

.PHONY: stop
stop:
	-@pkill -f "uvicorn vision.api" 2>/dev/null || true
	-@pkill -f "next dev" 2>/dev/null || true
	@echo "Stopped."

.PHONY: install
install:
	@test -d .venv || python3 -m venv .venv
	$(PIP) install -r requirements.txt
	cd frontend && npm install

.PHONY: clean
clean:
	rm -f cache/*.sqlite cache/*.sqlite-shm cache/*.sqlite-wal cache/*.duckdb cache/*.duckdb.wal
	rm -rf frontend/.next
	find vision -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

.PHONY: log
log:
	tail -f /tmp/vision_api.log
