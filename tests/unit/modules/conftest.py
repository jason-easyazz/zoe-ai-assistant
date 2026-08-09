"""Shared loaders for module-system tooling tests.

Module directory names are hyphenated, so they are not importable as normal
packages — load the files by path.
"""
import importlib.util
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_compose_generator():
    return _load("zoe_gen_module_compose", "tools/generate_module_compose.py")


def load_module_validator():
    return _load("zoe_validate_module", "tools/validate_module.py")
