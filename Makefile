.PHONY: install install-all preseed learn learn-notebook agents agents-notebook langgraph crewai langchain adk discovery crash-recovery orchestrator help

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*##"}; {printf "  %-20s %s\n", $$1, $$2}'

install: ## Install dependencies with uv
	uv sync

install-all: ## Install all dependencies including framework integrations
	uv sync --all-extras

# --- Live demos ---

preseed: ## Pre-warm Mubit (run before demos)
	cd live && uv run python scripts/00_preseed.py

learn: ## Run mubit.learn demo (auto-extraction, zero manual calls)
	cd live && uv run python scripts/demo_learn.py

agents: ## Run multi-agent demo (planner/developer/reviewer + Gemini)
	cd live && uv run python scripts/run_all.py

learn-notebook: ## Launch mubit.learn Jupyter notebook
	uv run jupyter notebook live/demo_learn.ipynb

agents-notebook: ## Launch multi-agent Jupyter notebook
	uv run jupyter notebook live/demo.ipynb

# --- Framework integration examples ---

langgraph: ## Run LangGraph code review pipeline example
	cd integrations/langgraph && uv run python main.py

crewai: ## Run CrewAI support ticket triage example
	cd integrations/crewai && uv run python main.py

langchain: ## Run LangChain research assistant example
	cd integrations/langchain && uv run python main.py

adk: ## Run Google ADK travel planner example
	cd integrations/adk && uv run python main.py

discovery: ## Run software discovery app (multi-agent + web search + Mubit)
	PYTHONPATH=apps uv run python -m discovery

crash-recovery: ## Run crash recovery demo (due diligence pipeline with crash + resume)
	PYTHONPATH=apps uv run python -m crash_recovery

orchestrator: ## Run autonomous orchestrator agent (Mubit as tools, LLM-driven)
	PYTHONPATH=apps uv run python -m orchestrator
