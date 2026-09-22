.PHONY: help memory-router oncall-agent policy-analyst team-pipeline self-tuner librarian researcher data-analyst helpdesk soc-triage code-reviewer supply-chain supply-chain-check \
        care-coordination care-coordination-compare abcd-tickets abcd-tickets-check \
        test test-abcd-check test-memory-router test-oncall-agent test-policy-analyst test-team-pipeline test-self-tuner test-librarian test-researcher test-data-analyst test-helpdesk test-soc-triage test-code-reviewer test-supply-chain test-care-coordination

MEMORY_ROUTER    := apps/memory_router
POLICY_ANALYST   := apps/policy_analyst
ONCALL_AGENT     := apps/oncall_agent
SUPPLY_CHAIN     := apps/supply_chain_agent
CARE_COORD       := apps/care_coordination_agent
ABCD_CHECK       := apps/abcd_check
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

data-analyst: ## Data analyst: tribal-knowledge rules, schema drift, two arms
	PYTHONPATH=apps $(WITH_DEPS) $(POLICY_ANALYST)/requirements.txt python -m data_analyst

helpdesk: ## Internal helpdesk: per-user memory, runbooks, admin guardrail
	PYTHONPATH=apps $(WITH_DEPS) $(POLICY_ANALYST)/requirements.txt python -m helpdesk

soc-triage: ## SOC triage: benign-pattern lessons, drift re-classification
	PYTHONPATH=apps $(WITH_DEPS) $(POLICY_ANALYST)/requirements.txt python -m soc_triage

code-reviewer: ## Code reviewer: convention lessons, incident-linked findings
	PYTHONPATH=apps $(WITH_DEPS) $(POLICY_ANALYST)/requirements.txt python -m code_reviewer

supply-chain: ## Supply chain agent: web page on 127.0.0.1 (PORT, default 7870)
	cd $(SUPPLY_CHAIN) && $(WITH_DEPS) requirements.txt python -m uvicorn app:app --host 127.0.0.1 --port $(PORT)

supply-chain-check: ## Supply chain agent: teach, then compare in a new process, without the page
	cd $(SUPPLY_CHAIN) && $(WITH_DEPS) requirements.txt python check_live.py

care-coordination: ## Care coordination agent: web page on 127.0.0.1 (PORT, default 7880)
	cd $(CARE_COORD) && $(WITH_DEPS) requirements.txt python -m uvicorn app:app --host 127.0.0.1 --port $(PORT)

care-coordination-compare: ## Care coordination agent: 3-arm comparison on a fresh experiment
	cd $(CARE_COORD) && $(WITH_DEPS) requirements.txt python compare.py --experiment cc-$(shell date +%Y%m%d%H%M%S)

# --- ABCD check (typed decisions on real support tickets; no keys for the ticket set) ---

abcd-tickets: ## ABCD check: fetch the ABCD dataset (MIT) into apps/abcd_check/data/abcd and rebuild the pinned fifty-ticket file
	cd $(ABCD_CHECK) && $(WITH_DEPS) requirements.txt python tickets.py

abcd-tickets-check: ## ABCD check: confirm the committed ticket file is reproduced byte for byte from the raw data
	cd $(ABCD_CHECK) && $(WITH_DEPS) requirements.txt python tickets.py --check

# --- Offline tests (no keys; model and memory are test doubles) ---

test: test-memory-router test-oncall-agent test-policy-analyst test-team-pipeline test-self-tuner test-librarian test-researcher test-data-analyst test-helpdesk test-soc-triage test-code-reviewer test-supply-chain test-care-coordination test-abcd-check ## Run every offline suite

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

test-data-analyst: ## Offline tests for the data analyst
	cd apps/data_analyst && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v

test-helpdesk: ## Offline tests for the helpdesk
	cd apps/helpdesk && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v

test-soc-triage: ## Offline tests for the SOC triage agent
	cd apps/soc_triage && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v

test-code-reviewer: ## Offline tests for the code reviewer
	cd apps/code_reviewer && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v

test-supply-chain: ## Offline tests for the supply chain agent (Python and Node.js)
	cd $(SUPPLY_CHAIN) && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v
	cd $(SUPPLY_CHAIN) && node --test test_api.cjs

test-care-coordination: ## Offline tests for the care coordination agent (Python and Node.js)
	cd $(CARE_COORD) && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v
	cd $(CARE_COORD) && node --test test_api.cjs

test-abcd-check: ## Offline tests for the ABCD check (ticket extraction, ground truth, seeded selection)
	cd $(ABCD_CHECK) && $(WITH_DEPS) requirements.txt python -m unittest test_demo -v
