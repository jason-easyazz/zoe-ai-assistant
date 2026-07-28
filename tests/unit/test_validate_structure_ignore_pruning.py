"""Regression tests for validate_structure's git-ignore pruning.

`get_all_project_files()` prunes git-ignored directories DURING the os.walk
instead of walking everything and filtering afterwards. The speedup is large
(>2min -> subsecond on the live checkout) but the dangerous failure mode is
silent: a prune that is even slightly broader than git's own ignore semantics
drops files out of the validated set without any error, and manifest orphan
detection then passes by simply not looking.

These tests pin the two constructions where a naive prune diverges from git:
a force-added tracked file inside an ignored directory, and a negation pattern
that re-includes a file beside ignored siblings. Both must still be validated.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

MODULE_PATH = Path(__file__).resolve().parents[2] / 'tools' / 'audit' / 'validate_structure.py'


def load_validator(project_root: Path):
    """Import validate_structure with PROJECT_ROOT pointed at a scratch repo."""
    spec = importlib.util.spec_from_file_location('validate_structure_under_test', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PROJECT_ROOT = project_root
    return module


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ['git', *args], cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A scratch git repo; no network, no reliance on the caller's git config."""
    git(tmp_path, 'init', '-q', '.')
    git(tmp_path, 'config', 'user.email', 'test@example.com')
    git(tmp_path, 'config', 'user.name', 'test')
    # -c so the test is unaffected by a global commit.gpgsign / hooks setup.
    (tmp_path / '.gitignore').write_text('')
    git(tmp_path, 'add', '.gitignore')
    git(tmp_path, '-c', 'commit.gpgsign=false', 'commit', '-qm', 'init')
    return tmp_path


def files_seen(repo: Path) -> set:
    return load_validator(repo).get_all_project_files()


def test_force_added_file_inside_ignored_dir_is_still_validated(repo: Path):
    """The exemption trap: `vault/` is ignored but holds an indexed file.

    A prune keyed on ".gitignore says vault/" (or a hardcoded runtime-dir list)
    skips the whole tree and the tracked file silently leaves the validated set.
    git does not collapse a directory that contains an indexed file, so the
    walker must still descend and report it.
    """
    (repo / '.gitignore').write_text('vault/\n')
    (repo / 'vault').mkdir()
    (repo / 'vault' / 'forced.txt').write_text('tracked despite the ignore rule\n')
    (repo / 'vault' / 'junk.bin').write_text('genuinely ignored\n')
    git(repo, 'add', '-f', 'vault/forced.txt')
    git(repo, '-c', 'commit.gpgsign=false', 'commit', '-qm', 'force-add')

    seen = files_seen(repo)
    assert 'vault/forced.txt' in seen, 'tracked file was silently exempted by the prune'
    assert 'vault/junk.bin' not in seen, 'genuinely ignored sibling must stay excluded'

    # Non-vacuity guard: the emptiness of the collapse listing is what the
    # implementation relies on, so assert git really does behave that way here.
    listing = git(repo, 'ls-files', '--others', '--ignored', '--exclude-standard', '--directory')
    assert 'vault/\n' not in listing, (
        'git collapsed a directory containing an indexed file -- the prune would '
        'be unsafe and this test would no longer be meaningful'
    )


def test_negation_restored_file_beside_ignored_siblings_is_validated(repo: Path):
    """`case/*` + `!case/keep.txt` genuinely re-includes keep.txt.

    Here git refuses to collapse `case/`, so the walker must enter it, drop the
    ignored siblings and keep the re-included file.
    """
    (repo / '.gitignore').write_text('case/*\n!case/keep.txt\n')
    (repo / 'case').mkdir()
    (repo / 'case' / 'keep.txt').write_text('re-included\n')
    (repo / 'case' / 'drop.log').write_text('ignored\n')

    seen = files_seen(repo)
    assert 'case/keep.txt' in seen, 'negation-restored file was silently exempted'
    assert 'case/drop.log' not in seen


def test_fully_ignored_nested_dir_is_excluded(repo: Path):
    """The actual speedup: a wholly-ignored tree contributes nothing."""
    (repo / '.gitignore').write_text('models/\n')
    (repo / 'models' / 'deep' / 'deeper').mkdir(parents=True)
    (repo / 'models' / 'a.bin').write_text('x\n')
    (repo / 'models' / 'deep' / 'b.bin').write_text('x\n')
    (repo / 'models' / 'deep' / 'deeper' / 'c.bin').write_text('x\n')
    (repo / 'kept.txt').write_text('x\n')

    seen = files_seen(repo)
    assert 'kept.txt' in seen
    assert not any(p.startswith('models/') for p in seen), 'ignored tree leaked into the set'


def test_env_files_skipped_but_example_kept(repo: Path):
    """.env handling predates this change and must survive it."""
    (repo / '.env').write_text('SECRET=x\n')
    (repo / '.env.local').write_text('SECRET=x\n')
    (repo / '.env.example').write_text('SECRET=\n')
    git(repo, 'add', '-f', '.env.example')
    git(repo, '-c', 'commit.gpgsign=false', 'commit', '-qm', 'env example')

    seen = files_seen(repo)
    assert '.env' not in seen
    assert '.env.local' not in seen
    assert '.env.example' in seen


def test_git_dir_never_walked(repo: Path):
    seen = files_seen(repo)
    assert not any(p.startswith('.git/') for p in seen)


def test_falls_back_to_post_walk_filter_when_git_unavailable(repo: Path, monkeypatch):
    """git failure must degrade to the old batched filter, not to a broken prune.

    An empty successful listing and a git failure are different states: the
    former means "nothing is ignored", the latter means "unknown". Only the
    latter may fall back.
    """
    (repo / '.gitignore').write_text('secret/\n')
    (repo / 'secret').mkdir()
    (repo / 'secret' / 'x.txt').write_text('x\n')
    (repo / 'plain.txt').write_text('x\n')

    module = load_validator(repo)
    monkeypatch.setattr(module, 'get_gitignored_paths', lambda: None)
    calls = []

    def fake_batch(candidates):
        calls.append(candidates)
        return {c for c in candidates if c.startswith('secret/')}

    monkeypatch.setattr(module, 'get_gitignored_files', fake_batch)

    seen = module.get_all_project_files()
    assert calls, 'fallback batched filter was not consulted after a git failure'
    assert 'plain.txt' in seen
    assert 'secret/x.txt' not in seen


def test_empty_ignore_listing_is_not_treated_as_failure(repo: Path, monkeypatch):
    """A repo with nothing ignored must NOT trigger the fallback path."""
    (repo / 'plain.txt').write_text('x\n')

    module = load_validator(repo)
    monkeypatch.setattr(module, 'get_gitignored_paths', lambda: set())

    def explode(candidates):
        raise AssertionError('fallback used for an empty-but-successful listing')

    monkeypatch.setattr(module, 'get_gitignored_files', explode)

    assert 'plain.txt' in module.get_all_project_files()
