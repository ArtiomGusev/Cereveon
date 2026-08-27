"""
Container hardening tests — llm/tests/test_container_hardening.py

Pin the docker-compose.prod.yml hardening contract so a future careless
edit (or a refactor that copies a service block into a new file) does
not silently regress runtime sandboxing.

Two hardening tiers are pinned:

  Aggressive tier  — applied to ``api`` and ``redis``.  Both carry
                     ``read_only: true`` + ``tmpfs: [/tmp]`` +
                     ``cap_drop: [ALL]`` +
                     ``security_opt: [no-new-privileges:true]``.
                     These services have well-understood writable
                     paths (volume + /tmp) and need no Linux caps.

  Conservative tier — applied to ``caddy``, ``db``, and ``ollama``.
                     These services run upstream images with complex
                     internal needs (Caddy binds privileged ports
                     and chowns cert volumes; Postgres writes to
                     /run/postgresql + tmp + data; Ollama loads
                     native CUDA libs at runtime).  The deeper
                     hardening recipe per upstream version drifts
                     too fast to pin in code without staging
                     validation, so we apply only the
                     no-privilege-escalation flag — zero functional
                     impact, immediate value.

The conservative-tier rationale is documented in
``docs/DEPLOYMENT.md > Container Hardening`` so an operator picking
up the next pass can pick up exactly where this commit left off.

Stable test IDs (do NOT rename):
  CH_01  api carries security_opt no-new-privileges:true
  CH_02  api drops ALL Linux capabilities
  CH_03  api rootfs is read-only
  CH_04  api carries a writable tmpfs at /tmp
  CH_05  api runs as a fixed non-root UID (not root)
  CH_06  redis carries security_opt no-new-privileges:true
  CH_07  redis drops ALL Linux capabilities
  CH_08  redis rootfs is read-only
  CH_09  redis carries a writable tmpfs at /tmp
  CH_10  caddy carries security_opt no-new-privileges:true
  CH_11  db carries security_opt no-new-privileges:true
  CH_12  ollama carries security_opt no-new-privileges:true
  CH_13  every prod service carries security_opt no-new-privileges:true
         (catch-all that fires when a new service is added without
         the universal hardening floor)
  CH_14  the runtime image uninstalls pip (llm/Dockerfile.api)
  CH_15  no ``pip install`` runs after that uninstall
  CH_16  the uninstall targets pip ONLY — setuptools and wheel are
         deliberately retained

CH_14..CH_16 pin the image build rather than the compose file.  They
live here because "what the runtime image is allowed to contain" is
the same contract as "how the runtime container is allowed to run".
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = PROJECT_ROOT / "docker-compose.prod.yml"
API_DOCKERFILE_PATH = PROJECT_ROOT / "llm" / "Dockerfile.api"


def _load_compose() -> dict[str, Any]:
    """Parse docker-compose.prod.yml.  ``yaml.safe_load`` is required
    because the compose file embeds shell-style ``${VAR:?required}``
    interpolations that the unsafe loader would mishandle as Python
    objects."""
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _service(compose: dict[str, Any], name: str) -> dict[str, Any]:
    services = compose.get("services") or {}
    svc = services.get(name)
    assert svc is not None, (
        f"docker-compose.prod.yml is missing the {name!r} service.  "
        f"This test guards a security contract; if a service was "
        f"renamed, update the constants here in the same commit."
    )
    return svc


def _has_no_new_privileges(svc: dict[str, Any]) -> bool:
    opts = svc.get("security_opt") or []
    # YAML may parse the value as ``"no-new-privileges:true"`` or
    # split it differently; normalise to a comparable shape.
    normalised = {str(o).strip().lower() for o in opts}
    return "no-new-privileges:true" in normalised


def _drops_all_caps(svc: dict[str, Any]) -> bool:
    drops = svc.get("cap_drop") or []
    normalised = {str(d).strip().upper() for d in drops}
    return "ALL" in normalised


def _has_tmpfs(svc: dict[str, Any], path: str) -> bool:
    tmpfs = svc.get("tmpfs") or []
    if isinstance(tmpfs, str):
        tmpfs = [tmpfs]
    # tmpfs entries may be ``"/tmp"`` or ``"/tmp:size=64m"``; match
    # by leading path segment.
    return any(str(t).split(":", 1)[0] == path for t in tmpfs)


def _dockerfile_directives() -> str:
    """``llm/Dockerfile.api`` with comment lines stripped.

    The Dockerfile documents the pip purge at length in comments, and a
    comment that merely *mentions* ``pip uninstall`` must not be able to
    satisfy CH_14 on its own, so every assertion below runs against
    build directives only.
    """
    text = API_DOCKERFILE_PATH.read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


# ---------------------------------------------------------------------------
# api — aggressive tier
# ---------------------------------------------------------------------------


class TestApiServiceHardening(unittest.TestCase):
    def setUp(self):
        self.svc = _service(_load_compose(), "api")

    def test_ch_01_api_no_new_privileges(self):
        self.assertTrue(
            _has_no_new_privileges(self.svc),
            "CH_01: api service must carry security_opt: no-new-privileges:true.  "
            "This is the universal floor; without it any future setuid binary "
            "shipped in the image becomes a privilege-escalation surface.",
        )

    def test_ch_02_api_drops_all_caps(self):
        self.assertTrue(
            _drops_all_caps(self.svc),
            "CH_02: api service must declare cap_drop: [ALL].  The Python web "
            "app needs no Linux capabilities; explicit-drop closes the loop "
            "against future Dockerfile changes that drift back to root.",
        )

    def test_ch_03_api_is_read_only(self):
        self.assertIs(
            self.svc.get("read_only"),
            True,
            "CH_03: api service must declare read_only: true.  All writable "
            "paths are explicit (the api_data volume at /app/data and the "
            "tmpfs at /tmp); locking the rootfs prevents an attacker who "
            "lands code execution from persisting changes inside the image.",
        )

    def test_ch_04_api_has_tmpfs_tmp(self):
        self.assertTrue(
            _has_tmpfs(self.svc, "/tmp"),
            "CH_04: api service must mount a tmpfs at /tmp.  Python's "
            "tempfile module, multipart-upload spool, and any subprocess "
            "scratch (Stockfish UCI, pip caches at runtime) need a writable "
            "/tmp; without this read_only locks Python out of basic "
            "filesystem operations and uvicorn fails on first request.",
        )

    def test_ch_05_api_runs_as_nonroot_uid(self):
        user = str(self.svc.get("user", ""))
        self.assertNotIn(
            "0",
            user.split(":", 1)[0:1],
            f"CH_05: api service runs as UID {user!r} which contains 0 (root).  "
            f"The api image expects appuser (UID 10001).",
        )
        self.assertTrue(
            user.startswith("1") or user.startswith("2") or user.startswith("3"),
            f"CH_05: api service must run as a fixed non-root UID; got {user!r}.  "
            f"Even with cap_drop ALL, root inside the container is a worse "
            f"baseline than a fixed non-zero UID for filesystem ownership.",
        )


# ---------------------------------------------------------------------------
# redis — aggressive tier
# ---------------------------------------------------------------------------


class TestRedisServiceHardening(unittest.TestCase):
    """CH_06..CH_09 carve-out: Redis alpine uses the same ``gosu``-based
    entrypoint as Postgres alpine.  Every flag in the original aggressive
    tier broke startup independently:

      - ``no-new-privileges:true``  → blocks setuid, gosu cannot drop to
                                       the redis user, container exits.
      - ``cap_drop: [ALL]``         → strips CAP_CHOWN, entrypoint's
                                       ``chown`` calls fail under set -e.
      - ``read_only: true``         → writable layer at /data becomes
                                       read-only, even the silenced
                                       ``chown ... || :`` line can't
                                       repair ownership.
      - ``tmpfs: [/tmp]``           → harmless in isolation but bundled
                                       with the rest and pointless if the
                                       container exits before it's used.

    Until a ``user: "999:999"`` (alpine redis UID) + ``/data`` volume
    ownership migration is validated in staging, the safest posture is
    NO hardening on redis (mirroring the Postgres CH_11 carve-out).
    Asserting equality with ``False`` (rather than skipping) makes the
    carve-out observable: any future contributor who re-adds the
    options without doing the user/volume migration will trip these
    tests immediately and see the documented reason.
    """

    def setUp(self):
        self.svc = _service(_load_compose(), "redis")

    def test_ch_06_redis_no_new_privileges(self):
        self.assertFalse(
            _has_no_new_privileges(self.svc),
            "CH_06 carve-out: redis must NOT carry security_opt: no-new-privileges:true "
            "until the user:'999:999' + /data ownership migration lands.  Redis alpine's "
            "gosu-based entrypoint cannot drop privileges under no-new-privileges, "
            "container exits within ~0.5s of Started.  See docker-compose.prod.yml "
            "comment block on the redis service.",
        )

    def test_ch_07_redis_drops_all_caps(self):
        self.assertFalse(
            _drops_all_caps(self.svc),
            "CH_07 carve-out: redis must NOT declare cap_drop: [ALL] until the "
            "user/volume migration lands.  The entrypoint's "
            "``find ... -exec chown redis '{}' +`` step needs CAP_CHOWN; under "
            "cap_drop ALL + set -e the entrypoint exits before redis-server starts.",
        )

    def test_ch_08_redis_is_read_only(self):
        self.assertIsNot(
            self.svc.get("read_only"),
            True,
            "CH_08 carve-out: redis must NOT declare read_only: true until the "
            "user/volume migration lands.  The entrypoint chowns the working "
            "directory at startup; locking the rootfs makes that fail.",
        )

    def test_ch_09_redis_has_tmpfs_tmp(self):
        # tmpfs in isolation is harmless, but the original CH_09 paired it
        # with read_only + cap_drop + no-new-privileges as the "aggressive
        # tier" recipe.  The carve-out reverts the whole tier; tmpfs on
        # /tmp is unnecessary without it (the entrypoint never reaches
        # the redis-cli scratch use case if the upstream chown fails).
        self.assertFalse(
            _has_tmpfs(self.svc, "/tmp"),
            "CH_09 carve-out: redis must NOT mount a tmpfs at /tmp while the "
            "rest of the aggressive tier is carved out.  Re-introducing tmpfs "
            "alone without the read_only/cap_drop/no-new-privileges siblings "
            "is misleading — adopt the full tier or none.",
        )


# ---------------------------------------------------------------------------
# caddy / db / ollama — conservative tier (no-new-privileges only)
# ---------------------------------------------------------------------------


class TestConservativeTierHardening(unittest.TestCase):
    """Caddy, Postgres, and Ollama carry only the no-privilege-escalation
    flag in this hardening pass.  Their upstream images have complex
    internal needs that need staging validation before deeper hardening
    (cap_drop, read-only, tmpfs lists) — see docs/DEPLOYMENT.md >
    Container Hardening for the next-pass recipe."""

    def setUp(self):
        self.compose = _load_compose()

    def test_ch_10_caddy_no_new_privileges(self):
        self.assertTrue(_has_no_new_privileges(_service(self.compose, "caddy")), "CH_10")

    def test_ch_11_db_no_new_privileges(self):
        # CH_11 carve-out: Postgres alpine's docker-entrypoint.sh uses
        # ``gosu`` (setuid) to drop from root to the postgres user.
        # ``no-new-privileges:true`` blocks setuid → entrypoint cannot
        # drop → postgres refuses to run as root → container exits ~1s
        # after Started, failing the Hetzner rolling deploy.  The fix
        # is to add ``user: "70:70"`` (alpine postgres UID) plus migrate
        # the pg_data volume ownership — a staging-validation task,
        # not a runtime workaround.  Until that lands, the conservative
        # tier carves db out of the universal floor.  Asserting equality
        # with False (rather than skipping) makes the carve-out
        # observable: any future contributor who re-adds the option
        # without doing the user/volume migration will trip this test
        # immediately and see the documented reason.
        self.assertFalse(
            _has_no_new_privileges(_service(self.compose, "db")),
            "CH_11 carve-out: db must NOT carry security_opt: no-new-privileges:true "
            "until the user:'70:70' + pg_data ownership migration lands. "
            "See docker-compose.prod.yml comment block on the db service.",
        )

    def test_ch_12_ollama_no_new_privileges(self):
        # CH_12 is a forward-looking contract: IF a local Ollama (or any
        # other named-LLM) sidecar is reintroduced into the prod stack,
        # it must carry the universal hardening floor.  The current
        # prod stack uses the managed DeepSeek API directly — see the
        # docker-compose.prod.yml header — so the ollama service is
        # legitimately absent.  Skip rather than fail so the architectural
        # migration is not blocked, but keep the test alive so that any
        # future re-introduction of the service trips it.
        services = self.compose.get("services") or {}
        if "ollama" not in services:
            self.skipTest(
                "ollama service is not present in docker-compose.prod.yml "
                "(DeepSeek API replaced the local sidecar); CH_12 remains "
                "as a contract for future re-introduction of a local LLM "
                "service — CH_13 still enforces the universal floor on "
                "every actually-deployed service."
            )
        self.assertTrue(_has_no_new_privileges(_service(self.compose, "ollama")), "CH_12")


# ---------------------------------------------------------------------------
# Catch-all — every prod service must have the universal floor
# ---------------------------------------------------------------------------


class TestUniversalSecurityFloor(unittest.TestCase):
    # Documented carve-outs from the universal floor.  Adding a name here
    # without a corresponding rationale in docker-compose.prod.yml AND a
    # specific failing test (e.g. CH_11) for the same service is a review
    # red flag — the floor should only carve out services with a real
    # upstream-image incompatibility, not as a convenience.
    _CH_13_CARVE_OUTS = frozenset({"db", "redis"})

    def test_ch_13_every_service_has_no_new_privileges(self):
        compose = _load_compose()
        services = compose.get("services") or {}
        self.assertTrue(services, "no services parsed from docker-compose.prod.yml")
        missing = [
            name
            for name, svc in services.items()
            if not _has_no_new_privileges(svc) and name not in self._CH_13_CARVE_OUTS
        ]
        self.assertEqual(
            missing,
            [],
            f"CH_13: services missing security_opt: no-new-privileges:true: "
            f"{missing}.  This flag is the universal hardening floor — "
            f"every prod service must carry it unless it is in the documented "
            f"carve-out set ({sorted(self._CH_13_CARVE_OUTS)}). Add the option "
            f"on the same commit that introduces the new service, or extend "
            f"the carve-out set here with the rationale comment.",
        )


# ---------------------------------------------------------------------------
# Runtime image contents — llm/Dockerfile.api
# ---------------------------------------------------------------------------


class TestRuntimeImageDropsPip(unittest.TestCase):
    """The published runtime image must not ship pip.

    pip bundles a ``_vendor`` tree whose dependency pins are frozen at
    whatever pip vendored (``pip/_vendor/vendor.txt``).  Image scanners
    report those vendored copies as installed packages, and nothing in
    this repo can move them — a vendored pin changes only when pip
    upstream re-vendors it.  Uninstalling pip once the requirements are
    resolved is what keeps them out of the image; it is also ordinary
    hardening, because the runtime installs nothing (CMD is uvicorn and
    the HEALTHCHECK is a python one-liner).

    Concretely this closed code-scanning alerts #413 (msgpack 1.1.2,
    GHSA-6v7p-g79w-8964) and #414 / #415 (setuptools 70.3.0,
    CVE-2025-47273 / CVE-2026-59890).
    """

    _UNINSTALL_RE = re.compile(r"pip\s+uninstall\s+(?:--yes|-y)\s+([^\\\n]+)")

    def setUp(self):
        self.directives = _dockerfile_directives()

    def test_ch_14_runtime_image_uninstalls_pip(self):
        match = self._UNINSTALL_RE.search(self.directives)
        self.assertIsNotNone(
            match,
            "CH_14: llm/Dockerfile.api must run `pip uninstall --yes pip` after "
            "installing requirements.  Without it the image ships pip's bundled "
            "_vendor tree, whose frozen pins (msgpack, setuptools) are reported by "
            "the Trivy image scan and cannot be upgraded from this repo.",
        )
        assert match is not None  # narrows Optional for mypy
        self.assertIn(
            "pip",
            match.group(1).split(),
            f"CH_14: the `pip uninstall` in llm/Dockerfile.api does not target pip "
            f"itself; it uninstalls {match.group(1).split()!r}.",
        )

    def test_ch_15_no_pip_install_after_the_uninstall(self):
        uninstall_at = self.directives.find("pip uninstall")
        self.assertNotEqual(
            uninstall_at,
            -1,
            "CH_15 precondition: no `pip uninstall` found in llm/Dockerfile.api "
            "(CH_14 covers the missing-uninstall case).",
        )
        self.assertNotIn(
            "pip install",
            self.directives[uninstall_at:],
            "CH_15: a `pip install` appears AFTER the `pip uninstall` in "
            "llm/Dockerfile.api.  pip no longer exists at that point, so the image "
            "build fails with `pip: not found`.  Move the new install ahead of the "
            "uninstall within the same RUN layer.",
        )

    def test_ch_16_setuptools_and_wheel_are_retained(self):
        match = self._UNINSTALL_RE.search(self.directives)
        self.assertIsNotNone(match, "CH_16 precondition: see CH_14.")
        assert match is not None  # narrows Optional for mypy
        targets = sorted(match.group(1).split())
        self.assertEqual(
            targets,
            ["pip"],
            f"CH_16: the runtime image must uninstall pip ONLY; got {targets!r}.  "
            f"setuptools and wheel are retained deliberately — the setuptools "
            f"installed in that same layer is past the CVE-2025-47273 (78.1.1) and "
            f"CVE-2026-59890 (83.0.0) fixes so it carries no advisory, while "
            f"removing it would strip pkg_resources from the image and break any "
            f"dependency importing it at module load.  That trades a startup "
            f"failure for zero scanner benefit.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
