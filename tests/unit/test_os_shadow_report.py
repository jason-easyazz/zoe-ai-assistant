"""The week-of-shadow reader turns OS_SHADOW_LIST_ADD lines into the comparison table (scripts/maintenance/os_shadow_report.py)."""
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("os_shadow_report", ROOT / "scripts" / "maintenance" / "os_shadow_report.py")
rep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rep)


def line(result="match", kernel="verified", reason="-", hop=1.0, src="spoken", cls="shopping"):
    return (f"2030-01-01 10:00:00 INFO os_shadow: OS_SHADOW_LIST_ADD src={src} cls={cls} live=added kernel={kernel} "
            f"result={result} reason={reason} hop_ms={hop:.1f} q_ms=0.1")


def test_the_table_for_a_synthetic_week():
    lines = [line(hop=h) for h in (1, 2, 3, 4, 5, 6, 7, 8, 9, 40)]
    lines += [line("kernel_refused_live_did", "refused", "no_grant", 2.0, src="rest", cls="todo"),
              line("kernel_unavailable", "none", "circuit", 0.0), line("timeout", "none", "timeout", 500.0),
              line("member_not_in_roster", "none", "-", 0.0), "unrelated INFO line", "OS_SHADOW worker error=KeyError"]
    s = rep.summarize(rep.parse(lines))
    assert s["adds_logged"] == 14 and s["compared"] == 13 and s["match"] == 10 and s["match_rate"] == round(10 / 13, 4)
    assert s["mismatch"] == {"kernel_refused_live_did": 1, "kernel_unavailable": 1, "timeout": 1}
    assert s["refused_by_reason"] == {"no_grant": 1} and s["not_compared"] == {"member_not_in_roster": 1}
    assert s["kernel_unavailable"] == 1 and s["timeouts"] == 1 and s["hop_answered"] == 11
    assert s["hop_ms_p50"] == 5.0 and s["hop_ms_p95"] == 40.0       # the unavailable / timed-out adds are not in the hop
    assert s["by_src"] == {"spoken": 13, "rest": 1} and s["by_cls"] == {"shopping": 13, "todo": 1}
    text = rep.render(s)
    assert "match: 10 (76.9%)" in text and "kernel unavailable: 1" in text


def test_an_empty_week_does_not_divide_by_zero(tmp_path, capsys):
    f = tmp_path / "log"
    f.write_text("nothing here\n")
    assert rep.main([str(f)]) == 0 and "match: 0 (n/a)" in capsys.readouterr().out
    assert rep.main(["--json", str(f)]) == 0


def test_the_reader_never_echoes_a_line(tmp_path, capsys):
    f = tmp_path / "log"
    f.write_text(line() + " item=Quillon-tangerines\n")
    rep.main(["--json", str(f)])
    assert "Quillon" not in capsys.readouterr().out
    assert json.dumps(rep.summarize(rep.parse([line()])))
