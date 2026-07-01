"""Guards the config-flow translation keys and strings.json <-> en.json sync.

A custom component loads runtime text from translations/<lang>.json (NOT strings.json),
and does not resolve [%key:...] references, so en.json must ship literal English and stay
structurally in sync with the developer-source strings.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import custom_components.voltcraft_sem6000_spb012ble as integration

COMPONENT_DIR = Path(integration.__file__).parent
STRINGS = json.loads((COMPONENT_DIR / "strings.json").read_text())
EN = json.loads((COMPONENT_DIR / "translations" / "en.json").read_text())

REQUIRED_KEYS = [
    ("config", "error", "invalid_pin"),
    ("config", "step", "confirm", "data", "pin"),
    ("config", "step", "confirm", "data_description", "pin"),
    ("config", "step", "reauth_confirm", "data", "pin"),
    ("config", "step", "reauth_confirm", "description"),
    ("config", "step", "reconfigure", "data", "pin"),
    ("config", "step", "reconfigure", "description"),
    ("config", "abort", "reauth_successful"),
    ("config", "abort", "reconfigure_successful"),
]

GUIDANCE_KEYS = [
    ("config", "step", "confirm", "data_description", "pin"),
    ("config", "step", "reauth_confirm", "description"),
    ("config", "step", "reconfigure", "description"),
]


def _get(tree, path):
    for key in path:
        tree = tree[key]
    return tree


def _key_structure(tree):
    if isinstance(tree, dict):
        return {key: _key_structure(value) for key, value in sorted(tree.items())}
    return None


def test_required_keys_present_in_both_files():
    for path in REQUIRED_KEYS:
        _get(STRINGS, path)  # raises KeyError if missing
        _get(EN, path)


def test_files_have_identical_key_structure():
    assert _key_structure(STRINGS) == _key_structure(EN)


def test_en_json_has_no_unresolved_key_references():
    def walk(tree):
        if isinstance(tree, dict):
            for value in tree.values():
                walk(value)
        elif isinstance(tree, str):
            assert "[%key:" not in tree, f"unresolved reference in en.json: {tree!r}"

    walk(EN)


def test_guidance_carriers_are_non_empty():
    for path in GUIDANCE_KEYS:
        assert _get(EN, path).strip(), f"empty guidance string at {path}"
