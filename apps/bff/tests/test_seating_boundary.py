"""The waiter is the only client of the seating MCP: the BFF never talks to it."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from bff.config import BffSettings

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_MODULES = ("mcp", "restaurant_mcp", "restaurant_agent.seating_gateway")
FORBIDDEN_NAMES = ("MCPStreamableHTTPTool", "streamable_http_client", "ClientSession", "call_tool")


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize("path", sorted((ROOT / "src").rglob("*.py")), ids=lambda path: path.name)
def test_no_bff_module_imports_an_mcp_client(path: Path) -> None:
    for name in _imports(path):
        assert not any(
            name == forbidden or name.startswith(f"{forbidden}.") for forbidden in FORBIDDEN_MODULES
        ), f"{path.name} imports {name}"
    source = path.read_text(encoding="utf-8")
    for name in FORBIDDEN_NAMES:
        assert name not in source, f"{path.name} uses {name}"


def test_mcp_is_not_a_bff_dependency() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = [item.split(">")[0].split("=")[0].split("<")[0].strip().lower() for item in project["dependencies"]]
    assert "mcp" not in dependencies


def test_the_bff_refuses_a_seating_mcp_url(monkeypatch) -> None:
    monkeypatch.setenv("SEATING_MCP_URL", "http://127.0.0.1:8080/mcp")
    with pytest.raises(ValueError, match="only client of the seating MCP"):
        BffSettings(_env_file=None, waiter_agent_url="http://test-waiter.invalid")


def test_the_application_gateway_is_gone() -> None:
    assert not (ROOT / "src" / "bff" / "seating.py").exists()
