.PHONY: help memory-router oncall-agent policy-analyst team-pipeline self-tuner librarian researcher supply-chain supply-chain-check \
        care-coordination care-coordination-compare \
        test test-memory-router test-oncall-agent test-policy-analyst test-team-pipeline test-self-tuner test-librarian test-researcher test-supply-chain test-care-coordination

MEMORY_ROUTER    := apps/memory_router
POLICY_ANALYST   := apps/policy_analyst
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

policy-analyst: ## Policy analyst: bi-temporal setup + adjudication, then two-arm evaluation
	PYTHONPATH=apps $(WITH_DEPS) $(POLICY_ANALYST)/requirements.txt python -m policy_analyst

team-pipeline: ## Team pipeline: handoffs, event-driven reviewer, shared team lessons
	PYTHONPATH=apps $(WITH_DEPS) $(POLICY_ANALYST)/requirements.txt python -m team_pipeline

self-tuner: ## Self-tuner: prompt versioning, optimize, A/B activation, guardrail rule, skill
	PYTHONPATH=apps $(WITH_DEPS) $(POLICY_ANALYST)/requirements.txt python -m self_tuner

librarian: ## Librarian: consolidation distillation + archive/dereference reversibility (fast-consolidation server)
	PYTHONPATH=apps $(WITH_DEPS) $(POLICY_ANALYST)/requirements.txt python -m librarian

researcher: ## Researcher: checkpoint survival across compaction wipes, two arms
	PYTHONPATH=apps $(WITH_DEPS) $(POLICY_ANALYST)/requirements.txt python -m researcher

supply-chain: ## Supply chain agent: web page on 127.0.0.1 (PORT, default 7870)
	cd $(SUPPLY_CHAIN) && $(WITH_DEPS) requirements.txt python -m uvicorn app:app --host 127.0.0.1 --port $(PORT)

supply-chain-check: ## Supply chain agent: teach, then compare in a new process, without the page
	cd $(SUPPLY_CHAIN) && $(WITH_DEPS) requirements.txt python check_live.py

care-coordination: ## Care coordination agent: web page on 127.0.0.1 (PORT, default 7880)
	cd $(CARE_COORD) && $(WITH_DEPS) requirements.txt python -m uvicorn app:app --host 127.0.0.1 --port $(PORT)

care-coordination-compare: ## Care coordination agent: 3-arm comparison on a fresh experiment
	cd $(CARE_COORD) && $(WITH_DEPS) requirements.txt python compare.py --experiment cc-$(shell date +%Y%m%d%H%M%S)

# --- Offline tests (no keys; model and memory are test doubles) ---

test: test-memory-router test-oncall-agent test-policy-analyst test-team-pipeline test-self-tuner test-librarian test-researcher test-supply-chain test-care-coordination ## Run every offline suite

test-memory-router: ## Offline tests for the memory router (incl. the tools-mode round-trip)
	cd $(MEMORY_ROUTER) && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v

test-oncall-agent: ## Offline tests for the on-call triage agent
	cd $(ONCALL_AGENT) && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v

test-policy-analyst: ## Offline tests for the policy analyst
	cd $(POLICY_ANALYST) && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v

test-team-pipeline: ## Offline tests for the team pipeline
	cd apps/team_pipeline && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v

test-self-tuner: ## Offline tests for the self-tuner
	cd apps/self_tuner && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v

test-librarian: ## Offline tests for the librarian
	cd apps/librarian && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v

test-researcher: ## Offline tests for the researcher
	cd apps/researcher && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v

test-supply-chain: ## Offline tests for the supply chain agent (Python and Node.js)
	cd $(SUPPLY_CHAIN) && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v
	cd $(SUPPLY_CHAIN) && node --test test_api.cjs

test-care-coordination: ## Offline tests for the care coordination agent (Python and Node.js)
	cd $(CARE_COORD) && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v
	cd $(CARE_COORD) && node --test test_api.cjs
