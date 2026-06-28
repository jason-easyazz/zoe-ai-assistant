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




def test_local_model_fit_evidence_finds_local_model_file(tmp_path):
    module = _load_module()
    model_dir = tmp_path / "models" / "gemma4-e4b-qat"
    model_dir.mkdir(parents=True)
    model_file = model_dir / "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
    model_file.write_bytes(b"local model")

    evidence = module.local_model_fit_evidence(
        "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf",
        "http://127.0.0.1:11434/v1",
        roots=(tmp_path / "models",),
    )

    assert evidence["base_url_localhost"] is True
    assert evidence["model_file"] == str(model_file)
    assert evidence["model_file_exists"] is True
    assert evidence["model_file_bytes"] == len(b"local model")
    assert evidence["offline_cloud_keys_scrubbed"] is True


def test_run_command_with_evidence_captures_duration_without_log_dump(tmp_path):
    module = _load_module()

    result = module.run_command_with_evidence(
        [sys.executable, "-c", "print('hello graphify')"],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout_sec=5,
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.output.strip() == "hello graphify"
    assert result.duration_ms >= 0
    assert result.child_max_rss_kb is None or result.child_max_rss_kb >= 0


def test_run_probe_status_includes_command_and_model_fit_evidence(tmp_path):
    module = _load_module()
    fake_graphify = tmp_path / "graphify"
    fake_graphify.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "out = Path('graphify-out')\n"
        "out.mkdir(exist_ok=True)\n"
        "(out / 'graph.json').write_text('{}', encoding='utf-8')\n"
        "print('[graphify extract] found 1 code, 0 docs')\n"
        "print('[graphify extract] wrote graphify-out/graph.json - 2 nodes, 1 edges')\n",
        encoding="utf-8",
    )
    fake_graphify.chmod(0o755)

    status = module.run_probe(
        module.GraphifyLocalProbeConfig(
            graphify_bin=fake_graphify,
            base_url="http://127.0.0.1:11434/v1",
            model="missing-test-model.gguf",
            mode="smoke",
            timeout_sec=5,
        )
    )

    assert status["accepted"] is True
    assert status["command_evidence"]["extract"]["exit_code"] == 0
    assert "output" not in status["command_evidence"]["extract"]
    assert status["command_evidence"]["extract"]["duration_ms"] >= 0
    assert status["model_fit"]["base_url_localhost"] is True
    assert status["model_fit"]["model_file_exists"] is False
    assert status["metrics"]["nodes"] == 2
    assert status["metrics"]["edges"] == 1


def test_default_model_roots_honors_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_MODEL_ROOT", str(tmp_path / "custom-models"))

    module = _load_module()

    assert module.DEFAULT_MODEL_ROOTS == (tmp_path / "custom-models",)


def test_child_max_rss_reports_kilobytes_on_darwin(monkeypatch):
    module = _load_module()

    class Usage:
        ru_maxrss = 2048 * 1024

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.resource, "getrusage", lambda _target: Usage())

    assert module._child_max_rss_kb() == 2048


def test_run_probe_error_status_keeps_command_evidence_schema(tmp_path, monkeypatch):
    module = _load_module()
    fake_graphify = tmp_path / "graphify"
    fake_graphify.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "out = Path('graphify-out')\n"
        "out.mkdir(exist_ok=True)\n"
        "(out / 'graph.json').write_text('{}', encoding='utf-8')\n"
        "print('[graphify extract] found 1 code, 0 docs')\n",
        encoding="utf-8",
    )
    fake_graphify.chmod(0o755)

    def boom(**_kwargs):
        raise RuntimeError("classification failed")

    monkeypatch.setattr(module, "classify_probe_result", boom)

    status = module.run_probe(
        module.GraphifyLocalProbeConfig(
            graphify_bin=fake_graphify,
            base_url="http://127.0.0.1:11434/v1",
            model="missing-test-model.gguf",
            mode="smoke",
            timeout_sec=5,
        )
    )

    assert status["accepted"] is False
    assert status["blockers"] == ["probe_error"]
    assert status["command_evidence"]["extract"]["exit_code"] == 0
    assert "output" not in status["command_evidence"]["extract"]
    assert "classification failed" in status["error"]

def test_validate_scope_path_rejects_unsafe_values():
    module = _load_module()

    assert module.validate_scope_path("services/zoe-data") == Path("services/zoe-data")

    for value in ("", "/home/zoe/assistant", "../secrets", "services/../.env", "."):
        try:
            module.validate_scope_path(value)
        except ValueError as exc:
            assert "invalid scoped Graphify path" in str(exc)
        else:
            raise AssertionError(f"unsafe scope path accepted: {value}")


def test_prepare_scope_workdir_copies_only_requested_paths(tmp_path):
    module = _load_module()
    root = tmp_path / "repo"
    source_dir = root / "services" / "zoe-data"
    source_dir.mkdir(parents=True)
    (source_dir / "memory_contract.py").write_text("def retain():\\n    return True\\n", encoding="utf-8")
    ignored = source_dir / "__pycache__"
    ignored.mkdir()
    (ignored / "memory_contract.pyc").write_text("cache", encoding="utf-8")
    (root / "outside.py").write_text("outside", encoding="utf-8")
    parent = tmp_path / "probe"
    config = module.GraphifyLocalProbeConfig(root=root, mode="scope", include_paths=("services/zoe-data",))

    workdir = module.prepare_scope_workdir(config, parent)

    assert (workdir / "services" / "zoe-data" / "memory_contract.py").exists()
    assert not (workdir / "services" / "zoe-data" / "__pycache__").exists()
    assert not (workdir / "outside.py").exists()
    assert (workdir / ".git").exists()


def test_prepare_scope_workdir_requires_include_paths(tmp_path):
    module = _load_module()
    root = tmp_path / "repo"
    root.mkdir()
    config = module.GraphifyLocalProbeConfig(root=root, mode="scope")

    try:
        module.prepare_scope_workdir(config, tmp_path / "probe")
    except ValueError as exc:
        assert "requires at least one --include-path" in str(exc)
    else:
        raise AssertionError("scope mode without include paths was accepted")
