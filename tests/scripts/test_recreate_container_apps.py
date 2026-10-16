import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "recreate-container-apps.py"
SPEC = importlib.util.spec_from_file_location("recreate_container_apps", SCRIPT_PATH)
SCRIPT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SCRIPT
SPEC.loader.exec_module(SCRIPT)

SUBSCRIPTION = "11111111-2222-3333-4444-555555555555"
APPS = ("test-mcp", "test-kitchen", "test-cashier", "test-agent", "test-bff", "test-frontend")
CONFIG = SCRIPT.Config(SUBSCRIPTION, "rg-test", APPS)


class FakeAzure:
    def __init__(self, mode="Single", auto_retire=True):
        self.config = CONFIG
        self.mode = mode
        self.auto_retire = auto_retire
        self.calls = []
        self.active = {name: {f"{name}--old"} for name in APPS}
        self.fail_copy = None
        self.fail_ready = None
        self.bad_preflight = None
        self.no_ingress = False
        self.concurrent_after_copy = False

    def command(self, *args, query=None, timeout=None):
        self.calls.append(args)
        name = args[args.index("--name") + 1]
        if args[0] == "show":
            return {
                "mode": self.mode,
                "source": None if name == self.bad_preflight else f"{name}--old",
                "ingress": None if self.no_ingress else {"external": name == APPS[-1]},
            }
        if args[:2] == ("revision", "list"):
            return sorted(self.active[name])
        if args[:2] == ("revision", "copy"):
            if name == self.fail_copy:
                raise SCRIPT.ScriptError("simulated copy failure")
            revision = f"{name}--{args[args.index('--revision-suffix') + 1]}"
            if self.mode == "Single" and self.auto_retire:
                self.active[name].clear()
            self.active[name].add(revision)
        elif args[:2] == ("revision", "show"):
            if self.concurrent_after_copy:
                self.active[name].add(f"{name}--concurrent")
            return {
                "active": True,
                "provisioning": "Failed" if name == self.fail_ready else "Provisioned",
                "health": "Healthy",
                "running": "Running",
            }
        elif args[:3] == ("ingress", "traffic", "set"):
            pass
        elif args[:2] == ("revision", "deactivate"):
            self.active[name].remove(args[args.index("--revision") + 1])
        else:
            raise AssertionError(f"Unexpected Azure command: {args}")


class RecreateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env = Path(self.temporary.name) / "container apps.env"
        self.values = {
            "AZURE_SUBSCRIPTION_ID": SUBSCRIPTION,
            "AZURE_RESOURCE_GROUP": "rg-test",
            **dict(zip(SCRIPT.APP_KEYS, APPS)),
        }
        self.write_env()

    def write_env(self):
        self.env.write_text(
            "# deployment configuration\n"
            + "".join(f'{key}="{value}"\n' for key, value in self.values.items()),
            encoding="utf-8",
        )

    def run_script(self, fake, *args):
        output = io.StringIO()
        with (
            patch.object(SCRIPT, "Azure", return_value=fake),
            patch.object(SCRIPT.shutil, "which", return_value="/fake/az"),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            result = SCRIPT.main([str(self.env), *args])
        return result, output.getvalue()

    def test_reads_existing_deployment_format_and_dependency_order(self):
        self.assertEqual(SCRIPT.load_config(self.env), CONFIG)

    def test_cashier_name_has_same_default_as_deployment(self):
        del self.values["CASHIER_AGENT_APP_NAME"]
        self.write_env()
        self.assertEqual(SCRIPT.load_config(self.env).apps[2], "morcilla-cashier")

    def test_configuration_is_parsed_not_executed(self):
        marker = Path(self.temporary.name) / "must-not-exist"
        with self.env.open("a") as stream:
            stream.write(f'UNUSED_SETTING="$(touch {marker})" # not shell code\n')
        self.assertEqual(SCRIPT.load_config(self.env), CONFIG)
        self.assertFalse(marker.exists())

    def test_invalid_configuration_is_rejected_before_azure(self):
        for key, value in (
            ("AZURE_SUBSCRIPTION_ID", "00000000-0000-0000-0000-000000000000"),
            ("AZURE_RESOURCE_GROUP", "rg-invalid."),
            ("BFF_APP_NAME", "bad--name"),
            ("FRONTEND_APP_NAME", "test-bff"),
            ("MCP_APP_NAME", ""),
        ):
            with self.subTest(key=key):
                original = self.values[key]
                self.values[key] = value
                self.write_env()
                fake = FakeAzure()
                result, _ = self.run_script(fake, "--yes")
                self.assertEqual(result, 1)
                self.assertEqual(fake.calls, [])
                self.values[key] = original

    def test_invalid_env_syntax_is_not_silently_ignored(self):
        for line in ('INVALID="unterminated', "export NAME=value", "INVALID=two values"):
            with self.subTest(line=line):
                self.write_env()
                with self.env.open("a") as stream:
                    stream.write(line)
                with self.assertRaises(SCRIPT.ScriptError):
                    SCRIPT.load_config(self.env)

    def test_dry_run_makes_no_azure_calls_and_does_not_print_unused_values(self):
        with self.env.open("a") as stream:
            stream.write('UNUSED_SETTING="private-test-value"\n')
        fake = FakeAzure()
        result, output = self.run_script(fake, "--dry-run")
        self.assertEqual(result, 0)
        self.assertEqual(fake.calls, [])
        self.assertIn("DRY RUN", output)
        self.assertNotIn("private-test-value", output)
        self.assertTrue(all(name in output for name in APPS))

    def test_confirmation_and_positive_timeout_are_required(self):
        for args in ([], ["--yes", "--dry-run"], ["--yes", "--timeout", "0"], ["--yes", "--timeout", "bad"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    SCRIPT.main([str(self.env), *args])
                self.assertEqual(error.exception.code, 2)

    def test_preflights_all_six_before_any_mutation(self):
        fake = FakeAzure()
        fake.bad_preflight = APPS[-1]
        result, output = self.run_script(fake, "--yes")
        self.assertEqual(result, 1)
        self.assertIn("no active latest ready revision", output)
        self.assertFalse(any(call[:2] == ("revision", "copy") for call in fake.calls))

    def test_single_mode_creates_six_fresh_revisions_in_dependency_order(self):
        fake = FakeAzure()
        result, _ = self.run_script(fake, "--yes")
        self.assertEqual(result, 0)
        copies = [call for call in fake.calls if call[:2] == ("revision", "copy")]
        self.assertEqual([call[call.index("--name") + 1] for call in copies], list(APPS))
        self.assertEqual(sum(call[0] == "show" for call in fake.calls[:12]), 6)
        for name, call in zip(APPS, copies):
            self.assertEqual(call[call.index("--from-revision") + 1], f"{name}--old")
            suffix = call[call.index("--revision-suffix") + 1]
            self.assertRegex(suffix, r"^reset-\d{14}-[0-9a-f]{8}$")
            self.assertEqual(fake.active[name], {f"{name}--{suffix}"})
            self.assertNotIn("--image", call)
            self.assertNotIn("--set-env-vars", call)
        self.assertFalse(any(call[:3] == ("ingress", "traffic", "set") for call in fake.calls))

    def test_multiple_mode_routes_traffic_then_retires_every_old_revision(self):
        fake = FakeAzure(mode="Multiple")
        for name in APPS:
            fake.active[name].add(f"{name}--older")
        result, _ = self.run_script(fake, "--yes")
        self.assertEqual(result, 0)
        for name in APPS:
            calls = [call for call in fake.calls if call[call.index("--name") + 1] == name]
            traffic = next(index for index, call in enumerate(calls) if call[:3] == ("ingress", "traffic", "set"))
            retired = [index for index, call in enumerate(calls) if call[:2] == ("revision", "deactivate")]
            self.assertEqual(len(retired), 2)
            self.assertTrue(all(traffic < index for index in retired))
            self.assertEqual(len(fake.active[name]), 1)
            revision = next(iter(fake.active[name]))
            self.assertEqual(calls[traffic][-1], f"{revision}=100")

    def test_single_mode_also_retires_old_revisions_if_still_active(self):
        fake = FakeAzure(auto_retire=False)
        result, _ = self.run_script(fake, "--yes")
        self.assertEqual(result, 0)
        self.assertEqual(sum(call[:2] == ("revision", "deactivate") for call in fake.calls), 6)

    def test_multiple_mode_without_ingress_does_not_try_to_route_traffic(self):
        fake = FakeAzure(mode="Multiple")
        fake.no_ingress = True
        result, _ = self.run_script(fake, "--yes")
        self.assertEqual(result, 0)
        self.assertFalse(any(call[:3] == ("ingress", "traffic", "set") for call in fake.calls))
        self.assertTrue(all(len(revisions) == 1 for revisions in fake.active.values()))

    def test_concurrent_deployment_is_not_overwritten_before_copy(self):
        fake = FakeAzure()
        app = SCRIPT.preflight(fake)[0]
        fake.active[app.name].add(f"{app.name}--concurrent")
        with self.assertRaisesRegex(SCRIPT.ScriptError, "changed concurrently"):
            SCRIPT.recreate(fake, app, "reset-test", 600)
        self.assertFalse(any(call[:2] == ("revision", "copy") for call in fake.calls))

    def test_concurrent_deployment_after_copy_is_not_routed_or_retired(self):
        fake = FakeAzure(mode="Multiple")
        fake.concurrent_after_copy = True
        result, output = self.run_script(fake, "--yes")
        self.assertEqual(result, 1)
        self.assertIn("changed concurrently", output)
        self.assertFalse(any(call[:3] == ("ingress", "traffic", "set") for call in fake.calls))
        self.assertFalse(any(call[:2] == ("revision", "deactivate") for call in fake.calls))

    def test_failed_copy_or_revision_stops_without_resetting_downstream_apps(self):
        for failure in ("fail_copy", "fail_ready"):
            with self.subTest(failure=failure):
                fake = FakeAzure()
                setattr(fake, failure, APPS[1])
                result, output = self.run_script(fake, "--yes")
                self.assertEqual(result, 1)
                self.assertIn("ERROR:", output)
                self.assertIn("not automatically rolled back", output)
                copies = [call for call in fake.calls if call[:2] == ("revision", "copy")]
                self.assertEqual(len(copies), 2)

    def test_cli_uses_explicit_subscription_no_shell_and_bounded_timeout(self):
        azure = SCRIPT.Azure(CONFIG, 600)
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(["revision"]), stderr="")
        with patch.object(SCRIPT.subprocess, "run", return_value=completed) as run:
            self.assertEqual(azure.command("show", "--name", APPS[0], query="name", timeout=4.5), ["revision"])
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--subscription") + 1], SUBSCRIPTION)
        self.assertEqual(command[command.index("--resource-group") + 1], "rg-test")
        self.assertEqual(run.call_args.kwargs["timeout"], 4.5)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_cli_errors_and_invalid_json_are_not_silenced(self):
        azure = SCRIPT.Azure(CONFIG, 600)
        with patch.object(SCRIPT.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "permission denied")):
            with self.assertRaisesRegex(SCRIPT.ScriptError, "permission denied"):
                azure.command("show", "--name", APPS[0])
        with patch.object(SCRIPT.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "bad JSON", "")):
            with self.assertRaises(json.JSONDecodeError):
                azure.command("show", "--name", APPS[0], query="name")

    def test_wait_timeout_is_an_explicit_failure(self):
        with patch.object(SCRIPT.time, "monotonic", side_effect=[0, 1, 2, 3]), patch.object(SCRIPT.time, "sleep"):
            with self.assertRaisesRegex(SCRIPT.ScriptError, "Timed out"):
                SCRIPT.wait_for(lambda remaining: False, "healthy revision", 2)


if __name__ == "__main__":
    unittest.main()
