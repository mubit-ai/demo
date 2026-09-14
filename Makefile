.PHONY: help memory-router supply-chain supply-chain-check test test-memory-router test-supply-chain

MEMORY_ROUTER := apps/memory_router
SUPPLY_CHAIN  := apps/supply_chain_agent
WITH_DEPS     := uv run --no-project --with-requirements
PORT          ?= 7870

# A .env at the repository root configures both demos. A .env inside a demo
# folder also works when you run that demo from its folder; under make, the
# root file takes precedence because its values are already in the environment.
ifneq (,$(wildcard .env))
include .env
export
endif

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(firstword $(MAKEFILE_LIST)) | awk 'BEGIN {FS = ":.*##"}; {printf "  %-20s %s\n", $$1, $$2}'

# --- Live demos (need MUBIT_ENDPOINT, MUBIT_API_KEY and GEMINI_API_KEY) ---

memory-router: ## Memory router: teach, then compare memory off and on in a new process
	PYTHONPATH=apps $(WITH_DEPS) $(MEMORY_ROUTER)/requirements.txt python -m memory_router

supply-chain: ## Supply chain agent: web page on 127.0.0.1 (PORT, default 7870)
	cd $(SUPPLY_CHAIN) && $(WITH_DEPS) requirements.txt python -m uvicorn app:app --host 127.0.0.1 --port $(PORT)

supply-chain-check: ## Supply chain agent: teach, then compare in a new process, without the page
	cd $(SUPPLY_CHAIN) && $(WITH_DEPS) requirements.txt python -m tests.check_live

# --- Offline tests (no keys; model and memory are test doubles) ---

test: test-memory-router test-supply-chain ## Run the offline tests of both demos

test-memory-router: ## Offline tests for the memory router
	cd $(MEMORY_ROUTER) && $(WITH_DEPS) requirements.txt python -m unittest discover -s tests -t . -v

test-supply-chain: ## Offline tests for the supply chain agent (Python and Node.js)
	cd $(SUPPLY_CHAIN) && $(WITH_DEPS) requirements.txt python -m unittest discover -s tests -t . -v
	cd $(SUPPLY_CHAIN) && node --test tests/test_api.cjs
