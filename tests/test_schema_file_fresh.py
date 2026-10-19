"""Audit 2026-10-19 — item 8: the committed schema.yaml documents every route.

The web and mobile clients generate from `schema.yaml`; an endpoint missing
from it is an endpoint their generated client cannot call.
"""
from pathlib import Path

import yaml
from drf_spectacular.generators import SchemaGenerator

ROOT = Path(__file__).resolve().parent.parent


def test_committed_schema_lists_every_current_path():
    committed = yaml.safe_load((ROOT / "schema.yaml").read_text(encoding="utf-8"))
    generated = SchemaGenerator().get_schema(request=None, public=True)
    missing = sorted(set(generated["paths"]) - set(committed["paths"]))
    assert missing == [], f"schema.yaml is stale, regenerate it: {missing}"


def test_no_merge_leftover_schema_file():
    assert not (ROOT / "schema.yaml.orig").exists()
