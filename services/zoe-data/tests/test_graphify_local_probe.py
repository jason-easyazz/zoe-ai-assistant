import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "graphify_local_probe.py"
    spec = importlib.util.spec_from_file_location("graphify_local_probe_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_graphify_log_counts_failure_signals():
    module = _load_module()
    metrics = module.parse_graphify_log(
        "[graphify extract] found 601 code, 257 docs, 0 papers, 0 images\n"
        "[graphify] chunk of 8 exceeded context at depth 2; splitting in half and retrying\n"
        "[graphify] LLM returned invalid JSON, skipping chunk: Extra data\n"
        "[graphify] chunk of 8 truncated at depth 2\n"
    )

    assert metrics["code_files"] == 601
    assert metrics["doc_files"] == 257
    assert metrics["context_splits"] == 1
    assert metrics["invalid_json_chunks"] == 1
    assert metrics["truncated_chunks"] == 1


def test_classify_rejects_invalid_json_even_when_graph_exists():
    module = _load_module()
    result = module.classify_probe_result(
        exit_code=0,
        timed_out=False,
        log_text="[graphify] LLM returned invalid JSON, skipping chunk: Extra data",
        graph_json_exists=True,
        graph_json_bytes=128,
        graph_report_exists=False,
        cluster=False,
    )

    assert result["accepted"] is False
    assert "invalid_json_chunks" in result["blockers"]


def test_classify_accepts_clean_extract_without_cluster_report():
    module = _load_module()
    result = module.classify_probe_result(
        exit_code=0,
        timed_out=False,
        log_text="[graphify extract] found 1 code, 0 docs\n[graphify extract] wrote x/graph.json - 2 nodes, 1 edges",
        graph_json_exists=True,
        graph_json_bytes=256,
        graph_report_exists=False,
        cluster=False,
    )

    assert result["accepted"] is True
    assert result["blockers"] == []


def test_classify_requires_report_when_cluster_enabled():
    module = _load_module()
    result = module.classify_probe_result(
        exit_code=0,
        timed_out=False,
        log_text="[graphify extract] found 1 code, 0 docs",
        graph_json_exists=True,
        graph_json_bytes=256,
        graph_report_exists=False,
        cluster=True,
    )

    assert result["accepted"] is False
    assert "graph_report_missing" in result["blockers"]


def test_graphify_env_removes_cloud_keys(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    monkeypatch.setenv("GEMINI_API_KEY", "should-not-leak")
    config = module.GraphifyLocalProbeConfig(base_url="http://127.0.0.1:11434/v1", model="local-model")

    env = module.graphify_env(config)

    assert env["OLLAMA_BASE_URL"] == "http://127.0.0.1:11434/v1"
    assert env["OLLAMA_MODEL"] == "local-model"
    assert env["OLLAMA_API_KEY"] == "local"
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "GEMINI_API_KEY" not in env


def test_classify_reports_missing_graph_once():
    module = _load_module()
    result = module.classify_probe_result(
        exit_code=0,
        timed_out=False,
        log_text="",
        graph_json_exists=False,
        graph_json_bytes=0,
        graph_report_exists=False,
        cluster=False,
    )

    assert result["accepted"] is False
    assert result["blockers"].count("graph_json_missing") == 1
    assert "graph_json_empty" not in result["blockers"]


def test_run_command_timeout_kills_child_process_group(tmp_path):
    module = _load_module()
    token = f"zoe_graphify_probe_timeout_{os.getpid()}"
    child_code = "import sys,time; assert sys.argv[1]; time.sleep(30)"
    parent_code = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); "
        "time.sleep(30)"
    )
    command = [sys.executable, "-c", parent_code, child_code, token]

    returncode, timed_out, _output = module.run_command(command, cwd=tmp_path, env=os.environ.copy(), timeout_sec=1)
    time.sleep(0.5)
    lingering = subprocess.run(
        ["pgrep", "-af", token],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if lingering.stdout.strip():
        subprocess.run(["pkill", "-f", token], check=False)

    assert returncode == 124
    assert timed_out is True
    assert token not in lingering.stdout


def test_text_output_decodes_timeout_bytes():
    module = _load_module()

    assert module._text_output(b"hello") == "hello"
    assert module._text_output(None) == ""
    assert module._text_output("already text") == "already text"


def test_run_command_timeout_handles_killpg_oserror(monkeypatch, tmp_path):
    module = _load_module()

    class FakeProcess:
        pid = 12345
        returncode = None

        def __init__(self):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            raise module.subprocess.TimeoutExpired(["fake"], timeout=timeout, output=b"partial")

    fake = FakeProcess()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: fake)
    monkeypatch.setattr(module.os, "killpg", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")))

    returncode, timed_out, output = module.run_command(["fake"], cwd=tmp_path, env={}, timeout_sec=1)

    assert returncode == 124
    assert timed_out is True
    assert output == "partial"
    assert fake.calls == 3
