"""Pipeline orchestrator — coordinator -> researchers -> evaluator -> recommender."""

from concurrent.futures import ThreadPoolExecutor, as_completed

from .agents import Coordinator, Researcher, Evaluator, Recommender
from .memory import Memory


def _run_parallel(researchers: list[Researcher], plan: str) -> dict[str, str]:
    """Run researchers in parallel using threads."""
    results = {}
    with ThreadPoolExecutor(max_workers=len(researchers)) as executor:
        futures = {executor.submit(r.run, plan): r.name for r in researchers}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                results[name] = f"Error: {e}"
    return results


def _safe(fn, label=""):
    """Run fn(), swallow exceptions so the pipeline keeps going."""
    try:
        return fn()
    except Exception as e:
        if label:
            print(f"  {label}: {e}")
        return None


class DiscoveryPipeline:
    def __init__(self, memory: Memory):
        self.memory = memory
        self.coordinator = Coordinator()
        self.researchers = [
            Researcher("payments"),
            Researcher("billing"),
            Researcher("fraud"),
        ]
        self.evaluator = Evaluator()
        self.recommender = Recommender()

    def all_agents(self):
        return [self.coordinator, *self.researchers, self.evaluator, self.recommender]

    def run(self, query: str) -> str:
        # 1. Recall prior research
        print(f"\n  recall() -- searching for prior research...")
        prior = ""
        try:
            prior = self.memory.recall_prior(
                "B2B SaaS payments billing subscription tools",
                entry_types=["fact", "lesson"],
            )
            if prior:
                print(f"  Found prior research ({len(prior.splitlines())} entries)")
            else:
                print(f"  No prior research found -- starting fresh.")
        except Exception as e:
            print(f"  recall(): {e}")

        # 2. Coordinator plans
        print(f"\n  [coordinator] Planning research strategy...")
        plan = self.coordinator.run(query, prior_context=prior)
        preview = plan[:300].replace("\n", " ")
        print(f"  [coordinator] {preview}{'...' if len(plan) > 300 else ''}")
        _safe(lambda: self.memory.store_finding("coordinator", plan, intent="trace"),
              "store_finding(coordinator)")

        # 3. Researchers search in parallel
        print(f"\n  Running {len(self.researchers)} researchers in parallel...")
        findings = _run_parallel(self.researchers, plan)
        for name, finding in findings.items():
            preview = finding[:200].replace("\n", " ")
            print(f"  [{name}] {preview}{'...' if len(finding) > 200 else ''}")
            _safe(lambda n=name, f=finding: self.memory.store_finding(n, f, intent="fact", importance="high"))
            _safe(lambda n=name: self.memory.store_handoff("coordinator", n, f"Research assigned: {n}"))

        # 4. Evaluator scores
        print(f"\n  [evaluator] Scoring tools...")
        mubit_context = _safe(lambda: self.memory.get_context("tool evaluation scoring")) or ""
        evaluation = self.evaluator.run(findings, mubit_context)
        preview = evaluation[:300].replace("\n", " ")
        print(f"  [evaluator] {preview}{'...' if len(evaluation) > 300 else ''}")
        _safe(lambda: self.memory.store_finding("evaluator", evaluation, intent="lesson", importance="high"))

        # Handoff + feedback: evaluator -> recommender
        handoff_id = _safe(lambda: self.memory.store_handoff("evaluator", "recommender", "Evaluation complete"))
        if handoff_id:
            _safe(lambda: self.memory.store_feedback(
                handoff_id, "approve",
                "Evaluation matrix is comprehensive. Proceeding with recommendation.",
            ))

        # 5. Recommender synthesizes
        print(f"\n  [recommender] Synthesizing final recommendation...")
        recommendation = self.recommender.run(evaluation, mubit_context)
        preview = recommendation[:300].replace("\n", " ")
        print(f"  [recommender] {preview}{'...' if len(recommendation) > 300 else ''}")
        _safe(lambda: self.memory.store_finding("recommender", recommendation, intent="lesson",
                                                importance="high"))

        # Archive the report
        ref = _safe(lambda: self.memory.archive_report(recommendation))
        if ref:
            print(f"  archive() -- reference: {ref}")
            exact = _safe(lambda: self.memory.verify_archive(ref))
            if exact and exact.get("found"):
                print(f"  dereference() -- verified ({len(exact.get('content', ''))} chars)")

        # 6. Post-run
        _safe(lambda: self.memory.checkpoint("pipeline-complete", f"Query: {query[:80]}"))
        _safe(lambda: self.memory.record_success(f"Pipeline completed for: {query[:80]}"))

        return recommendation
