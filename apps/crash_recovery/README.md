# Technology Due Diligence — Crash Recovery & Resumption

A multi-agent system that performs technology due diligence for a SaaS acquisition.
The pipeline **crashes mid-run**, and Mubit memory enables the next run to
**detect the crash, restore state, and resume from where it left off**.

## Architecture

```
Run 1 (Crashes after Phase 3):

  Phase 1: Market Analysis ──── MarketAnalyst (Google Search) ──── checkpoint ✓
      │
  Phase 2: Tech Assessment ──── TechAssessor (Google Search) ───── checkpoint ✓
      │
  Phase 3: Competitive Intel ── CompetitiveIntel (Google Search) ─ checkpoint ✓
      │
  Phase 4: Risk Analysis ────── RiskAnalyst ──── ✖ CRASH
      │
      └── crash state saved: variables, goals, step outcomes, activity

Recovery Detection (9 APIs):
  list_variables → list_goals → get_run_snapshot → list_activity
  → diagnose → get_action_log → get_goal_tree → get_cycle_history
  → list_concepts

Run 2 (Resumes from Phase 4):

  [link_run → recall prior outputs → skip Phases 1-3]
      │
  Phase 4: Risk Analysis ────── RiskAnalyst ────────────────────── checkpoint ✓
      │
  Phase 5: Financial Modeling ── FinancialModeler ─────────────── checkpoint ✓
      │
  Phase 6: Executive Report ──── ReportWriter ─────────────────── archive ✓
```

## Crash Recovery Mechanism

The recovery works through **working memory variables** and **goals**:

1. **State Variables** — `set_variable()` tracks `pipeline_status`, `current_phase`,
   `completed_phases`, `crash_error`. On resume, `list_variables()` reveals exactly
   what happened.

2. **Goal Tree** — Each phase has a goal. `list_goals()` shows 3 achieved, 1 failed,
   2 pending — immediately telling the resume run where to pick up.

3. **Run Linking** — `link_run()` connects the crash session to the resume session,
   enabling `recall(include_linked_runs=True)` to find phase outputs across runs.

4. **Step Outcomes** — `record_step_outcome()` with positive/negative signals creates
   a per-phase reward history that `reflect(include_step_outcomes=True)` can learn from.

## Mubit APIs Used (31 unique, 16 NEW)

| API | Purpose | New? |
|-----|---------|------|
| `remember()` | Store phase outputs | |
| `recall()` | Recover prior phase outputs | |
| `get_context()` | Inject findings into agent prompts | |
| `checkpoint()` | Snapshot after each phase | |
| `archive()` / `dereference()` | Store and verify executive report | |
| `register_agent()` / `list_agents()` | Agent management | |
| `handoff()` / `feedback()` | Inter-phase coordination | |
| `record_outcome()` | Final success signal | |
| `reflect()` | Post-run lesson extraction | |
| `surface_strategies()` | Pattern clustering | |
| `memory_health()` | Pre/post crash comparison | |
| `control.set_variable()` | Track pipeline state | **YES** |
| `control.get_variable()` | Read state on resume | **YES** |
| `control.list_variables()` | Enumerate all state | **YES** |
| `control.add_goal()` | Create goal tree | **YES** |
| `control.update_goal()` | Mark achieved/failed | **YES** |
| `control.list_goals()` | Check completion | **YES** |
| `control.get_goal_tree()` | Hierarchical view | **YES** |
| `control.record_step_outcome()` | Per-phase rewards | **YES** |
| `control.agent_heartbeat()` | Agent liveness | **YES** |
| `control.link_run()` | Cross-run linking | **YES** |
| `control.append_activity()` | Activity records | **YES** |
| `control.list_activity()` | Browse timeline | **YES** |
| `control.export_activity()` | Export timeline | **YES** |
| `control.submit_action()` | Log decisions | **YES** |
| `control.get_action_log()` | Review decisions | **YES** |
| `control.context_snapshot()` | Full run snapshot | **YES** |
| `control.run_cycle()` | Decision cycle | **YES** |
| `control.get_cycle_history()` | Cycle history | **YES** |
| `control.define_concept()` | Schema definitions | **YES** |
| `control.list_concepts()` | List schemas | **YES** |
| `diagnose()` | Error pattern analysis | **YES** |

## Running

```bash
cd demo
export MUBIT_ENDPOINT=https://api.mubit.ai
export MUBIT_API_KEY="mbt_..."
export GOOGLE_API_KEY="..."

make resilience

# Optional: control which phase crashes
export CRASH_AFTER_PHASE=2  # crash after Phase 3 (0-indexed)
make resilience
```
