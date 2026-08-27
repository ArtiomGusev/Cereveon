# pylint: disable=too-many-lines
# This file is the structural-pin contract for every CI workflow + every
# CI-runner script in the repo.  Each test pins one structural invariant
# (the ssh-action version, the per-module coverage gate wiring, the
# python-tests step ordering, etc.) — splitting it across multiple files
# would just multiply imports without changing the audit value.  The
# pylint default cap of 1000 lines is sized for production code, not for
# a single-purpose contract test.
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace  # pylint: disable=no-name-in-module

import pytest
import yaml  # type: ignore[import-untyped]

from llm import run_ci_suite, run_quality_gate

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def _load_workflow(filename: str) -> dict:
    return yaml.safe_load((WORKFLOW_DIR / filename).read_text(encoding="utf-8"))


def _step_named(job: dict, name: str) -> dict:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"Step {name!r} not found")


def _optional_step(job: dict, name: str) -> dict | None:
    """Like _step_named but returns None instead of raising — for asserting
    a step is ABSENT (e.g. the removed Fly app-image scan)."""
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    return None


def test_ci_workflow_includes_required_gates():
    workflow = _load_workflow("fly-deploy.yml")
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {"contents": "read"}
    # Single production tier: the Fly.io edge (`APP_IMAGE_NAME: cereveon`,
    # llm/server.js Ollama prototype) was removed 2026-07-15.  Only the
    # Hetzner llm-API image remains.
    assert workflow["env"] == {
        "API_IMAGE_NAME": "cereveon-llm-api",
    }
    assert workflow["concurrency"] == {
        "group": "ci-cd-${{ github.workflow }}-${{ github.ref }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }
    assert {
        "workflow-lint",
        "python-tests",
        "python-quality",
        "dependency-security",
        "android-build",
        "compose-validate",
        "docker-images",
        "image-security",
        "deploy",
        "release",
    }.issubset(jobs)
    # The Fly edge (node-security audit + fly-deploy) is gone.
    assert "node-security" not in jobs
    assert "fly-deploy" not in jobs
    assert set(jobs["docker-images"]["needs"]) == {
        "workflow-lint",
        "python-tests",
        "python-quality",
        "dependency-security",
        "android-build",
        "compose-validate",
    }
    assert "image-security" in jobs["deploy"]["needs"]
    assert "image-security" in jobs["release"]["needs"]
    assert jobs["deploy"]["environment"] == {"name": "production"}
    assert jobs["deploy"]["permissions"] == {"contents": "read"}
    assert jobs["release"]["permissions"] == {"contents": "write"}


def test_ci_workflow_hardens_checkout_and_supply_chain_controls():  # pylint: disable=too-many-statements
    workflow = _load_workflow("fly-deploy.yml")
    jobs = workflow["jobs"]

    for job_name in [
        "workflow-lint",
        "python-tests",
        "python-quality",
        "dependency-security",
        "android-build",
        "compose-validate",
        "docker-images",
        "image-security",
        "deploy",
    ]:
        checkout = _step_named(jobs[job_name], "Checkout repository")
        assert checkout["uses"] == "actions/checkout@v6"
        assert checkout["with"]["persist-credentials"] is False

    android_build = jobs["android-build"]
    assert android_build["env"] == {
        "GRADLE_USER_HOME": "${{ github.workspace }}/.gradle",
        "ANDROID_USER_HOME": "${{ github.workspace }}/.android",
    }
    android_test_step = _step_named(android_build, "Run Android host JVM unit tests")
    assert android_test_step["working-directory"] == "android"
    assert android_test_step["run"] == "./gradlew test --no-daemon"

    android_manifest_step = _step_named(android_build, "Generate and validate packaged manifests")
    assert android_manifest_step["working-directory"] == "android"
    assert (
        android_manifest_step["run"]
        == "./gradlew processDebugManifestForPackage processReleaseManifestForPackage --no-daemon"
    )
    verify_manifest_step = _step_named(
        android_build, "Verify packaged manifest includes INTERNET permission"
    )
    assert verify_manifest_step["shell"] == "bash"
    assert "android.permission.INTERNET" in verify_manifest_step["run"]
    assert "processDebugManifestForPackage/AndroidManifest.xml" in verify_manifest_step["run"]
    assert "processReleaseManifestForPackage/AndroidManifest.xml" in verify_manifest_step["run"]

    assert (
        _step_named(jobs["workflow-lint"], "Lint GitHub Actions workflows")["uses"]
        == "raven-actions/actionlint@v2"
    )
    docker_job = jobs["docker-images"]
    assert docker_job["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }
    # Only the Hetzner llm-API image remains (the Fly `app_digest` /
    # build-app is gone).
    assert docker_job["outputs"] == {
        "image_owner": "${{ steps.prep.outputs.owner }}",
        "api_digest": "${{ steps.build-api.outputs.digest }}",
    }
    docker_login = _step_named(docker_job, "Log in to GHCR")
    assert docker_login["with"]["username"] == "${{ github.repository_owner }}"

    build_api = _step_named(docker_job, "Build llm API image")
    assert build_api["with"]["provenance"] is False
    assert build_api["with"]["sbom"] is False
    assert build_api["with"]["build-args"] == "BUILDDATE=${{ github.run_id }}"
    assert _step_named(docker_job, "Install Cosign")["uses"] == "sigstore/cosign-installer@v3"
    # One image now → one provenance attestation (was two: app + api).
    assert [
        step["uses"]
        for step in docker_job["steps"]
        if step.get("uses") == "actions/attest-build-provenance@v4"
    ] == [
        "actions/attest-build-provenance@v4",
    ]

    image_security = jobs["image-security"]
    assert image_security["permissions"] == {
        "contents": "read",
        "packages": "read",
        "security-events": "write",
    }
    image_security_login = _step_named(image_security, "Log in to GHCR")
    assert image_security_login["with"]["username"] == "${{ github.repository_owner }}"

    # The Fly app-image scan (scan_app / sarif_app) was removed with the
    # edge; only the Hetzner llm-API image is scanned now.
    assert _optional_step(image_security, "Scan published app image") is None

    scan_api = _step_named(image_security, "Scan published llm API image")
    assert scan_api["uses"] == "aquasecurity/trivy-action@v0.36.0"
    assert scan_api["with"]["scan-type"] == "image"
    assert scan_api["with"]["format"] == "table"
    assert scan_api["with"]["vuln-type"] == "os,library"
    assert scan_api["with"]["severity"] == "CRITICAL"
    assert scan_api["with"]["ignore-unfixed"] is True
    assert scan_api["with"]["trivy-config"] == "trivy.yaml"
    assert scan_api["env"]["TRIVY_TIMEOUT"] == "15m"
    assert scan_api["env"]["TRIVY_USERNAME"] == "${{ github.repository_owner }}"

    sarif_api = _step_named(image_security, "Generate llm API image SARIF")
    assert sarif_api["uses"] == "aquasecurity/trivy-action@v0.36.0"
    assert sarif_api["with"]["format"] == "sarif"
    assert sarif_api["with"]["exit-code"] == "0"
    assert sarif_api["with"]["ignore-unfixed"] is False
    assert sarif_api["with"]["trivy-config"] == "trivy.yaml"
    assert sarif_api["env"]["TRIVY_SKIP_DB_UPDATE"] == "true"
    assert sarif_api["env"]["TRIVY_USERNAME"] == "${{ github.repository_owner }}"

    # The gate must actually be able to fail the job.  ``continue-on-error`` on
    # the scan step is deliberate — it lets the SARIF steps publish even when
    # the gate trips — so the swallowed exit code has to be re-raised by a
    # terminal step.  Without that step the job's conclusion is unconditionally
    # success and ``deploy``, which lists image-security in ``needs``, can never
    # be blocked by a fixable CRITICAL.  Both halves are load-bearing; assert
    # them together so removing either one fails here.
    assert scan_api["continue-on-error"] is True
    assert scan_api["with"]["exit-code"] == "1"

    fail_step = _step_named(image_security, "Fail on fixable CRITICAL image findings")
    assert "steps.trivy-api-image.outcome == 'failure'" in fail_step["if"]
    assert "exit 1" in fail_step["run"]

    # Ordering matters: the failure is raised AFTER the SARIF upload.  Raised
    # any earlier, a tripped gate aborts the job before the findings reach the
    # Security tab — losing visibility at the exact moment it matters most.
    step_names = [step.get("name") for step in image_security["steps"]]
    assert step_names.index("Fail on fixable CRITICAL image findings") > step_names.index(
        "Upload llm API image scan results"
    )

    # The SARIF feed is deliberately non-blocking, so its outcome needs an
    # explicit consumer too.  Without one, a failed generation silently skips
    # the upload — whose ``if:`` is only a hashFiles guard — and the Security
    # tab freezes while the job still reports success, which would let an empty
    # alert queue mean "nothing was reported" rather than "nothing was found".
    assert sarif_api["id"] == "trivy-api-sarif"

    sarif_warn = _step_named(image_security, "Warn if the SARIF triage feed did not publish")
    assert "steps.trivy-api-sarif.outcome == 'failure'" in sarif_warn["if"]
    assert "hashFiles('trivy-api-image.sarif') == ''" in sarif_warn["if"]
    assert "::warning" in sarif_warn["run"]

    # It must stay a WARNING.  The SARIF feed is the visibility path, not the
    # safety-critical one; failing here would let a transient upload hiccup
    # block a production deploy.  The terminal gate asserted above is what
    # blocks a bad image.
    assert "exit 1" not in sarif_warn["run"]

    # It runs after the upload (so it can observe the result) but before the
    # terminal gate — which, exiting non-zero, would otherwise end the job
    # first and suppress the warning entirely.
    assert (
        step_names.index("Upload llm API image scan results")
        < step_names.index("Warn if the SARIF triage feed did not publish")
        < step_names.index("Fail on fixable CRITICAL image findings")
    )


def test_security_workflow_uses_safe_checkout_and_codeql_v4():
    workflow = _load_workflow("security.yml")
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {
        "actions": "read",
        "contents": "read",
        "security-events": "write",
    }

    for job in jobs.values():
        checkout = _step_named(job, "Checkout repository")
        assert checkout["uses"] == "actions/checkout@v6"
        assert checkout["with"]["persist-credentials"] is False

    codeql_python_job = jobs["codeql-python"]
    assert (
        _step_named(codeql_python_job, "Initialize CodeQL")["uses"]
        == "github/codeql-action/init@v4"
    )
    assert _step_named(codeql_python_job, "Initialize CodeQL")["with"]["languages"] == "python"

    # The analyze step is matched by its `uses:` (the action contract) rather
    # than its display name, because we wrap it in a SARIF-filter pipeline:
    #   Initialize → Analyze (upload: never) → Filter SARIF → Upload filtered
    # See .github/scripts/filter_codeql_sarif.py for the rationale (one
    # accepted false positive: py/weak-cryptographic-hash on hashing.py).
    analyze_steps = [
        s for s in codeql_python_job["steps"] if s.get("uses") == "github/codeql-action/analyze@v4"
    ]
    assert (
        len(analyze_steps) == 1
    ), "codeql-python must call github/codeql-action/analyze@v4 exactly once"
    analyze = analyze_steps[0]
    assert (
        analyze["with"]["upload"] == "never"
    ), "analyze must use upload: never so the filter step can run before SARIF upload"
    assert (
        analyze["with"]["output"] == "codeql-sarif"
    ), "analyze must write SARIF to the codeql-sarif directory the filter reads"

    # Filter step runs after analyze, before upload.
    filter_step = _step_named(codeql_python_job, "Filter accepted false positives from SARIF")
    assert filter_step["run"] == "python .github/scripts/filter_codeql_sarif.py codeql-sarif"

    # Upload step uses the filtered SARIF, in the codeql-python category so
    # alerts have stable identity in the Security tab across runs.
    upload_step = _step_named(codeql_python_job, "Upload filtered SARIF")
    assert upload_step["uses"] == "github/codeql-action/upload-sarif@v4"
    assert upload_step["with"]["sarif_file"] == "codeql-sarif"
    assert upload_step["with"]["category"] == "codeql-python"

    codeql_javascript_job = jobs["codeql-javascript"]
    assert "schedule" in codeql_javascript_job["if"]
    assert "workflow_dispatch" in codeql_javascript_job["if"]
    assert (
        _step_named(codeql_javascript_job, "Initialize CodeQL")["with"]["languages"] == "javascript"
    )

    trivy_misconfig_job = jobs["trivy-misconfig"]
    assert "github.event_name == 'schedule'" in trivy_misconfig_job["if"]
    assert "github.event_name == 'workflow_dispatch'" in trivy_misconfig_job["if"]
    assert "github.event_name == 'pull_request'" in trivy_misconfig_job["if"]
    assert (
        _step_named(trivy_misconfig_job, "Run Trivy misconfiguration scan")["with"]["scanners"]
        == "misconfig"
    )
    assert (
        _step_named(trivy_misconfig_job, "Run Trivy misconfiguration scan")["uses"]
        == "aquasecurity/trivy-action@v0.36.0"
    )

    trivy_runtime_job = jobs["trivy-runtime-vulns"]
    assert (
        trivy_runtime_job["if"]
        == "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    )
    prepare_trivy_input = _step_named(
        trivy_runtime_job, "Prepare Trivy runtime manifest scan input"
    )
    assert (
        "cp llm/requirements.txt tmp_logs/trivy-runtime/requirements.txt"
        in prepare_trivy_input["run"]
    )
    trivy_runtime_step = _step_named(trivy_runtime_job, "Run Trivy runtime vulnerability scan")
    assert trivy_runtime_step["uses"] == "aquasecurity/trivy-action@v0.36.0"
    assert trivy_runtime_step["with"]["scanners"] == "vuln"
    assert trivy_runtime_step["with"]["scan-ref"] == "tmp_logs/trivy-runtime"
    assert trivy_runtime_step["with"]["format"] == "table"


def test_dependency_security_audits_ci_requirements():
    """dependency-security CI job must audit requirements-ci.txt (closes issue #6).

    GHSA-w234-x5rp-h73c was reported against pytest (pip) in requirements-ci.txt.
    The job previously only audited requirements.txt, leaving CI-only tools (including
    pytest) outside the vulnerability scan perimeter.  Both files must be explicitly
    audited so a future downgrade to a vulnerable version causes CI to fail.
    """
    workflow = _load_workflow("fly-deploy.yml")
    dep_sec = workflow["jobs"]["dependency-security"]
    step_runs = "\n".join(step.get("run", "") for step in dep_sec["steps"])

    assert (
        "requirements.txt" in step_runs
    ), "dependency-security job does not audit llm/requirements.txt."
    assert "requirements-ci.txt" in step_runs, (
        "dependency-security job does not audit llm/requirements-ci.txt. "
        "CI-only dependencies (e.g. pytest) are outside the vulnerability scan — "
        "root cause of issue #6 (GHSA-w234-x5rp-h73c)."
    )


def test_fly_edge_ollama_prototype_is_removed():
    """The Fly.io edge — root ``Dockerfile`` → ``llm/server.js`` (a Node/
    Express Ollama prototype exposing unauthenticated, validator-bypassing
    /coach·/explain) — was deleted 2026-07-15.  It never fronted
    cereveon.com (Hetzner's Caddy serves that directly) and 502'd on a
    non-existent Ollama upstream.  Pin its absence so it can't creep back.
    """
    assert not (ROOT / "Dockerfile").exists(), "the Fly edge root Dockerfile is back"
    assert not (ROOT / "fly.toml").exists(), "root fly.toml is back"
    assert not (ROOT / "llm" / "fly.toml").exists(), "llm/fly.toml is back"
    assert not (ROOT / "llm" / "server.js").exists(), "the Node/Ollama server.js is back"
    assert not (ROOT / "llm" / "package.json").exists(), "llm/package.json (Node) is back"

    # The Hetzner llm-API image (the sole remaining tier) must NOT drag any
    # Node/Ollama server artifact back in.
    llm_api_dockerfile = (ROOT / "llm" / "Dockerfile.api").read_text(encoding="utf-8")
    assert "server.js" not in llm_api_dockerfile
    assert "package.json" not in llm_api_dockerfile


def test_container_image_keeps_health_check_and_non_root_runtime():
    """The single production image is llm/Dockerfile.api (Hetzner). The
    former root Dockerfile (Fly edge) is pinned gone by
    test_fly_edge_ollama_prototype_is_removed."""
    llm_api_dockerfile = (ROOT / "llm" / "Dockerfile.api").read_text(encoding="utf-8")
    assert "apt-get upgrade -y" in llm_api_dockerfile
    assert (
        "rm -rf /app/llm/tests /app/llm/rag/tests /app/llm/.github /app/llm/redis-win"
        in llm_api_dockerfile
    )
    assert "USER appuser" in llm_api_dockerfile
    assert "HEALTHCHECK" in llm_api_dockerfile
    assert "127.0.0.1:8000/health" in llm_api_dockerfile


def test_runtime_dependency_files_are_pinned():
    requirements = (ROOT / "llm" / "requirements.txt").read_text(encoding="utf-8").splitlines()
    pinned_requirements = [
        line for line in requirements if line.strip() and not line.startswith("#")
    ]

    assert pinned_requirements
    assert all("==" in line for line in pinned_requirements)


def test_run_ci_suite_builds_expected_pytest_command(monkeypatch, tmp_path):
    """When pytest fails (rc != 0) main() returns that rc immediately and
    does NOT call the per-module coverage gate — the gate is only meaningful
    against a successful pytest run."""
    calls: list[dict[str, object]] = []

    def _fake_run(cmd: list[str], cwd: Path, check: bool):
        calls.append({"cmd": cmd, "cwd": cwd, "check": check})
        return SimpleNamespace(returncode=7)

    artifact_dir = tmp_path / "tmp_logs"
    monkeypatch.setattr(run_ci_suite, "ARTIFACT_DIR", artifact_dir)
    monkeypatch.setattr(run_ci_suite.subprocess, "run", _fake_run)

    assert run_ci_suite.main() == 7
    assert artifact_dir.is_dir()
    assert calls == [
        {
            "cmd": [
                run_ci_suite.sys.executable,
                "-m",
                "pytest",
                "-q",
                *run_ci_suite.TEST_TARGETS,
                *[f"--cov={target}" for target in run_ci_suite.COVERAGE_TARGETS],
                "--cov-report=term-missing:skip-covered",
                "--cov-report=xml:tmp_logs/coverage.xml",
                "--cov-fail-under=80",
                "--junitxml=tmp_logs/pytest-ci.xml",
            ],
            "cwd": run_ci_suite.PROJECT_ROOT,
            "check": False,
        }
    ]
    assert "llm/tests/test_ci_pipeline.py" in run_ci_suite.TEST_TARGETS
    assert "llm/tests/test_api_contract_validation.py" in run_ci_suite.TEST_TARGETS
    assert "llm/tests/test_coaching_pipeline_regression.py" in run_ci_suite.TEST_TARGETS
    assert "llm/tests/test_explain_schema_validation.py" in run_ci_suite.TEST_TARGETS


def test_run_ci_suite_runs_per_module_coverage_gate_on_pytest_success(monkeypatch, tmp_path):
    """When pytest succeeds (rc == 0) main() must invoke
    llm/check_coverage_thresholds.py to enforce per-module floors that the
    pytest --cov-fail-under global average does not.

    This is the load-bearing assertion that a regression in run_ci_suite
    cannot silently drop the validator/safety 95% gate.
    """
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], cwd: Path, check: bool):
        del cwd, check
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    artifact_dir = tmp_path / "tmp_logs"
    monkeypatch.setattr(run_ci_suite, "ARTIFACT_DIR", artifact_dir)
    monkeypatch.setattr(run_ci_suite.subprocess, "run", _fake_run)

    assert run_ci_suite.main() == 0
    assert len(calls) == 2, (
        "On pytest success, run_ci_suite must run pytest THEN the per-module "
        f"coverage gate — got {len(calls)} subprocess.run calls instead of 2"
    )
    assert calls[0][1:3] == ["-m", "pytest"], "first call must be pytest"
    assert calls[1] == [
        run_ci_suite.sys.executable,
        "llm/check_coverage_thresholds.py",
    ], "second call must invoke the per-module coverage threshold script"


def test_python_tests_job_includes_mandatory_explicit_steps():
    """Verify the python-tests CI job has explicit named steps for each mandatory test category.

    TESTING.md CI Policy requires these to be distinct named steps so failures are
    immediately visible in the GitHub Actions UI rather than buried in the full suite log.
    """
    workflow = _load_workflow("fly-deploy.yml")
    python_tests_job = workflow["jobs"]["python-tests"]

    golden_step = _step_named(python_tests_job, "Run golden tests (Category A — mandatory)")
    assert "test_retriever.py" in golden_step["run"]
    assert "test_prompt_snapshot.py" in golden_step["run"]

    contract_step = _step_named(python_tests_job, "Run LLM contract tests (Category B — mandatory)")
    assert "test_fake_llm.py" in contract_step["run"]

    violations_step = _step_named(
        python_tests_job, "Run validator violations corpus (Category B — mandatory)"
    )
    assert "test_violations_corpus.py" in violations_step["run"]

    api_contract_step = _step_named(python_tests_job, "Run API contract validation")
    assert "test_api_contract_validation.py" in api_contract_step["run"]

    regression_step = _step_named(python_tests_job, "Run coaching pipeline regression tests")
    assert "test_coaching_pipeline_regression.py" in regression_step["run"]

    schema_step = _step_named(python_tests_job, "Run explain schema validation tests")
    assert "test_explain_schema_validation.py" in schema_step["run"]

    engine_regression_step = _step_named(python_tests_job, "Run engine regression tests")
    # Post-engine-library-cleanup (2026-05-12): live engine-pool coverage
    # replaced the retired ``llm.engine_eval`` / ``llm.elite_engine_service``
    # tests.  Pin the live surface — these files exercise the
    # ``llm.seca.engines.stockfish.pool`` pool/cache plus the response-format
    # signal extraction.
    assert "test_engine_pool_evaluate_position.py" in engine_regression_step["run"]
    assert "test_fen_move_cache_key.py" in engine_regression_step["run"]
    assert "test_engine_response_format.py" in engine_regression_step["run"]

    api_security_step = _step_named(python_tests_job, "Run API security tests")
    assert "test_api_security.py" in api_security_step["run"]

    regression_pipeline_step = _step_named(python_tests_job, "Run regression pipeline")
    assert "run_regression_suite.py" in regression_pipeline_step["run"]

    # Full suite with coverage must still follow as the authoritative CI gate
    suite_step = _step_named(python_tests_job, "Run pytest suite with coverage")
    assert "run_ci_suite.py" in suite_step["run"]

    # Ordering: explicit category steps must precede the full suite
    step_names = [step.get("name") for step in python_tests_job["steps"]]
    golden_idx = step_names.index("Run golden tests (Category A — mandatory)")
    engine_regression_idx = step_names.index("Run engine regression tests")
    regression_pipeline_idx = step_names.index("Run regression pipeline")
    suite_idx = step_names.index("Run pytest suite with coverage")
    assert golden_idx < suite_idx, "Category A golden tests must run before the full suite"
    assert (
        engine_regression_idx < suite_idx
    ), "Engine regression tests must run before the full suite"
    assert regression_pipeline_idx < suite_idx, "Regression pipeline must run before the full suite"


def test_run_quality_gate_runs_all_steps_by_default(monkeypatch, tmp_path):
    calls: list[dict[str, object]] = []

    def _fake_run(
        cmd: list[str],
        cwd: Path,
        check: bool,
        env: dict[str, str] | None = None,
    ):
        calls.append({"cmd": cmd, "cwd": cwd, "check": check, "env": env})
        return SimpleNamespace(returncode=0)

    pylint_home = tmp_path / ".pylint"
    monkeypatch.setattr(run_quality_gate, "PYLINT_HOME", pylint_home)
    monkeypatch.setattr(run_quality_gate.subprocess, "run", _fake_run)
    monkeypatch.setattr(run_quality_gate.sys, "argv", ["run_quality_gate.py"])

    assert run_quality_gate.main() == 0
    assert [call["cmd"][2] for call in calls] == ["black", "pylint", "mypy"]
    assert all(call["cwd"] == run_quality_gate.PROJECT_ROOT for call in calls)
    assert all(call["check"] is False for call in calls)
    assert calls[0]["env"] is None
    assert calls[1]["env"]["PYLINTHOME"] == str(pylint_home)
    assert calls[2]["env"] is None
    assert pylint_home.is_dir()


def test_run_quality_gate_runs_only_requested_steps(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def _fake_run(
        cmd: list[str],
        cwd: Path,
        check: bool,
        env: dict[str, str] | None = None,
    ):
        del cwd, check, env
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_quality_gate, "PYLINT_HOME", tmp_path / ".pylint")
    monkeypatch.setattr(run_quality_gate.subprocess, "run", _fake_run)
    monkeypatch.setattr(run_quality_gate.sys, "argv", ["run_quality_gate.py", "black", "mypy"])

    assert run_quality_gate.main() == 0
    assert [call[2] for call in calls] == ["black", "mypy"]


def test_run_quality_gate_rejects_unknown_steps(monkeypatch):
    monkeypatch.setattr(run_quality_gate.sys, "argv", ["run_quality_gate.py", "ruff"])

    with pytest.raises(SystemExit) as excinfo:
        run_quality_gate.main()

    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# Phase 3 — Android release build integration
# ---------------------------------------------------------------------------


def test_android_build_job_apk_step_uses_vars_not_secrets():
    """COACH_API_BASE is visible in the APK binary — must be vars.*, not secrets.*.
    COACH_API_KEY is a rate-limit shield only and is appropriately secrets.*.
    The upload step must target the unsigned release APK path.

    The build step is split into PR and push variants so PR forks never see
    the production COACH_API_KEY.  Both variants must apply the COACH_API_BASE
    rule (vars only, never secrets); only the push variant injects
    COACH_API_KEY.  Upload must run on push only.
    """
    workflow = _load_workflow("fly-deploy.yml")
    android_build = workflow["jobs"]["android-build"]

    pr_apk = _step_named(android_build, "Build release APK (PR)")
    push_apk = _step_named(android_build, "Build release APK (push)")

    for label, step in (("PR", pr_apk), ("push", push_apk)):
        assert step["working-directory"] == "android", f"{label} APK build must run from android/"
        assert (
            step["run"] == "./gradlew assembleRelease --no-daemon"
        ), f"{label} APK build command changed unexpectedly"
        api_base_ref = step["env"]["COACH_API_BASE"]
        assert "vars.COACH_API_BASE" in api_base_ref, (
            f"{label}: COACH_API_BASE is visible in the APK; "
            f"use vars.COACH_API_BASE (not secrets.*)"
        )
        assert (
            "secrets.COACH_API_BASE" not in api_base_ref
        ), f"{label}: COACH_API_BASE must come from vars, never secrets"

    # Trigger gating: PR runs without the production key, push runs with it.
    assert pr_apk["if"] == "github.event_name == 'pull_request'"
    assert push_apk["if"] == "github.event_name == 'push'"

    # PR builds must NOT carry the production rate-limit key.
    assert "COACH_API_KEY" not in (pr_apk.get("env") or {}), (
        "PR APK builds must not see secrets.COACH_API_KEY (PR runs from forks "
        "would otherwise leak the rate-limit shield)"
    )
    # Push builds inject it.
    assert "secrets.COACH_API_KEY" in push_apk["env"]["COACH_API_KEY"]

    upload_step = _step_named(android_build, "Upload release APK")
    assert upload_step["with"]["path"].endswith("app-release-unsigned.apk")
    assert upload_step["uses"].startswith("actions/upload-artifact@")
    # Upload only runs on push; PR runners shouldn't publish artefacts.
    assert upload_step["if"] == "github.event_name == 'push'"


def test_build_gradle_kts_release_enforces_https_and_obfuscation():
    """Release build must enable R8, shrink resources, and hard-fail on plain-HTTP
    COACH_API_BASE so a misconfigured secret is caught at build time, not at runtime.
    """
    gradle = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")

    assert "isMinifyEnabled = true" in gradle, "R8 minification must be enabled for release"
    assert "isShrinkResources = true" in gradle, "Resource shrinking must be enabled for release"
    assert "proguard-android-optimize.txt" in gradle
    assert "proguard-rules.pro" in gradle

    assert (
        'startsWith("https://")' in gradle
    ), "Release build must hard-fail when COACH_API_BASE does not start with https://"
    assert "error(" in gradle, "Hard-fail guard for non-HTTPS COACH_API_BASE must be present"

    assert 'System.getenv("COACH_API_BASE")' in gradle
    assert 'System.getenv("COACH_API_KEY")' in gradle


def test_build_gradle_kts_debug_reads_api_endpoint_from_env():
    """Debug builds must override the defaultConfig API endpoint from env vars so
    developers can test against Hetzner without modifying source code (Step 3.4).
    """
    gradle = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")

    # Must appear in both release and debug blocks — count must be >= 2
    assert (
        gradle.count('System.getenv("COACH_API_BASE")') >= 2
    ), "COACH_API_BASE env-var override must appear in both debug and release build types"
    assert (
        gradle.count('System.getenv("COACH_API_KEY")') >= 2
    ), "COACH_API_KEY env-var override must appear in both debug and release build types"


def test_proguard_rules_preserve_api_model_classes():
    """ProGuard/R8 must not rename or remove API model members accessed by string
    name through org.json, Kotlin coroutine internals, or EncryptedSharedPreferences.
    """
    proguard = (ROOT / "android" / "app" / "proguard-rules.pro").read_text(encoding="utf-8")

    assert (
        "-keepattributes SourceFile,LineNumberTable" in proguard
    ), "Stack-trace line numbers must be preserved for crash reporting"
    assert "com.cereveon.myapp" in proguard, "API model classes in com.cereveon.myapp must be kept"
    assert (
        "public *" in proguard
    ), "Public members of model classes must be kept for org.json field access"
    assert "kotlinx.coroutines" in proguard, "Kotlin coroutine internals must be preserved"
    assert (
        "androidx.security.crypto" in proguard
    ), "AndroidX EncryptedSharedPreferences must be preserved"


# ---------------------------------------------------------------------------
# Phase 4 — Hetzner deploy and Phase 5 — runtime configuration
# ---------------------------------------------------------------------------


def _assert_zero_downtime_ssh_step(ssh_step: dict, *, label: str) -> None:
    """Shared assertions for the Deploy to Hetzner via SSH step.

    Used by both the automated deploy (fly-deploy.yml) and the manual
    re-deploy (production-deploy.yml) to ensure they remain in sync.
    """
    assert ssh_step["uses"] == "appleboy/ssh-action@v1.2.5", f"{label}: wrong SSH action"
    assert (
        ssh_step["with"]["key"] == "${{ secrets.HETZNER_SSH_KEY }}"
    ), f"{label}: SSH key must come from secrets.HETZNER_SSH_KEY"
    assert (
        ssh_step["with"]["host"] == "${{ secrets.HETZNER_HOST }}"
    ), f"{label}: host must come from secrets.HETZNER_HOST"
    assert ssh_step["with"]["username"] == "deploy", f"{label}: username must be 'deploy'"

    script: str = ssh_step["with"]["script"]
    step_env: dict = ssh_step.get("env", {})
    step_envs: str = ssh_step["with"].get("envs", "")

    assert "set -euo pipefail" in script, f"{label}: must use strict error handling"
    assert "docker pull" in script, f"{label}: must pull pinned image before rollout"
    assert "DEPLOY_IMAGE" in script, f"{label}: must deploy by pinned digest via DEPLOY_IMAGE"
    assert "DEPLOY_IMAGE" in step_env, f"{label}: DEPLOY_IMAGE must be set on the step env"
    assert "DEPLOY_IMAGE" in step_envs, f"{label}: DEPLOY_IMAGE must be forwarded via envs:"
    # Stop-and-replace strategy (PR #70): the 4 GB Hetzner box cannot host two
    # api containers in parallel without OOM-stalling docker inspect during
    # the rolling-pair window.  We accept ~5-10 s of Caddy 502s per deploy in
    # exchange for not hitting the memory ceiling.  The previous --scale api=2
    # / --no-recreate invariants were intentionally removed; --force-recreate
    # is now the pinned contract, and the helper name is kept generic
    # (health-gated, not zero-downtime).
    assert "--force-recreate" in script, f"{label}: must --force-recreate api for stop-and-replace"
    assert "--scale api=2" not in script, (
        f"{label}: --scale api=2 was deliberately removed in PR #70 (4 GB box "
        "can't host two api containers in parallel — see PR #68/#69 OOM incidents). "
        "If you're re-introducing rolling deploy, also bump the box to CX22 (8 GB)."
    )
    assert "Health.Status" in script, f"{label}: must inspect Docker healthcheck on new container"
    assert "PREV_CONTAINER" in script, f"{label}: must record old container ID for removal"
    assert "PREV_IMAGE" in script, f"{label}: must record old image reference for rollback logging"
    assert "roll" in script.lower(), f"{label}: must roll back if new container is unhealthy"


def test_hetzner_deploy_health_gates_rollout():
    """Deploy script must implement health-gated stop-and-replace:
    --force-recreate api (single-container swap), wait for Docker healthcheck
    to report healthy on the new container, otherwise --force-recreate again
    with the previously-running image.  See PR #70 for the trade-off
    rationale (4 GB Hetzner box can't host two api containers in parallel).
    """
    workflow = _load_workflow("fly-deploy.yml")
    deploy = workflow["jobs"]["deploy"]

    assert deploy["environment"] == {"name": "production"}
    assert deploy["concurrency"]["group"] == "hetzner-production"
    assert deploy["concurrency"]["cancel-in-progress"] is False

    ssh_step = _step_named(deploy, "Deploy to Hetzner via SSH")
    _assert_zero_downtime_ssh_step(ssh_step, label="fly-deploy.yml")


def test_production_deploy_workflow_structure():
    """production-deploy.yml must be a proper manual re-deploy workflow with:
    - workflow_dispatch trigger requiring api_digest input
    - concurrency group matching fly-deploy.yml (prevents races)
    - environment: production and persist-credentials: false checkout
    - key: secrets.HETZNER_SSH_KEY (not written to disk)
    - same health-gated stop-and-replace SSH script as the automated deploy
      (the manual hotfix path must use the same strategy or the two will
      drift; see _assert_zero_downtime_ssh_step + PR #70).
    """
    workflow = _load_workflow("production-deploy.yml")

    # Trigger — PyYAML parses the `on:` key as boolean True (YAML 1.1)
    triggers = workflow[True]
    assert (
        "workflow_dispatch" in triggers
    ), "production-deploy.yml must be manually triggerable via workflow_dispatch"
    dispatch_inputs = triggers["workflow_dispatch"]["inputs"]
    assert "api_digest" in dispatch_inputs, "Must require api_digest input"
    assert dispatch_inputs["api_digest"]["required"] is True, "api_digest must be required"

    # Concurrency — same group as fly-deploy.yml deploy job (prevents simultaneous runs)
    concurrency = workflow["concurrency"]
    assert (
        concurrency["group"] == "hetzner-production"
    ), "concurrency group must match fly-deploy.yml's deploy job to prevent races"
    assert (
        concurrency["cancel-in-progress"] is False
    ), "cancel-in-progress must be False — a deploy in progress must not be interrupted"

    # Job structure
    deploy = workflow["jobs"]["deploy"]
    assert deploy["environment"] == {"name": "production"}
    assert deploy["permissions"] == {"contents": "read"}

    checkout = _step_named(deploy, "Checkout repository")
    assert checkout["uses"] == "actions/checkout@v6"
    assert checkout["with"]["persist-credentials"] is False

    # SSH key usage
    ssh_step = _step_named(deploy, "Deploy to Hetzner via SSH")
    _assert_zero_downtime_ssh_step(ssh_step, label="production-deploy.yml")


def test_automated_and_manual_deploy_share_concurrency_group():
    """fly-deploy.yml's deploy job and production-deploy.yml must use the same
    concurrency group so a manual re-deploy and an automated push-triggered
    deploy can never run simultaneously on the production server.
    """
    ci = _load_workflow("fly-deploy.yml")
    prod = _load_workflow("production-deploy.yml")

    ci_group = ci["jobs"]["deploy"]["concurrency"]["group"]
    prod_group = prod["concurrency"]["group"]
    assert ci_group == prod_group, (
        f"Concurrency group mismatch: fly-deploy.yml uses '{ci_group}' "
        f"but production-deploy.yml uses '{prod_group}'. "
        "Both must use the same group to prevent concurrent deploys."
    )


# The Fly.io edge deploy job was removed 2026-07-15 (see
# test_fly_edge_ollama_prototype_is_removed + the `"fly-deploy" not in
# jobs` pin in test_ci_workflow_includes_required_gates).  Its former
# topology / concurrency-isolation tests went with it.


def test_android_instrumented_workflow_pins_topology():
    """The nightly instrumented Android workflow must:
    - run on a schedule (not on push) + accept manual workflow_dispatch
    - use a real Android emulator (reactivecircus/android-emulator-runner@v2)
      with KVM enabled
    - run the connectedAndroidTest gradle target on the :app module
    - upload both HTML and XML test reports as artefacts (so a failed
      nightly run is debuggable without a re-run)
    """
    workflow = _load_workflow("android-instrumented.yml")

    # PyYAML parses the `on:` key as boolean True (YAML 1.1)
    triggers = workflow[True]
    assert "schedule" in triggers, "instrumented suite must run on a schedule, not on push"
    assert "workflow_dispatch" in triggers, "must also accept manual triggers"
    schedule = triggers["schedule"]
    assert (
        isinstance(schedule, list) and schedule
    ), "schedule must be a non-empty list of cron entries"
    assert "cron" in schedule[0], "schedule entry must have a cron expression"

    # Push-trigger must be absent — adding the suite to every push would
    # dominate PR latency.
    assert (
        "push" not in triggers
    ), "instrumented suite must not run on push (15-30 min boot+test cost)"
    assert "pull_request" not in triggers, "same reason — must not run on PRs"

    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["group"] == "android-instrumented"
    assert workflow["concurrency"]["cancel-in-progress"] is False

    job = workflow["jobs"]["connected-tests"]
    assert (
        job["timeout-minutes"] >= 30
    ), "connectedAndroidTest needs ample time (boot + tests); >= 30 min"

    # KVM must be enabled — without it the emulator boots in ~5 min instead
    # of <60 s.  The `udev` rule below is the only way to get /dev/kvm
    # writable on GitHub-hosted Ubuntu runners.
    kvm_step = _step_named(job, "Enable KVM")
    assert "/dev/kvm" in kvm_step["run"] or "kvm" in kvm_step["run"].lower()

    checkout = _step_named(job, "Checkout repository")
    assert checkout["uses"] == "actions/checkout@v6"
    assert checkout["with"]["persist-credentials"] is False

    # The actual test invocation must hit the :app module's connectedAndroidTest.
    test_step = _step_named(job, "Run connectedAndroidTest")
    assert test_step["uses"].startswith("reactivecircus/android-emulator-runner@")
    assert test_step["with"]["working-directory"] == "android"
    assert "connectedAndroidTest" in test_step["with"]["script"]
    assert (
        ":app:connectedAndroidTest" in test_step["with"]["script"]
    ), "must scope to the :app module, matching scripts/run_connected_android_tests.sh"

    # Reports must be uploaded on every run (success and failure) so a
    # failed nightly is debuggable from the Actions UI alone.
    html_step = _step_named(job, "Upload HTML test report")
    assert html_step["if"] == "always()"
    assert "androidTests/connected" in html_step["with"]["path"]

    xml_step = _step_named(job, "Upload XML test results")
    assert xml_step["if"] == "always()"
    assert "androidTest-results/connected" in xml_step["with"]["path"]


def test_llm_regression_cron_workflow_pins_topology():
    """The weekly llm-regression-cron workflow must:
    - run on a schedule + accept manual workflow_dispatch (not on push/PR — it's
      Category D, mandated to stay out of per-push CI)
    - exercise both real-LLM tests with RUN_DEEPSEEK_TESTS=1 (test_llm_regression
      AND test_deepseek_smoke — Category D + C)
    - own its own concurrency group so it can't race the tag-pushed regression
      job in fly-deploy.yml

    The ``quality_scores.jsonl`` artifact-upload step was retired in PR 13
    (2026-05-15) alongside the writer (``record_quality_score``) which had
    no callers anywhere in the repo — the upload was always empty.
    """
    workflow = _load_workflow("llm-regression-cron.yml")

    # PyYAML parses the `on:` key as boolean True (YAML 1.1).
    triggers = workflow[True]
    assert "schedule" in triggers, "weekly regression must run on a schedule"
    assert "workflow_dispatch" in triggers, "must also accept manual triggers"
    assert "push" not in triggers, "Category D must not run on push (TESTING.md policy)"
    assert "pull_request" not in triggers, "Category D must not run on PRs (TESTING.md policy)"

    schedule = triggers["schedule"]
    assert (
        isinstance(schedule, list) and schedule
    ), "schedule must be a non-empty list of cron entries"
    assert "cron" in schedule[0], "schedule entry must have a cron expression"

    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["group"] == "llm-regression-weekly"
    assert workflow["concurrency"]["cancel-in-progress"] is False

    job = workflow["jobs"]["llm-regression"]
    assert job["timeout-minutes"] >= 15, "DeepSeek calls + REPEATS over the corpus need ample time"

    checkout = _step_named(job, "Checkout repository")
    assert checkout["uses"] == "actions/checkout@v6"
    assert checkout["with"]["persist-credentials"] is False

    # Both Category D (regression) AND C (smoke) must run with RUN_DEEPSEEK_TESTS=1
    # — the tests skip themselves at module load otherwise.
    regression_step = _step_named(job, "Run LLM regression tests (Category D)")
    assert regression_step["env"]["RUN_DEEPSEEK_TESTS"] == "1"
    assert "test_llm_regression.py" in regression_step["run"]

    smoke_step = _step_named(job, "Run DeepSeek smoke test (Category C)")
    assert smoke_step["env"]["RUN_DEEPSEEK_TESTS"] == "1"
    assert "test_deepseek_smoke.py" in smoke_step["run"]

    # Regression guard: the secret-presence check must be surfaced through
    # the job-level ``env:`` (not directly in a step ``if:``).  GitHub
    # Actions rejects ``${{ secrets.X }}`` in step-level ``if:`` and the
    # workflow fails at PARSE time — every push silently red, zero jobs
    # run, deploy never happens.  We failed this once (Sprint 3) — pin
    # the working pattern so any future regression trips here instead
    # of in production.
    job_env = job.get("env") or {}
    assert "HAS_DEEPSEEK_KEY" in job_env, (
        "Cron workflow must surface the COACH_DEEPSEEK_API_KEY secret presence "
        "via a job-level env (e.g. ``HAS_DEEPSEEK_KEY: ${{ secrets.X != '' }}``) "
        "so step-level ``if:`` can read it without referencing the secret "
        "directly (GitHub Actions rejects ``secrets.X`` inside step ``if:``)."
    )
    for step in (regression_step, smoke_step):
        step_if = step.get("if", "")
        assert "secrets." not in step_if, (
            f"Step {step.get('name')!r} references secrets.X in its ``if:`` — "
            f"GitHub Actions rejects this at workflow-parse time. "
            f"Use env.HAS_DEEPSEEK_KEY instead."
        )
        assert (
            "env.HAS_DEEPSEEK_KEY" in step_if
        ), f"Step {step.get('name')!r} must gate on env.HAS_DEEPSEEK_KEY."

    # Telemetry artifact step retired in PR 13 — see workflow comment.
    # The writer (``record_quality_score``) had no callers, so the upload
    # never had anything to upload.  When per-attempt retry telemetry is
    # actually wired, restore both the workflow step and a pin here.

    # The tag-pushed counterpart job in fly-deploy.yml must still exist so the
    # release pipeline runs the same suite against pinned tag refs.  This
    # ties the two workflows together: removing one without the other should
    # break the link this test enforces.
    fly_workflow = _load_workflow("fly-deploy.yml")
    assert "llm-regression" in fly_workflow["jobs"], (
        "fly-deploy.yml must keep its tag-pushed llm-regression job; the weekly "
        "cron is a drift-detection complement, not a replacement"
    )


def test_docker_compose_prod_health_and_immutable_image():
    """Production compose must: pull a pre-built image (not build from source),
    expose api internally only, gate Caddy startup on api health, and rotate logs.
    """
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))

    api = compose["services"]["api"]

    assert "image" in api, "api must pull a pre-built image in prod, not build from source"
    assert "build" not in api, "api must not build from source in prod"
    assert "ports" not in api, "api must not publish ports to host — Caddy proxies it"
    assert "expose" in api

    assert "healthcheck" in api
    assert "/health" in str(api["healthcheck"]["test"])

    caddy = compose["services"]["caddy"]
    caddy_api_dep = caddy["depends_on"]["api"]
    assert (
        caddy_api_dep.get("condition") == "service_healthy"
    ), "Caddy must wait for api service_healthy before starting"

    assert "logging" in api
    assert api["logging"]["driver"] == "json-file"


def test_compose_fallback_targets_real_ghcr_namespace():
    """The api.image fallback (when GHCR_IMAGE is not set) must point to a
    real, pullable GHCR namespace — not the literal ``owner`` placeholder.

    Background: docker-compose.prod.yml previously declared
    ``image: ${GHCR_IMAGE:-ghcr.io/owner/cereveon-llm-api:latest}``.  Manual
    operator commands like ``docker compose up --force-recreate api`` (run
    during the 2026-05-09 recovery from a half-failed PR #69 deploy)
    triggered the fallback because GHCR_IMAGE wasn't exported in the shell.
    GHCR returned 404 for the literal ``owner`` namespace, and Docker
    Compose silently kept the local cached image — leaving the operator
    convinced they had deployed the latest build when in fact they had
    re-created the previous one.

    This test pins the fallback to a real namespace so the silent-stale-image
    failure mode cannot recur.  It is intentionally tolerant of fork-friendly
    re-naming: any fallback that uses ``ghcr.io/<concrete-owner>/<image>:tag``
    is accepted, as long as the owner segment is not the literal placeholder.
    """
    compose_text = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)

    api_image = compose["services"]["api"]["image"]

    # Compose env-var-with-default syntax is ``${VAR:-FALLBACK}`` — anything
    # else means an unconditional reference and the operator-recovery path
    # would just blow up with "GHCR_IMAGE: variable is not set", which is
    # also acceptable (fail-loud over silent-stale).
    if ":-" not in api_image:
        return  # Unconditional reference — no fallback to validate.

    # Extract the part after ``:-`` and before the closing ``}``, then split
    # on ``/`` so each segment can be checked exactly (rather than via a
    # ``startswith`` substring match — CodeQL's URL-sanitization rule
    # py/incomplete-url-substring-sanitization fires on the looser shape,
    # and exact segment equality is the stronger check anyway: an attacker
    # who could write to docker-compose.prod.yml would not be limited by
    # whether ``ghcr.io/`` appears at the start of a longer string).
    fallback = api_image.split(":-", 1)[1].rstrip("}")
    segments = fallback.split("/")

    assert len(segments) >= 3, (
        "api.image fallback must have the shape "
        f"``ghcr.io/<owner>/<image>:<tag>``, got: {fallback!r}"
    )
    assert segments[0] == "ghcr.io", (
        "api.image fallback must point to GHCR (where CI publishes), "
        f"got registry segment: {segments[0]!r}"
    )

    # Reject the literal ``owner`` placeholder that template-cruft tends
    # to leave behind.
    owner_segment = segments[1]
    assert owner_segment != "owner", (
        "api.image fallback uses the literal ``owner`` placeholder — pulls "
        "404 against GHCR.  Replace with the real GHCR namespace (the same "
        "one CI publishes to via fly-deploy.yml's docker-images job).  See "
        "the comment block above the api.image line for the 2026-05-09 "
        "recovery incident this prevents."
    )

    # Image name segment must match what CI publishes (cereveon-llm-api).
    # If you renamed the image, update both ends in the same commit.
    image_segment = segments[2].split(":")[0]
    assert image_segment == "cereveon-llm-api", (
        "api.image fallback image name must match the CI-published name "
        f"(env API_IMAGE_NAME in fly-deploy.yml), got: {image_segment!r}"
    )


def test_compose_alloy_service_is_profile_gated():
    """Sprint 5.D.3 — the Grafana Alloy observability shipper must be
    behind the ``monitoring`` compose profile so default deploys do NOT
    require Grafana Cloud credentials.

    Without the profile gate, ``docker compose up`` on a fresh box with
    no GRAFANA_CLOUD_* env vars would fail to start the alloy container,
    cascading via depends_on into the api/caddy chain and breaking the
    deploy.  Pinned here so a future refactor that drops the profile
    declaration is caught at CI time.
    """
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    assert "alloy" in compose["services"], (
        "alloy service missing from docker-compose.prod.yml — Sprint 5.D.3 "
        "shipper deleted?  Restore or update this test."
    )
    alloy = compose["services"]["alloy"]
    assert alloy.get("profiles") == ["monitoring"], (
        "alloy must be gated by the ``monitoring`` compose profile.  "
        f"Without the profile, default ``docker compose up`` would try to "
        f"start it and require GRAFANA_CLOUD_* env vars.  Got: {alloy.get('profiles')!r}"
    )


def test_compose_alloy_config_path_and_file_exist():
    """Sprint 5.D.3 — the alloy service mounts ``monitoring/alloy.alloy``
    as its config.  Both the mount declaration and the file must exist;
    a missing file would crash the container at startup and the
    monitoring profile would silently fail without affecting the rest
    of the stack.
    """
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    alloy = compose["services"]["alloy"]

    # Compose ``volumes`` is a list of ``<host>:<container>:<flags>`` strings.
    config_mount = next(
        (v for v in alloy.get("volumes", []) if "alloy.alloy" in v),
        None,
    )
    assert config_mount is not None, (
        "alloy service must mount ``./monitoring/alloy.alloy`` into the container; "
        f"got volumes={alloy.get('volumes')!r}"
    )

    config_file = ROOT / "monitoring" / "alloy.alloy"
    assert config_file.is_file(), (
        f"alloy config file missing at {config_file} — the docker-compose "
        f"mount declaration would crash the container at startup."
    )


def test_alloy_config_references_required_env_vars():
    """Sprint 5.D.3 — the alloy config file uses ``sys.env(...)`` to
    pull Grafana Cloud credentials from the environment.  All five
    Grafana Cloud env vars + SECA_API_KEY (reused for /metrics scrape
    auth) must be referenced; a missing one would either crash the
    config load or send unauthenticated requests to Grafana Cloud's
    push endpoints.
    """
    config_text = (ROOT / "monitoring" / "alloy.alloy").read_text(encoding="utf-8")

    required_env_vars = [
        "GRAFANA_CLOUD_LOKI_URL",
        "GRAFANA_CLOUD_LOKI_USER",
        "GRAFANA_CLOUD_PROM_URL",
        "GRAFANA_CLOUD_PROM_USER",
        "GRAFANA_CLOUD_API_KEY",
        "SECA_API_KEY",
    ]
    for var in required_env_vars:
        assert var in config_text, (
            f"alloy.alloy is missing a ``sys.env({var!r})`` reference.  "
            "Either the variable was renamed in the config without "
            "updating .env.example, or the corresponding pipeline "
            "(logs vs metrics) was removed.  Re-add the reference or "
            "delete this assertion in the same commit."
        )
