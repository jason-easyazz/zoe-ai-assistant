"""A foreign key into a PARTITIONED parent must not be nulled when the parent's rows were dumped under its partitions."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

MAINT = Path(__file__).resolve().parents[2] / "scripts" / "maintenance"


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, MAINT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


plan = _load("pg_restore_plan")

SCHEMA = """
ALTER TABLE ONLY public.events ATTACH PARTITION public.events_2026 FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
ALTER TABLE ONLY public.events ATTACH PARTITION public.events_2027 FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
ALTER TABLE ONLY public.gone ATTACH PARTITION public.gone_a FOR VALUES IN (1);
ALTER TABLE ONLY public.notes
    ADD CONSTRAINT notes_event_fkey FOREIGN KEY (event_id) REFERENCES public.events(id);
ALTER TABLE ONLY public.notes
    ADD CONSTRAINT notes_gone_fkey FOREIGN KEY (gone_id) REFERENCES public.gone(id);
"""

# events / gone have no TABLE DATA of their own; rows live under partitions. gone_a's data was excluded.
TOC = [
    "1; 1259 1 TABLE public notes u", "2; 1259 2 TABLE public events u", "3; 1259 3 TABLE public events_2026 u",
    "4; 1259 4 TABLE public events_2027 u", "5; 1259 5 TABLE public gone u", "6; 1259 6 TABLE public gone_a u",
    "7; 0 0 TABLE DATA public notes u", "8; 0 0 TABLE DATA public events_2026 u",
    "9; 0 0 TABLE DATA public events_2027 u",
]


def test_fk_into_partitioned_parent_with_restored_partitions_is_kept():
    got = plan.plan_for(TOC, SCHEMA)
    assert {f["name"] for f in got} == {"notes_gone_fkey"}  # only the parent whose partitions were all excluded


def test_without_partition_structure_the_valid_link_would_be_nulled():
    # negative control: the pre-fix behaviour (no schema passed) plans both
    got = plan.excluded_fks(plan.parse_fks(SCHEMA), plan.excluded_tables(None, TOC))
    assert {f["name"] for f in got} == {"notes_event_fkey", "notes_gone_fkey"}


def test_manifest_listed_exclusions_are_also_corrected():
    manifest = {"tables": [], "data_excluded": ["public.events", "public.gone", "public.gone_a"]}
    got = plan.plan_for(TOC, SCHEMA, manifest)
    assert {f["name"] for f in got} == {"notes_gone_fkey"}
