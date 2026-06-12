.PHONY: help install lock upgrade sync format format-check lint lint-ci lint-fix lint-loc typecheck typecheck-fast typecheck-stop typecheck-fresh test test-fast test-unit test-integration test-cov test-all check ci-local precommit clean dev mcp-serve mcp-serve-http run-dev run-prod run-mcp docker-build docker-up docker-down docker-logs docker-prod-config docker-npm-config

.DEFAULT_GOAL := help

DOCKER_COMPOSE := $(shell if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; else echo "docker compose"; fi)

help: ## Display this help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Install project and development dependencies with uv
	uv sync --group dev

sync: install ## Alias for install

lock: ## Resolve and update uv.lock
	uv lock

upgrade: ## Upgrade locked dependencies
	uv lock --upgrade

format: ## Format Python code
	uv run ruff format clingen_link tests server.py mcp_server.py

format-check: ## Check formatting without writing
	uv run ruff format --check clingen_link tests server.py mcp_server.py

lint: ## Lint Python code
	uv run ruff check clingen_link tests server.py mcp_server.py

lint-ci: ## Lint Python code without modifying files
	uv run ruff check clingen_link tests server.py mcp_server.py --output-format=github

lint-fix: ## Lint and apply safe fixes
	uv run ruff check clingen_link tests server.py mcp_server.py --fix

lint-loc: ## Enforce per-file line budget (see AGENTS.md "File Size Discipline")
	uv run python scripts/check_file_size.py

typecheck: ## Type check package
	uv run mypy clingen_link server.py mcp_server.py

typecheck-fast: ## Type check with mypy daemon and fallback
	@tmp_log=$$(mktemp); \
	if uv run dmypy run -- clingen_link server.py mcp_server.py >$$tmp_log 2>&1; then \
		cat $$tmp_log; \
	elif grep -Eq "Daemon crashed!|INTERNAL ERROR" $$tmp_log; then \
		echo "dmypy crashed; retrying with a fresh daemon..."; \
		uv run dmypy stop >/dev/null 2>&1 || true; \
		if uv run dmypy run -- clingen_link server.py mcp_server.py >$$tmp_log 2>&1; then \
			cat $$tmp_log; \
		else \
			cat $$tmp_log; \
			echo "Falling back to plain mypy..."; \
			uv run dmypy stop >/dev/null 2>&1 || true; \
			uv run mypy clingen_link server.py mcp_server.py; \
		fi; \
	else \
		cat $$tmp_log; \
		rm -f $$tmp_log; \
		exit 1; \
	fi; \
	rm -f $$tmp_log

typecheck-stop: ## Stop mypy daemon
	uv run dmypy stop

typecheck-fresh: ## Clear mypy cache and run typecheck
	rm -rf .mypy_cache
	uv run mypy clingen_link server.py mcp_server.py

test: ## Run deterministic unit tests quickly
	uv run pytest tests/unit -q

test-fast: ## Run deterministic unit tests in parallel with pytest-xdist
	uv run pytest tests/unit -q -n auto

test-unit: ## Run unit tests in parallel
	uv run pytest tests/unit -q -n auto

test-integration: ## Run live integration tests against ClinGen
	uv run pytest tests/integration -q

test-cov: ## Run unit tests with coverage
	uv run pytest tests/unit --cov=clingen_link --cov-report=term-missing --cov-report=html --cov-report=xml

test-all: test-cov ## Alias for full test run with coverage

check: format lint ## Format and lint

ci-local: format-check lint-ci lint-loc typecheck-fast test-fast ## Run fast local CI-equivalent checks

precommit: ci-local ## Run checks expected before commit

clean: ## Remove local caches and generated reports
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml

dev: ## Run FastAPI host (/health) + mounted MCP HTTP locally
	uv run python server.py --transport unified --host 127.0.0.1 --port 8000

mcp-serve: ## Start local stdio MCP server
	uv run python mcp_server.py

mcp-serve-http: dev ## Alias: FastAPI host (/health) + mounted MCP HTTP locally

run-dev: dev ## Backwards-compatible alias for dev

run-prod: ## Run production server with uvicorn
	uv run python server.py --transport unified --host 0.0.0.0 --port 8000

run-mcp: mcp-serve ## Backwards-compatible alias for mcp-serve

docker-build: ## Build Docker image
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml build

docker-up: ## Start Docker development stack
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml up -d

docker-down: ## Stop Docker development stack
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml down

docker-logs: ## Follow Docker logs
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml logs -f

docker-prod-config: ## Render production Compose configuration
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml -f docker/docker-compose.prod.yml config

docker-npm-config: ## Render NPM Compose configuration
	$(DOCKER_COMPOSE) --env-file .env.docker.example -f docker/docker-compose.yml -f docker/docker-compose.prod.yml -f docker/docker-compose.npm.yml config
