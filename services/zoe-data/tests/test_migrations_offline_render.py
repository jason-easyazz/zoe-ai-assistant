"""Offline rendering guard — `alembic upgrade head --sql` must work end-to-end.

Under `--sql` (offline mode) the bind is a MockConnection that cannot return
results, so any migration that inspects the live DB (`sa.inspect(bind)`,
`exec_driver_sql(...).scalar()`) aborts the render instead of emitting SQL.
0027 and 0028 route their inspection through `context.is_offline_mode()` and
emit server-side guards (ADD/DROP COLUMN IF EXISTS, plpgsql DO blocks); these
tests render the REAL migration chain offline so a future migration that
reintroduces live-bind inspection fails here, not in an operator's terminal
(Codex, #1583). No database is touched — offline mode only uses the URL to
pick the dialect.
"""
import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
_DUMMY_URL = "postgresql+psycopg2://u:p@localhost/db"


def _render(monkeypatch, fn, *args) -> str:
    # env.py prefers POSTGRES_URL over the config URL — pin the dummy so a
    # developer's live-DB env var cannot change what dialect gets rendered.
    monkeypatch.setenv("POSTGRES_URL", _DUMMY_URL)
    buf = io.StringIO()
    cfg = Config(output_buffer=buf, stdout=buf)
    cfg.set_main_option("script_location", str(SVC / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _DUMMY_URL)
    fn(cfg, *args, sql=True)
    return buf.getvalue()


def test_upgrade_chain_renders_offline(monkeypatch):
    sql = _render(monkeypatch, command.upgrade, "head")
    # The chain reached its end: 0028 is the last step and its DROP made it out.
    assert "Running upgrade 0027 -> 0028" in sql
    assert "DROP TABLE IF EXISTS panel_presence_events" in sql
    # 0027's column guards rendered server-side, not as a live inspection.
    assert "ADD COLUMN IF NOT EXISTS autonomy_class" in sql
    # Existence lookups must resolve via search_path, never assume 'public'.
    assert "to_regclass('public." not in sql


def test_downgrade_steps_render_offline(monkeypatch):
    # head:0026 covers both live-DB-inspecting downgrades (0028 and 0027).
    sql = _render(monkeypatch, command.downgrade, "head:0026")
    assert "Running downgrade 0028 -> 0027" in sql
    assert "Running downgrade 0027 -> 0026" in sql
    # 0028's conditional restore became a server-side DO block. Unqualified
    # to_regclass on purpose — the lookup must resolve via search_path like
    # the DROP/INSERT beside it, not assume 'public' (Codex P1, #1583).
    assert "to_regclass('panel_presence_events_backup_0028')" in sql
    assert "to_regclass('public." not in sql
    assert "DROP COLUMN IF EXISTS autonomy_class" in sql
