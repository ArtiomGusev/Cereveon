"""CI test-registration completeness tripwire (CI_REG_01..03).

The per-push CI test set is a hand-maintained allowlist: ``TEST_TARGETS`` in
``llm/run_ci_suite.py``, the groups in ``llm/run_regression_suite.py``, and the
explicit ``pytest`` steps in ``.github/workflows/``.  A new ``test_*.py`` that
nobody adds to that allowlist is *silently* excluded — it exists, it looks like
coverage, but it never gates a push.

That failure mode is not hypothetical.  The 2026-08-03 CI-registration audit
found 50+ such orphaned test files — including security, SECA freeze-guard,
output-firewall, validator-parity, and billing/entitlement suites — that had
never run in CI.  Several had quietly rotted: a dormant test drifts out of sync
with the production code it pins (a mock's arity, a moved class, a tightened
validator) and only fails once someone finally runs it.

This guard makes the drift LOUD.  Every ``test_*.py`` under ``llm/`` must be
EITHER referenced by a real CI gate OR listed in ``CI_EXCLUDED`` below with a
reason.  A file that is neither fails ``CI_REG_01`` immediately, so a future
contributor who adds a test file is forced to make a conscious
register-or-exclude decision instead of the test silently never running.

Matching is by basename (mirrors the audit).  It is a safety net, not a
byte-exact model of pytest collection: a basename shared by two files in
different directories is treated as one.  The point is coverage governance —
no test file slips into the tree ungoverned — not perfect collection modelling.
"""

from __future__ import annotations

from pathlib import Path
import re

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LLM = _REPO_ROOT / "llm"
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"

# ---------------------------------------------------------------------------
# Test files intentionally NOT gated in per-push CI.  Every entry MUST carry a
# reason.  A stale entry (file deleted/renamed) fails CI_REG_02; an entry that
# is ALSO wired into a gate (redundant/contradictory) fails CI_REG_03.
# ---------------------------------------------------------------------------
CI_EXCLUDED: dict[str, str] = {
    "test_real_llm_smoke.py": (
        "Category C real-LLM smoke — needs RUN_DEEPSEEK_TESTS + a live key; "
        "local / weekly-cron only (docs/TESTING.md)."
    ),
    "test_explanation_quality.py": (
        "Category E advisory quality heuristic — must NEVER block CI "
        "(docs/TESTING.md); run on demand via run_all_tests.py --local."
    ),
    "test_stress_suite.py": (
        "Perf/stress suite: SLO benchmarks + randomised workload — run "
        "locally via run_stress_suite.py, not on every push."
    ),
    "test_ci_optional_run.py": (
        "BROKEN — imports the removed module llm.rag.llm.ollama and cannot be "
        "collected (a collection error aborts the whole pytest run). Dead "
        "test: owner to delete it or restore the Ollama adapter. Tracked by "
        "the 2026-08-03 CI-registration audit."
    ),
}


def _iter_test_files() -> list[Path]:
    """Every ``test_*.py`` under ``llm/`` that is our code (not vendored)."""
    out: list[Path] = []
    for path in _LLM.rglob("test_*.py"):
        parts = set(path.parts)
        if parts & {"venv", "site-packages", "__pycache__", ".mypy_cache", ".pytest_cache"}:
            continue
        out.append(path)
    return out


def _gate_text() -> str:
    """Concatenated source of every real CI gate (runner scripts + workflows).

    ``run_all_tests.py`` is deliberately NOT included: it is a local
    convenience runner, not a CI gate, so a file referenced only there does
    not actually gate a push.
    """
    text = ""
    for name in ("run_ci_suite.py", "run_regression_suite.py"):
        text += (_LLM / name).read_text(encoding="utf-8")
    if _WORKFLOWS.is_dir():
        for wf in sorted(_WORKFLOWS.glob("*.yml")) + sorted(_WORKFLOWS.glob("*.yaml")):
            text += wf.read_text(encoding="utf-8")
    return text


def _gated_basenames() -> set[str]:
    return set(re.findall(r"test_[A-Za-z0-9_]+\.py", _gate_text()))


def _rel(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def test_ci_reg_01_every_test_file_is_gated_or_excluded() -> None:
    """CI_REG_01: no ``test_*.py`` under ``llm/`` is silently un-run.

    Each must be referenced by a CI gate (``run_ci_suite.py`` /
    ``run_regression_suite.py`` / a ``.github/workflows`` pytest step) or be
    listed in ``CI_EXCLUDED`` with a reason.
    """
    gated = _gated_basenames()
    orphans = sorted(
        _rel(path)
        for path in _iter_test_files()
        if path.name not in gated and path.name not in CI_EXCLUDED
    )
    assert not orphans, (
        f"{len(orphans)} test file(s) are wired into NO CI gate and are not in "
        "CI_EXCLUDED — they never run on a push:\n  "
        + "\n  ".join(orphans)
        + "\n\nFix: add each to TEST_TARGETS in llm/run_ci_suite.py (after "
        "confirming it PASSES), or add it to CI_EXCLUDED in this file with a "
        "reason if it is intentionally local-only."
    )


def test_ci_reg_02_excluded_entries_all_exist() -> None:
    """CI_REG_02: ``CI_EXCLUDED`` carries no stale entry (file deleted/renamed)."""
    present = {path.name for path in _iter_test_files()}
    stale = sorted(name for name in CI_EXCLUDED if name not in present)
    assert (
        not stale
    ), "CI_EXCLUDED names test files that no longer exist — remove them: " + ", ".join(stale)


def test_ci_reg_03_excluded_entries_are_not_also_gated() -> None:
    """CI_REG_03: an excluded file must not ALSO be wired into a CI gate.

    If it is, the exclusion is misleading (the file already runs) — delete the
    redundant ``CI_EXCLUDED`` entry so the list stays honest.
    """
    gated = _gated_basenames()
    contradictory = sorted(name for name in CI_EXCLUDED if name in gated)
    assert not contradictory, (
        "CI_EXCLUDED names test files that ARE gated in CI — remove the "
        "redundant exclusion: " + ", ".join(contradictory)
    )
