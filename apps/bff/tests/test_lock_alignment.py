"""The BFF runs the waiter in-process: it must use the stack the waiter was validated with."""

import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
AGENT_LOCK = REPO / "agents/restaurant/src/restaurant_agent/uv.lock"
BFF_DIR = Path(__file__).resolve().parents[1]
BFF_LOCK = BFF_DIR / "uv.lock"
FIX = (
    "Regenerate [tool.uv] constraint-dependencies in apps/bff/pyproject.toml from "
    "the waiter lock (see apps/bff/README.md) and run `uv lock` in apps/bff."
)


def registry_versions(lock: Path) -> dict[str, str]:
    if not lock.is_file():
        pytest.fail(f"Missing lockfile: {lock.relative_to(REPO)}")
    packages = tomllib.loads(lock.read_text(encoding="utf-8"))["package"]
    return {
        package["name"]: package["version"]
        for package in packages
        if "registry" in package["source"]
    }


def test_constraints_pin_every_package_of_the_waiter_lock() -> None:
    project = tomllib.loads((BFF_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    constraints = dict(
        item.split("==", 1) for item in project["tool"]["uv"]["constraint-dependencies"]
    )
    expected = registry_versions(AGENT_LOCK)
    assert constraints == expected, FIX


def test_shared_packages_have_the_same_version_in_both_locks() -> None:
    agent = registry_versions(AGENT_LOCK)
    bff = registry_versions(BFF_LOCK)
    drift = {
        name: (agent[name], bff[name])
        for name in agent.keys() & bff.keys()
        if agent[name] != bff[name]
    }
    assert not drift, f"Versions differ (waiter, BFF): {drift}. {FIX}"
    for package in ("agent-framework-core", "agent-framework-foundry", "openai", "azure-identity"):
        assert package in bff, f"{package} is missing from the BFF lock"
