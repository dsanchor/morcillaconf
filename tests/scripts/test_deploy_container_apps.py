import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts/deploy-container-apps.sh"
TRANSFORM_START = '  python3 - "$spec_file" "$image" "${env_vars[@]}" <<\'PY\'\n'
TRANSFORM = SCRIPT_PATH.read_text(encoding="utf-8").split(TRANSFORM_START, 1)[1].split(
    "\nPY\n", 1
)[0]


class DeploymentTemplateTests(unittest.TestCase):
    def transform(self, spec):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            subprocess.run(
                [
                    sys.executable, "-", str(path), "example/new-image:latest",
                    "APP_ENVIRONMENT=production", "VALUE=contains=equals",
                ],
                input=TRANSFORM,
                text=True,
                capture_output=True,
                check=True,
            )
            return json.loads(path.read_text(encoding="utf-8"))

    def spec(self):
        return {
            "name": "kitchen-agent",
            "identity": {"type": "UserAssigned"},
            "properties": {
                "configuration": {
                    "registries": [{"server": "old.example"}],
                    "ingress": {"external": False, "targetPort": 8089},
                },
                "template": {
                    "revisionSuffix": "reset-20261016195338-8621b80f",
                    "containers": [{
                        "name": "kitchen",
                        "image": "example/old-image:latest",
                        "env": [{"name": "OLD", "value": "old"}],
                        "resources": {"cpu": 0.5, "memory": "1Gi"},
                        "volumeMounts": [{"volumeName": "old", "mountPath": "/data"}],
                    }],
                    "volumes": [{"name": "old", "storageType": "AzureFile"}],
                    "scale": {"minReplicas": 0, "maxReplicas": 3, "rules": []},
                },
            },
        }

    def test_update_drops_inherited_suffix_for_all_six_apps(self):
        for app in ("mcp", "kitchen", "cashier", "waiter", "bff", "frontend"):
            with self.subTest(app=app):
                spec = self.spec()
                spec["name"] = app

                updated = self.transform(spec)

                self.assertNotIn("revisionSuffix", updated["properties"]["template"])
                self.assertEqual(updated["name"], app)

    def test_update_preserves_other_deployment_behavior(self):
        original = self.spec()

        updated = self.transform(original)

        properties = updated["properties"]
        template = properties["template"]
        container = template["containers"][0]
        self.assertEqual(container["image"], "example/new-image:latest")
        self.assertEqual(container["env"], [
            {"name": "APP_ENVIRONMENT", "value": "production"},
            {"name": "VALUE", "value": "contains=equals"},
        ])
        self.assertEqual(container["resources"], {"cpu": 0.5, "memory": "1Gi"})
        self.assertNotIn("volumeMounts", container)
        self.assertEqual(template["volumes"], [])
        self.assertEqual(template["scale"], {
            "minReplicas": 1, "maxReplicas": 1, "rules": [],
        })
        self.assertEqual(properties["configuration"]["registries"], [])
        self.assertEqual(
            properties["configuration"]["ingress"],
            original["properties"]["configuration"]["ingress"],
        )
        self.assertEqual(updated["identity"], original["identity"])

    def test_update_without_a_suffix_remains_repeatable(self):
        spec = self.spec()
        del spec["properties"]["template"]["revisionSuffix"]

        updated = self.transform(spec)
        repeated = self.transform(updated)

        self.assertNotIn("revisionSuffix", updated["properties"]["template"])
        self.assertEqual(repeated, updated)


if __name__ == "__main__":
    unittest.main()
