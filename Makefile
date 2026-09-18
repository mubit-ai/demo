.PHONY: help memory-router oncall-agent supply-chain supply-chain-check \
        care-coordination care-coordination-compare \
        test test-memory-router test-oncall-agent test-supply-chain test-care-coordination

MEMORY_ROUTER    := apps/memory_router
ONCALL_AGENT     := apps/oncall_agent
SUPPLY_CHAIN     := apps/supply_chain_agent
CARE_COORD       := apps/care_coordination_agent
WITH_DEPS        := uv run --no-project --with-requirements
PORT             ?= 7870

# A .env at the repository root configures every demo. A .env inside a demo
# folder also works when you run that demo from its folder; under make, the
# root file takes precedence because its values are already in the environment.
ifneq (,$(wildcard .env))
include .env
export
endif

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(firstword $(MAKEFILE_LIST)) | awk 'BEGIN {FS = ":.*##"}; {printf "  %-28s %s\n", $$1, $$2}'

# --- Live demos (need MUBIT_ENDPOINT, MUBIT_API_KEY and GEMINI_API_KEY) ---

memory-router: ## Memory router: teach, then compare memory off and on in a new process
	PYTHONPATH=apps $(WITH_DEPS) $(MEMORY_ROUTER)/requirements.txt python -m memory_router

oncall-agent: ## On-call triage: observe + reflect, then apply + attribute, then evaluate
	PYTHONPATH=apps $(WITH_DEPS) $(ONCALL_AGENT)/requirements.txt python -m oncall_agent

supply-chain: ## Supply chain agent: web page on 127.0.0.1 (PORT, default 7870)
	cd $(SUPPLY_CHAIN) && $(WITH_DEPS) requirements.txt python -m uvicorn app:app --host 127.0.0.1 --port $(PORT)

supply-chain-check: ## Supply chain agent: teach, then compare in a new process, without the page
	cd $(SUPPLY_CHAIN) && $(WITH_DEPS) requirements.txt python check_live.py

care-coordination: ## Care coordination agent: web page on 127.0.0.1 (PORT, default 7880)
	cd $(CARE_COORD) && $(WITH_DEPS) requirements.txt python -m uvicorn app:app --host 127.0.0.1 --port $(PORT)

care-coordination-compare: ## Care coordination agent: 3-arm comparison on a fresh experiment
	cd $(CARE_COORD) && $(WITH_DEPS) requirements.txt python compare.py --experiment cc-$(shell date +%Y%m%d%H%M%S)

# --- Offline tests (no keys; model and memory are test doubles) ---

test: test-memory-router test-oncall-agent test-supply-chain test-care-coordination ## Run every offline suite

test-memory-router: ## Offline tests for the memory router (incl. the tools-mode round-trip)
	cd $(MEMORY_ROUTER) && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v

test-oncall-agent: ## Offline tests for the on-call triage agent
	cd $(ONCALL_AGENT) && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v

test-supply-chain: ## Offline tests for the supply chain agent (Python and Node.js)
	cd $(SUPPLY_CHAIN) && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v
	cd $(SUPPLY_CHAIN) && node --test test_api.cjs

test-care-coordination: ## Offline tests for the care coordination agent (Python and Node.js)
	cd $(CARE_COORD) && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v
	cd $(CARE_COORD) && node --test test_api.cjs
