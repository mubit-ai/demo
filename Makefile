.PHONY: install preseed learn learn-notebook agents agents-notebook help

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*##"}; {printf "  %-20s %s\n", $$1, $$2}'

install: ## Install dependencies with uv
	uv sync

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
