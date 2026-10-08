"""Lab-only import trim for the bake-off's Hindsight server, chained AFTER the egress audit hook.

Hindsight 0.10.2 imports whole subsystems at start-up that a loopback, MCP-off, OpenAI-provider-only server never uses: the MCP stack (``fastmcp`` + ``mcp``, ~90 + 108
modules, ~24 MB of resident memory) and the Gemini provider (``google.genai``, ~9 MB). ``ZMB_LEAN_STUBS=fastmcp,mcp,google.genai`` replaces the named packages with inert stubs
BEFORE hindsight_api is imported, so the interpreter never loads them. The stubs raise nothing and do nothing: a feature that actually needed one of these packages would misbehave
silently, so this is OFF unless the environment names the packages, the functional checks of the lab (retain / recall / delete / health) are the proof it is safe, and it is a
lab measurement, not a recommendation to patch a production dependency (an upgrade of the engine can start using a stubbed package).

It first loads the egress audit hook (``../egress_audit/sitecustomize.py``) because only one ``sitecustomize`` is found on ``sys.path``: a lean directory in front of the audit
directory would otherwise switch the G0 egress instrument off.
"""
import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_AUDIT = os.path.join(os.path.dirname(_HERE), "egress_audit", "sitecustomize.py")
if os.path.isfile(_AUDIT):
    _spec = importlib.util.spec_from_file_location("zmb_egress_audit_sitecustomize", _AUDIT)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)


class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _make(name)


class _Base(metaclass=_Meta):
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return self

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _make(name)


def _make(name):
    return _Meta(name, (_Base,), {})


class _StubModule(types.ModuleType):
    __path__ = []                                 # a package: any dotted child resolves to another stub

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _make(name)


class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, names):
        self.names = tuple(names)

    def find_spec(self, fullname, path=None, target=None):
        for n in self.names:
            if fullname == n or fullname.startswith(n + "."):
                return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        return _StubModule(spec.name)

    def exec_module(self, module):
        return None


_names = [n.strip() for n in os.environ.get("ZMB_LEAN_STUBS", "").split(",") if n.strip()]
if _names:
    sys.meta_path.insert(0, _StubFinder(_names))
    sys.stderr.write("zmb lean_imports: stubbed " + ", ".join(_names) + "\n")
