"""Import blocker for the Kokoro TTS venv (``~/.zoe/venvs/kokoro-py310``).

Installed by ``scripts/setup/build_kokoro_venv.sh`` into the venv's site-packages
as ``zoe_kokoro_import_block.py`` plus a one-line ``zoe_kokoro_import_block.pth``,
so it runs at interpreter start-up — before ``kokoro_sidecar.py`` imports anything.

Why it exists
-------------
The sidecar shares the system Python 3.10 site-packages (``~/.local``) because the
NVIDIA CUDA torch wheel lives there (1.8 GB, no recorded source — rebuilding it into
a clean venv is not reproducible offline). That site-packages also carries
scikit-learn, pandas and pyarrow for other tools, and transformers imports them
merely because they are *installed*:

    transformers.generation.candidate_generator
      -> is_sklearn_available()  (importlib.util.find_spec("sklearn"))
      -> sklearn.metrics -> sklearn.utils.fixes -> pandas -> pyarrow

Kokoro uses only ``AlbertModel``; nothing on its synthesis path needs any of them.

How
---
``sys.modules[name] = None`` is the documented import stop: ``import name`` raises
``ModuleNotFoundError`` and ``importlib.util.find_spec(name)`` returns ``None``, so
every "is it available?" probe answers no and takes its no-dependency branch. Nothing
is uninstalled; other interpreters sharing the site-packages are unaffected.

``ZOE_KOKORO_IMPORT_BLOCK=0`` disables it for one run (debugging only).
"""
from __future__ import annotations

import os
import sys
from typing import MutableMapping

# Top-level packages only; submodule imports stop at their (blocked) parent.
BLOCKED: tuple[str, ...] = ("sklearn", "pandas", "pyarrow")


def install(modules: MutableMapping[str, object] | None = None,
            blocked: tuple[str, ...] = BLOCKED,
            environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Mark each *blocked* package unimportable. Returns the names it blocked.

    A package that is already imported is left alone — this runs from a ``.pth``
    at start-up, so that never happens in the service, and yanking a live module
    out from under its importers would be worse than keeping it.
    """
    modules = sys.modules if modules is None else modules
    environ = os.environ if environ is None else environ
    if environ.get("ZOE_KOKORO_IMPORT_BLOCK", "1") == "0":
        return []
    done = []
    for name in blocked:
        if name not in modules:
            modules[name] = None
            done.append(name)
    return done


# Only the INSTALLED copy acts on import (the .pth imports it by that name). The
# repo copy, imported by tests or tools as ``kokoro_import_block``, does nothing
# until install() is called with explicit arguments.
if __name__ == "zoe_kokoro_import_block":
    install()
