from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "voltcraft_sem6000_spb012ble"


class PackageConsistencyTests(unittest.TestCase):
    def test_const_defines_all_required_runtime_names(self) -> None:
        tree = ast.parse((COMPONENT / "const.py").read_text(encoding="utf-8"))
        defined = {
            target.id
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Name)
        }
        required = {
            "COMMAND_UUID",
            "CONF_PIN",
            "DEFAULT_PIN",
            "DEVICE_INFO_UUID",
            "DEVICE_NAME",
            "DEVICE_NAME_UUID",
            "DOMAIN",
            "NOTIFY_UUID",
            "SCAN_INTERVAL",
            "SERVICE_UUID",
        }
        self.assertEqual(required - defined, set())

    def test_device_name_characteristic_is_preserved(self) -> None:
        source = (COMPONENT / "const.py").read_text(encoding="utf-8")
        self.assertIn(
            'DEVICE_NAME_UUID = "00002a00-0000-1000-8000-00805f9b34fb"',
            source,
        )

    def test_manifest_is_beta_6_and_uses_core_bluetooth_packages(self) -> None:
        manifest = json.loads(
            (COMPONENT / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["version"], "2.0.0-beta.6")
        self.assertEqual(manifest["codeowners"], ["@citizenserious"])
        self.assertNotIn("requirements", manifest)
        self.assertIn("bluetooth", manifest["dependencies"])

    def test_config_flow_masks_pin_without_prefilling_options(self) -> None:
        source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
        self.assertIn("TextSelectorType.PASSWORD", source)
        self.assertIn("vol.Optional(CONF_PIN): _PIN_SELECTOR", source)
        self.assertNotIn("current_pin", source)
        self.assertNotIn("default=current_pin", source)

    def test_destructive_actions_are_admin_only_and_confirmed(self) -> None:
        source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("async_register_admin_service("), 4)
        for service in (
            '"change_pin"',
            '"reset_pin"',
            '"reset_consumption"',
            '"factory_reset"',
        ):
            self.assertIn(service, source)
        self.assertIn("vol.Required(CONF_CONFIRM): cv.boolean", source)

    def test_routine_actions_check_outlet_control_permission(self) -> None:
        source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("POLICY_CONTROL", source)
        self.assertIn("user.permissions.check_entity", source)
        self.assertIn("controlled_coordinator", source)
        self.assertNotIn("entity_id.startswith(\"switch.\")", source)

    def test_github_actions_are_sha_pinned_and_read_only(self) -> None:
        action_ref = re.compile(r"uses:\s+[^@\s]+@([0-9a-f]{40})(?:\s|$)")
        workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        self.assertTrue(workflows)
        for workflow in workflows:
            source = workflow.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow.name):
                uses_lines = [
                    line.strip() for line in source.splitlines() if "uses:" in line
                ]
                self.assertTrue(uses_lines)
                self.assertTrue(all(action_ref.search(line) for line in uses_lines))
                self.assertIn("permissions:\n  contents: read", source)


if __name__ == "__main__":
    unittest.main()
