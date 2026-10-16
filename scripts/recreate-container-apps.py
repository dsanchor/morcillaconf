#!/usr/bin/env python3
"""Recreate the demo's six Container Apps revisions with fresh ephemeral storage."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

APP_KEYS = (
    "MCP_APP_NAME",
    "KITCHEN_AGENT_APP_NAME",
    "CASHIER_AGENT_APP_NAME",
    "RESTAURANT_AGENT_APP_NAME",
    "BFF_APP_NAME",
    "FRONTEND_APP_NAME",
)
DEFAULT_ENV = Path(__file__).resolve().with_name("container-apps.env")


class ScriptError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    subscription: str
    resource_group: str
    apps: tuple[str, ...]


@dataclass(frozen=True)
class App:
    name: str
    source: str
    mode: str
    ingress: bool
    previous: frozenset[str]


def load_config(path: Path) -> Config:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ScriptError(f"{path}:{number}: expected NAME=value")
        try:
            parsed = shlex.split(value, comments=True, posix=True)
        except ValueError as exc:
            raise ScriptError(f"{path}:{number}: {exc}") from exc
        if len(parsed) != 1:
            raise ScriptError(f"{path}:{number}: expected exactly one value")
        values[key] = parsed[0]

    values["CASHIER_AGENT_APP_NAME"] = values.get("CASHIER_AGENT_APP_NAME") or "morcilla-cashier"
    for key in ("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", *APP_KEYS):
        if not values.get(key):
            raise ScriptError(f"Required variable {key} is missing or empty in {path}")
    subscription = values["AZURE_SUBSCRIPTION_ID"]
    try:
        subscription_id = uuid.UUID(subscription)
    except ValueError as exc:
        raise ScriptError("AZURE_SUBSCRIPTION_ID must be a subscription UUID") from exc
    if str(subscription_id) != subscription.lower() or subscription_id.int == 0:
        raise ScriptError("Replace AZURE_SUBSCRIPTION_ID with a non-placeholder subscription UUID")
    group = values["AZURE_RESOURCE_GROUP"]
    if not re.fullmatch(r"[A-Za-z0-9_.()_-]{1,90}", group) or group.endswith("."):
        raise ScriptError("AZURE_RESOURCE_GROUP is invalid")
    apps = tuple(values[key] for key in APP_KEYS)
    for key, name in zip(APP_KEYS, apps):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}[a-z0-9]", name) or "--" in name:
            raise ScriptError(f"{key} must be a valid 2-32 character Container App name")
    if len(set(apps)) != len(apps):
        raise ScriptError("The six Container App names must be distinct")
    return Config(subscription, group, apps)


class Azure:
    def __init__(self, config: Config, timeout: int):
        self.config = config
        self.timeout = timeout

    def command(self, *args: str, query: str | None = None, timeout: float | None = None):
        command = [
            "az", "containerapp", *args,
            "--subscription", self.config.subscription,
            "--resource-group", self.config.resource_group,
            "--only-show-errors", "--output", "json" if query is not None else "none",
        ]
        if query is not None:
            command.extend(("--query", query))
        result = subprocess.run(
            command, text=True, capture_output=True,
            timeout=min(self.timeout, timeout) if timeout is not None else self.timeout,
        )
        if result.returncode:
            raise ScriptError(f"{shlex.join(command)} failed:\n{result.stderr.strip()}")
        return json.loads(result.stdout) if query is not None else None


def active_revisions(azure: Azure, name: str, timeout: float | None = None) -> frozenset[str]:
    names = azure.command(
        "revision", "list", "--name", name,
        query="[?properties.active].name", timeout=timeout,
    )
    if not isinstance(names, list) or not all(isinstance(item, str) and item.startswith(f"{name}--") for item in names):
        raise ScriptError(f"Unexpected active revision list for {name}")
    return frozenset(names)


def preflight(azure: Azure) -> list[App]:
    apps = []
    for name in azure.config.apps:
        data = azure.command(
            "show", "--name", name,
            query="{mode:properties.configuration.activeRevisionsMode,source:properties.latestReadyRevisionName,ingress:properties.configuration.ingress}",
        )
        if not isinstance(data, dict) or data.get("mode") not in ("Single", "Multiple"):
            raise ScriptError(f"Unexpected revision mode for {name}")
        source = data.get("source")
        previous = active_revisions(azure, name)
        if not isinstance(source, str) or source not in previous:
            raise ScriptError(f"{name} has no active latest ready revision to copy; inspect its revisions first")
        apps.append(App(name, source, data["mode"], data.get("ingress") is not None, previous))
    return apps


def wait_for(check: Callable[[float], bool], description: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        if check(remaining):
            return
        time.sleep(max(0, min(5, deadline - time.monotonic())))
    raise ScriptError(f"Timed out after {timeout}s waiting for {description}")


def revision_ready(azure: Azure, app: str, revision: str, remaining: float) -> bool:
    state = azure.command(
        "revision", "show", "--name", app, "--revision", revision,
        query="{active:properties.active,provisioning:properties.provisioningState,health:properties.healthState,running:properties.runningState}",
        timeout=remaining,
    )
    if not isinstance(state, dict):
        raise ScriptError(f"Unexpected revision status for {revision}")
    if state.get("provisioning") in ("Failed", "Deprovisioned") or state.get("running") == "Failed":
        raise ScriptError(f"{revision} failed to start: {state}")
    return state.get("active") is True and state.get("provisioning") == "Provisioned" and state.get("health") == "Healthy"


def recreate(azure: Azure, app: App, suffix: str, timeout: int) -> None:
    revision = f"{app.name}--{suffix}"
    if active_revisions(azure, app.name) != app.previous:
        raise ScriptError(f"Revisions changed concurrently for {app.name}; no new revision was created")
    print(f"==> {app.name}: copying {app.source} to {revision}", flush=True)
    azure.command(
        "revision", "copy", "--name", app.name, "--from-revision", app.source,
        "--revision-suffix", suffix,
    )
    wait_for(
        lambda remaining: revision_ready(azure, app.name, revision, remaining),
        f"{revision} to be healthy", timeout,
    )
    active = active_revisions(azure, app.name)
    if revision not in active or active - app.previous - {revision}:
        raise ScriptError(f"Revisions changed concurrently for {app.name}; inspect before retiring old revisions")
    if app.mode == "Multiple" and app.ingress:
        azure.command(
            "ingress", "traffic", "set", "--name", app.name,
            "--revision-weight", f"{revision}=100",
        )
    for old in sorted(active - {revision}):
        azure.command("revision", "deactivate", "--name", app.name, "--revision", old)
    wait_for(
        lambda remaining: active_revisions(azure, app.name, remaining) == frozenset({revision}),
        f"{app.name} to have only the new revision active", timeout,
    )
    print(f"==> {app.name}: healthy; old revisions inactive", flush=True)


def positive_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a positive integer") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive integer")
    return seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env_file", nargs="?", type=Path, default=DEFAULT_ENV)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate and display targets without calling Azure")
    mode.add_argument("--yes", action="store_true", help="Confirm resetting all six apps' ephemeral state")
    parser.add_argument("--timeout", type=positive_seconds, default=600, help="Maximum seconds per CLI call or readiness/retirement wait (default: 600)")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.env_file)
        print(f"Subscription: {config.subscription}\nResource group: {config.resource_group}")
        print("WARNING: fresh revisions lose ephemeral files, SQLite databases, visits, memory and seating state.")
        print("Persistent volumes, Azure Files, images, identities and app configuration are not deleted.")
        print("Multiple-revision apps will move all traffic to the new revision and retire all old revisions.")
        for name in config.apps:
            print(f"  - {name}")
        if args.dry_run:
            print("DRY RUN: no Azure commands executed.")
            return 0
        if shutil.which("az") is None:
            raise ScriptError("Required command not found: az (install Azure CLI and its containerapp extension)")
        azure = Azure(config, args.timeout)
        apps = preflight(azure)
        suffix = f"reset-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
        for app in apps:
            recreate(azure, app, suffix, args.timeout)
        print("All six apps have fresh, healthy revisions; old revisions are inactive.")
        return 0
    except (ScriptError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Stopped. Any revisions already created are not automatically rolled back.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
