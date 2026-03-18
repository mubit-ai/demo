.PHONY: install preseed learn learn-notebook agents agents-notebook langgraph crewai langchain adk help

INTEGRATIONS := ../integrations/python

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
	cd $(INTEGRATIONS)/mubit_langgraph/examples/code_review && uv run python main.py

crewai: ## Run CrewAI support ticket triage example
	cd $(INTEGRATIONS)/mubit_crewai/examples/support_triage && uv run python main.py

langchain: ## Run LangChain research assistant example
	cd $(INTEGRATIONS)/mubit_langchain/examples/research_assistant && uv run python main.py

adk: ## Run Google ADK travel planner example
	cd $(INTEGRATIONS)/mubit_adk/examples/travel_planner && uv run python main.py
