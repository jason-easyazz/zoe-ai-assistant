"""The migration chain has exactly ONE head, checked offline (no database, no Alembic import).

Two pull requests that each chain a migration onto the same parent merge cleanly as text and leave the chain with two
heads; ``alembic upgrade head`` then refuses "head" as ambiguous. CI catches that on its PostgreSQL step, but only with
a database and only on an updated branch. This walks ``services/zoe-data/alembic/versions`` and reads each file's
``revision`` / ``down_revision`` assignments with ``ast``, so a unit that has no GitHub can run it too.
"""
import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

VERSIONS = Path(__file__).resolve().parents[2] / "services" / "zoe-data" / "alembic" / "versions"


def _ids(source: str) -> tuple[str | None, tuple[str, ...]]:
    """``(revision, down_revisions)`` from one migration's source. ``down_revision`` may be None, a string or a tuple."""
    rev, downs = None, ()
    for node in ast.parse(source).body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, value = node.target.id, node.value
        if target == "revision":
            rev = ast.literal_eval(value)
        elif target == "down_revision":
            got = ast.literal_eval(value)
            downs = () if got is None else ((got,) if isinstance(got, str) else tuple(got))
    return rev, downs


def heads(sources: dict[str, str]) -> list[str]:
    """Revisions that no migration names as its parent, sorted. A healthy chain has exactly one."""
    revisions, parents = {}, set()
    for name, src in sources.items():
        rev, downs = _ids(src)
        assert rev, f"{name}: no revision id"
        assert rev not in revisions, f"{name} and {revisions[rev]} share revision {rev!r}"
        revisions[rev] = name
        parents.update(downs)
    missing = sorted(parents - set(revisions))
    assert not missing, f"down_revision names a revision no file declares: {missing}"
    return sorted(set(revisions) - parents)


def _real() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(VERSIONS.glob("*.py"))}


def test_the_migration_chain_has_exactly_one_head():
    sources = _real()
    assert len(sources) > 10, "the versions directory was not found"
    got = heads(sources)
    assert len(got) == 1, f"the migration chain has {len(got)} heads {got}: re-chain the later migration onto the earlier one"


def test_a_second_migration_on_the_same_parent_is_caught():
    """Negative control: the exact shape of the merge clash, a synthetic sibling chained onto the current head's parent."""
    sources = _real()
    (head,) = heads(sources)
    parent = _ids(sources[next(n for n, s in sources.items() if _ids(s)[0] == head)])[1]
    assert parent, "the head has no parent to share"
    sources["9999_synthetic_sibling.py"] = f'revision = "9999_sibling"\ndown_revision = "{parent[0]}"\n'
    assert heads(sources) == sorted([head, "9999_sibling"])


def test_one_declared_root_and_no_unknown_parent():
    sources = _real()
    roots = [n for n, s in sources.items() if not _ids(s)[1]]
    assert len(roots) == 1, f"expected one root migration, found {roots}"
    with pytest.raises(AssertionError, match="no file declares"):
        heads({"x.py": 'revision = "b"\ndown_revision = "a_missing"\n'})
