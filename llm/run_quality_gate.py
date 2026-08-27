"""Run the stable linting and type-check gates used by CI."""

from __future__ import annotations

from pathlib import Path
import argparse
import os
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYLINT_HOME = PROJECT_ROOT / "tmp_logs" / ".pylint"

# Sprint 6.B closure pass (2026-05-11): FORMAT_TARGETS and PYLINT_TARGETS
# expanded to cover every module already in MYPY_TARGETS, so the same
# 60+-module surface is checked by all three gates.  Keep the lists in
# lockstep when adding new modules; the disable rules in pyproject.toml
# under ``[tool.pylint."messages control"]`` already cover the
# false-positive patterns surfaced during the 6.B closure (relative
# imports, ungrouped imports, lazy imports, too-many-positional-args,
# the slowapi ``request`` parameter pattern, etc).
FORMAT_TARGETS = [
    "scripts/run_all_tests.py",
    "llm/cache_keys.py",
    "llm/log_config.py",
    "llm/metrics.py",
    "llm/observability.py",
    "llm/position_input.py",
    "llm/run_all_tests.py",
    "llm/run_ci_suite.py",
    "llm/run_quality_gate.py",
    "llm/tests/test_ci_pipeline.py",
    "llm/tests/test_container_hardening.py",
    "llm/rag/contracts/validate_output.py",
    "llm/rag/llm/config.py",
    "llm/rag/llm/fake.py",
    "llm/rag/quality/explanation_score.py",
    "llm/rag/tests/contracts/test_violations_corpus.py",
    "llm/rag/validators/explain_response_schema.py",
    "llm/rag/validators/mode_2_negative.py",
    "llm/rag/validators/mode_2_semantic.py",
    "llm/rag/validators/mode_2_structure.py",
    "llm/rag/validators/sanitize.py",
    "llm/seca/adaptation/coupling.py",
    "llm/seca/adaptation/opponent_policy.py",
    "llm/seca/adaptation/skill_profile.py",
    "llm/seca/adaptation/teaching_policy.py",
    "llm/seca/analytics/events.py",
    "llm/seca/analytics/logger.py",
    "llm/seca/analytics/mistake_stats.py",
    "llm/seca/analytics/models.py",
    "llm/seca/analytics/router.py",
    "llm/seca/analytics/training_recommendations.py",
    "llm/seca/auth/api_key.py",
    "llm/seca/auth/erasure.py",
    "llm/seca/auth/export.py",
    "llm/seca/auth/hashing.py",
    "llm/seca/auth/models.py",
    "llm/seca/auth/router.py",
    "llm/seca/auth/service.py",
    "llm/seca/auth/tokens.py",
    "llm/seca/auth/web_deletion.py",
    "llm/seca/legal/router.py",
    "llm/seca/chat/__init__.py",
    "llm/seca/chat/models.py",
    "llm/seca/chat/repo.py",
    "llm/seca/curriculum/generator.py",
    "llm/seca/curriculum/models.py",
    "llm/seca/curriculum/policy.py",
    "llm/seca/curriculum/priority_model.py",
    "llm/seca/curriculum/router.py",
    "llm/seca/curriculum/scheduler.py",
    "llm/seca/curriculum/task_selector.py",
    "llm/seca/curriculum/types.py",
    "llm/seca/engines/stockfish/pool.py",
    "llm/seca/events/models.py",
    "llm/seca/events/storage.py",
    "llm/seca/explainer/safe_explainer.py",
    "llm/seca/feedback/__init__.py",
    "llm/seca/feedback/models.py",
    "llm/seca/feedback/router.py",
    "llm/seca/moderation/models.py",
    "llm/seca/moderation/router.py",
    "llm/seca/inference/router.py",
    "llm/seca/learning/skill_update.py",
    "llm/seca/lichess/__init__.py",
    "llm/seca/lichess/analysis_service.py",
    "llm/seca/lichess/client.py",
    "llm/seca/lichess/import_service.py",
    "llm/seca/lichess/models.py",
    "llm/seca/lichess/router.py",
    "llm/seca/mistakes/__init__.py",
    "llm/seca/mistakes/detector.py",
    "llm/seca/mistakes/router.py",
    "llm/seca/mistakes/verify.py",
    "llm/seca/notifications/__init__.py",
    "llm/seca/notifications/models.py",
    "llm/seca/notifications/router.py",
    "llm/seca/notifications/service.py",
    "llm/seca/coach/study_plan/__init__.py",
    "llm/seca/coach/study_plan/models.py",
    "llm/seca/coach/study_plan/agent.py",
    "llm/seca/coach/study_plan/router.py",
    "llm/seca/coach/study_plan/verdict.py",
    "llm/seca/coach/study_plan/library.py",
    "llm/seca/coach/study_plan/lichess_puzzles.py",
    "llm/seca/puzzles/__init__.py",
    "llm/seca/puzzles/router.py",
    "llm/seca/repertoire/router.py",
    "llm/seca/review/__init__.py",
    "llm/seca/review/models.py",
    "llm/seca/review/moments.py",
    "llm/seca/review/router.py",
    "llm/seca/review/service.py",
    "llm/seca/review/writer.py",
    "llm/seca/runtime/safe_mode.py",
    "llm/seca/safety/freeze.py",
    "llm/seca/shared_limiter.py",
    "llm/seca/storage/db.py",
    "llm/seca/storage/models.py",
    "llm/seca/storage/repo.py",
    "llm/seca/training/__init__.py",
    "llm/seca/training/models.py",
    "llm/seca/training/router.py",
    "llm/seca/world_model/safe_stub.py",
]

PYLINT_TARGETS = [
    "scripts/run_all_tests.py",
    "llm/cache_keys.py",
    "llm/log_config.py",
    "llm/metrics.py",
    "llm/observability.py",
    "llm/position_input.py",
    "llm/run_all_tests.py",
    "llm/run_ci_suite.py",
    "llm/run_quality_gate.py",
    "llm/tests/test_ci_pipeline.py",
    "llm/tests/test_container_hardening.py",
    "llm/rag/contracts/validate_output.py",
    "llm/rag/llm/config.py",
    "llm/rag/llm/fake.py",
    "llm/rag/quality/explanation_score.py",
    "llm/rag/tests/contracts/test_violations_corpus.py",
    "llm/rag/validators/explain_response_schema.py",
    "llm/rag/validators/mode_2_negative.py",
    "llm/rag/validators/mode_2_semantic.py",
    "llm/rag/validators/mode_2_structure.py",
    "llm/rag/validators/sanitize.py",
    "llm/seca/adaptation/coupling.py",
    "llm/seca/adaptation/opponent_policy.py",
    "llm/seca/adaptation/skill_profile.py",
    "llm/seca/adaptation/teaching_policy.py",
    "llm/seca/analytics/events.py",
    "llm/seca/analytics/logger.py",
    "llm/seca/analytics/mistake_stats.py",
    "llm/seca/analytics/models.py",
    "llm/seca/analytics/router.py",
    "llm/seca/analytics/training_recommendations.py",
    "llm/seca/auth/api_key.py",
    "llm/seca/auth/erasure.py",
    "llm/seca/auth/export.py",
    "llm/seca/auth/hashing.py",
    "llm/seca/auth/models.py",
    "llm/seca/auth/router.py",
    "llm/seca/auth/service.py",
    "llm/seca/auth/tokens.py",
    "llm/seca/auth/web_deletion.py",
    "llm/seca/legal/router.py",
    "llm/seca/chat/__init__.py",
    "llm/seca/chat/models.py",
    "llm/seca/chat/repo.py",
    "llm/seca/curriculum/generator.py",
    "llm/seca/curriculum/models.py",
    "llm/seca/curriculum/policy.py",
    "llm/seca/curriculum/priority_model.py",
    "llm/seca/curriculum/router.py",
    "llm/seca/curriculum/scheduler.py",
    "llm/seca/curriculum/task_selector.py",
    "llm/seca/curriculum/types.py",
    "llm/seca/engines/stockfish/pool.py",
    "llm/seca/events/models.py",
    "llm/seca/events/router.py",
    "llm/seca/events/storage.py",
    "llm/seca/explainer/safe_explainer.py",
    "llm/seca/feedback/__init__.py",
    "llm/seca/feedback/models.py",
    "llm/seca/feedback/router.py",
    "llm/seca/moderation/models.py",
    "llm/seca/moderation/router.py",
    "llm/seca/inference/router.py",
    "llm/seca/learning/skill_update.py",
    "llm/seca/lichess/__init__.py",
    "llm/seca/lichess/analysis_service.py",
    "llm/seca/lichess/client.py",
    "llm/seca/lichess/import_service.py",
    "llm/seca/lichess/models.py",
    "llm/seca/lichess/router.py",
    "llm/seca/mistakes/__init__.py",
    "llm/seca/mistakes/detector.py",
    "llm/seca/mistakes/router.py",
    "llm/seca/mistakes/verify.py",
    "llm/seca/notifications/__init__.py",
    "llm/seca/notifications/models.py",
    "llm/seca/notifications/router.py",
    "llm/seca/notifications/service.py",
    "llm/seca/coach/study_plan/__init__.py",
    "llm/seca/coach/study_plan/models.py",
    "llm/seca/coach/study_plan/agent.py",
    "llm/seca/coach/study_plan/router.py",
    "llm/seca/coach/study_plan/verdict.py",
    "llm/seca/coach/study_plan/library.py",
    "llm/seca/coach/study_plan/lichess_puzzles.py",
    "llm/seca/puzzles/__init__.py",
    "llm/seca/puzzles/router.py",
    "llm/seca/repertoire/router.py",
    "llm/seca/review/__init__.py",
    "llm/seca/review/models.py",
    "llm/seca/review/moments.py",
    "llm/seca/review/router.py",
    "llm/seca/review/service.py",
    "llm/seca/review/writer.py",
    "llm/seca/runtime/safe_mode.py",
    "llm/seca/safety/freeze.py",
    "llm/seca/shared_limiter.py",
    "llm/seca/skills/updater.py",
    "llm/seca/storage/db.py",
    "llm/seca/storage/models.py",
    "llm/seca/storage/repo.py",
    "llm/seca/training/__init__.py",
    "llm/seca/training/models.py",
    "llm/seca/training/router.py",
    "llm/seca/world_model/safe_stub.py",
]

MYPY_TARGETS = [
    "scripts/run_all_tests.py",
    "llm/log_config.py",
    "llm/metrics.py",
    "llm/observability.py",
    "llm/position_input.py",
    "llm/run_all_tests.py",
    "llm/run_ci_suite.py",
    "llm/run_quality_gate.py",
    "llm/tests/test_ci_pipeline.py",
    "llm/tests/test_container_hardening.py",
    "llm/rag/contracts/validate_output.py",
    "llm/rag/llm/config.py",
    "llm/rag/llm/fake.py",
    "llm/rag/quality/explanation_score.py",
    "llm/rag/tests/contracts/test_violations_corpus.py",
    "llm/rag/validators/explain_response_schema.py",
    "llm/rag/validators/mode_2_negative.py",
    "llm/rag/validators/mode_2_semantic.py",
    "llm/rag/validators/mode_2_structure.py",
    "llm/rag/validators/sanitize.py",
    # Sprint 6.A — SECA tree expansion.  Modules whose direct + transitive
    # imports type-check cleanly with the existing global mypy config.
    # Modules that pull in storage/repo.py (legacy Column-style models) or
    # coach/__init__.py (live LLM pipeline imports with looser typing) are
    # deferred to a follow-up sprint that migrates those bases.
    "llm/seca/adaptation/coupling.py",
    "llm/seca/adaptation/opponent_policy.py",
    "llm/seca/adaptation/skill_profile.py",
    "llm/seca/adaptation/teaching_policy.py",
    "llm/seca/analytics/events.py",
    "llm/seca/analytics/logger.py",
    "llm/seca/analytics/mistake_stats.py",
    "llm/seca/analytics/training_recommendations.py",
    "llm/seca/auth/api_key.py",
    "llm/seca/auth/erasure.py",
    "llm/seca/auth/export.py",
    "llm/seca/auth/hashing.py",
    "llm/seca/auth/models.py",
    "llm/seca/auth/router.py",
    "llm/seca/auth/service.py",
    "llm/seca/auth/tokens.py",
    "llm/seca/auth/web_deletion.py",
    "llm/seca/legal/router.py",
    "llm/seca/chat/__init__.py",
    "llm/seca/chat/models.py",
    "llm/seca/chat/repo.py",
    "llm/seca/curriculum/generator.py",
    "llm/seca/curriculum/policy.py",
    "llm/seca/curriculum/priority_model.py",
    "llm/seca/curriculum/scheduler.py",
    "llm/seca/curriculum/task_selector.py",
    "llm/seca/curriculum/types.py",
    "llm/seca/engines/stockfish/pool.py",
    "llm/seca/events/models.py",
    "llm/seca/explainer/safe_explainer.py",
    "llm/seca/feedback/__init__.py",
    "llm/seca/feedback/models.py",
    "llm/seca/feedback/router.py",
    "llm/seca/moderation/models.py",
    "llm/seca/moderation/router.py",
    "llm/seca/inference/router.py",
    "llm/seca/learning/skill_update.py",
    "llm/seca/runtime/safe_mode.py",
    "llm/seca/safety/freeze.py",
    "llm/seca/shared_limiter.py",
    "llm/seca/storage/models.py",
    "llm/seca/world_model/safe_stub.py",
    # Sprint 6.A follow-ups — storage models migrated to Mapped[T] unlocked
    # repo.py + downstream routers; a second follow-up (2026-05-12) cleaned
    # up events/router.py by deleting the freeze-guard-forbidden
    # ``if not SAFE_MODE:`` adaptive block + null-guarding skills/updater
    # for the Optional[float] event.accuracy / event.weaknesses_json types
    # introduced in the storage Mapped[T] migration.
    "llm/seca/analytics/models.py",
    "llm/seca/analytics/router.py",
    "llm/seca/curriculum/models.py",
    "llm/seca/curriculum/router.py",
    "llm/seca/events/router.py",
    "llm/seca/events/storage.py",
    "llm/seca/lichess/__init__.py",
    "llm/seca/lichess/analysis_service.py",
    "llm/seca/lichess/client.py",
    "llm/seca/lichess/import_service.py",
    "llm/seca/lichess/models.py",
    "llm/seca/lichess/router.py",
    "llm/seca/mistakes/__init__.py",
    "llm/seca/mistakes/detector.py",
    "llm/seca/mistakes/router.py",
    "llm/seca/mistakes/verify.py",
    "llm/seca/notifications/__init__.py",
    "llm/seca/notifications/models.py",
    "llm/seca/notifications/router.py",
    "llm/seca/notifications/service.py",
    "llm/seca/coach/study_plan/__init__.py",
    "llm/seca/coach/study_plan/models.py",
    "llm/seca/coach/study_plan/agent.py",
    "llm/seca/coach/study_plan/router.py",
    "llm/seca/coach/study_plan/verdict.py",
    "llm/seca/coach/study_plan/library.py",
    "llm/seca/coach/study_plan/lichess_puzzles.py",
    "llm/seca/puzzles/__init__.py",
    "llm/seca/puzzles/router.py",
    "llm/seca/repertoire/router.py",
    "llm/seca/review/__init__.py",
    "llm/seca/review/models.py",
    "llm/seca/review/moments.py",
    "llm/seca/review/router.py",
    "llm/seca/review/service.py",
    "llm/seca/review/writer.py",
    "llm/seca/skills/updater.py",
    "llm/seca/storage/db.py",
    "llm/seca/training/__init__.py",
    "llm/seca/training/models.py",
    "llm/seca/training/router.py",
    "llm/seca/storage/repo.py",
]


def run_step(name: str, cmd: list[str], *, env: dict[str, str] | None = None) -> int:
    print(f"=== RUNNING {name.upper()} ===")
    return subprocess.run(cmd, cwd=PROJECT_ROOT, check=False, env=env).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "steps",
        nargs="*",
        metavar="step",
        help="Run only the selected quality steps.",
    )
    args = parser.parse_args()

    PYLINT_HOME.mkdir(parents=True, exist_ok=True)

    pylint_env = os.environ.copy()
    pylint_env["PYLINTHOME"] = str(PYLINT_HOME)

    steps = [
        (
            "black",
            [sys.executable, "-m", "black", "--check", *FORMAT_TARGETS],
            None,
        ),
        (
            "pylint",
            [sys.executable, "-m", "pylint", "--score=n", *PYLINT_TARGETS],
            pylint_env,
        ),
        (
            "mypy",
            [sys.executable, "-m", "mypy", *MYPY_TARGETS],
            None,
        ),
    ]

    selected_steps = set(args.steps or [])
    invalid_steps = sorted(selected_steps - {"black", "pylint", "mypy"})
    if invalid_steps:
        parser.error(
            "invalid quality step(s): "
            + ", ".join(invalid_steps)
            + ". Choose from: black, pylint, mypy."
        )

    if selected_steps:
        steps = [step for step in steps if step[0] in selected_steps]

    for name, cmd, env in steps:
        code = run_step(name, cmd, env=env)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
