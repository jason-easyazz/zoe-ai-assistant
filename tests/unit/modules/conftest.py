"""Shared loaders for the zoe-music module (its dir name is hyphenated, so it
is not importable as a normal package — load the files by path)."""
import importlib.util
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_music_main():
    return _load("zoe_music_main", "modules/zoe-music/main.py")


def load_music_handlers():
    return _load("zoe_music_handlers", "modules/zoe-music/intents/handlers.py")


def load_compose_generator():
    return _load("zoe_gen_module_compose", "tools/generate_module_compose.py")
