# Abhi Studio — developer entry points
SHELL := /bin/bash
VENV  := .venv
PY    := $(VENV)/bin/python
PIP   := $(VENV)/bin/pip
PORT  ?= 8000

.PHONY: help setup run dev build test smoke clean clean-media reset-db status

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv, install deps, build the frontend, create .env
	@bash scripts/setup.sh

run: ## Start the studio (API + built UI) on http://0.0.0.0:$(PORT)
	@test -d $(VENV) || (echo "run 'make setup' first" && exit 1)
	cd backend && ../$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port $(PORT)

dev: ## Run the API plus a Vite dev server with hot reload (two terminals not needed)
	@test -d $(VENV) || (echo "run 'make setup' first" && exit 1)
	@echo "API on :$(PORT) — open the Vite URL printed below for hot reload"
	@(cd backend && ../$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port $(PORT) --reload &) ; \
	 cd frontend && npm run dev

build: ## Build the frontend into frontend/dist (served by the API)
	cd frontend && npm run build

test: ## Run the backend test suite
	cd backend && ../$(PY) -m pytest tests -q --timeout=600

smoke: ## End-to-end smoke test against a running server
	@bash scripts/smoke.sh http://127.0.0.1:$(PORT) admin "$${ABHI_ADMIN_PASSWORD:-abhi-admin}"

typecheck: ## Type-check the frontend
	cd frontend && npm run typecheck

status: ## Print what the running server reports about itself
	@curl -fsS http://127.0.0.1:$(PORT)/api/health | $(PY) -m json.tool || echo "server not running on :$(PORT)"

clean: ## Remove build caches (keeps your media)
	rm -rf backend/__pycache__ backend/app/**/__pycache__ backend/.pytest_cache frontend/dist

clean-media: ## Delete generated media (assets in the DB become unavailable)
	rm -rf data/media data/thumbs data/tmp
	@echo "media removed; run 'make run' and use /api/jobs (asset.thumbnail) to rebuild thumbnails"

reset-db: ## Delete the database (destroys projects, characters, stories, jobs)
	@read -p "Delete data/abhi.db? [y/N] " confirm; [ "$$confirm" = "y" ] || exit 1
	rm -f data/abhi.db data/abhi.db-wal data/abhi.db-shm
	@echo "database removed; it will be recreated on next start"
