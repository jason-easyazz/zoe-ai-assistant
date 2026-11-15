"""[code] In-memory JSON mode around canonical replay, never a transcript file.

measure_voice.py delegates to this same replay and sums the same three timings.
Run only as a ReplayBackend child; one pinned clip per fresh process/session.
"""
import builtins
import asyncio
import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import runpy
import sys


def main():
    request = json.load(sys.stdin)
    service = Path(request['service_dir']).resolve()
    source = request.get('env_source')
    for source in filter(None, [source, str(service / '.env') if (service / '.env').exists() else None]):
        # Same setdefault precedence as replay_samples._load_env: CHILD overrides win.
        for line in Path(source).read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                k, v = line.strip().split('=', 1)
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))
    os.environ.pop('ZOE_REPLAY_FAULT', None)
    os.environ['ZOE_TRAFFIC_CLASS'] = 'probe'
    clip = Path(request['clip'])
    os.environ['ZOE_VOICE_SAMPLE_DIR'] = str(clip.parent)
    sys.dont_write_bytecode = True
    saved_open = builtins.open
    saved_io_open = io.open
    output = io.StringIO()
    manifest = io.StringIO(clip.name + '\n')
    class MemoryFile:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): self.stream.seek(0); return self.stream
        def __exit__(self, *exc): return False
    def memory_open(file, mode='r', *a, **kw):
        if str(file) == '__speed_loop_output__': return MemoryFile(output)
        if str(file) == '__speed_loop_manifest__': return MemoryFile(manifest)
        # Refuse python-level file writes by imported modules (including log handlers).
        if any(c in mode for c in 'wax+'):
            raise PermissionError('lab_write_refused')
        return saved_open(file, mode, *a, **kw)
    sys.path.insert(0, str(service))
    sys.argv = ['replay_samples.py', '--brain', '--user', 'probe-speed-loop',
                '--stt', 'inprocess', '--files-from', '__speed_loop_manifest__',
                '--json', '__speed_loop_output__']
    # Never expose replay prints, stderr, warning messages, filenames or exception details.
    with saved_open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        builtins.open = memory_open
        io.open = memory_open
        logging.disable(logging.CRITICAL)
        try:
            # STT stays local: remote STT emits on the watched service logger and
            # cannot be distinguished from household activity using prefixes only.
            # Prime only the real STT callable before the harness starts its timer.
            namespace = runpy.run_path(str(service / 'tests/replay_samples.py'), run_name='_speed_loop_replay')
            async def invoke():
                import routers.voice_tts as vt
                await vt._transcribe_audio_impl(str(clip))
                return await namespace['_run'](argparse.Namespace(
                    brain=True, user='probe-speed-loop', stt='inprocess', execute=False,
                    files_from='__speed_loop_manifest__', json='__speed_loop_output__',
                    since=None, last=None, since_mtime=None, base_url=None))
            if asyncio.run(invoke()) != 0:
                raise ValueError('replay_failed')
        finally:
            builtins.open = saved_open
            io.open = saved_io_open
    report = json.loads(output.getvalue())
    if len(report['rows']) != 1 or report.get('memory_recall') != 'ok' or report.get('fault'):
        raise ValueError('invalid_replay')
    r = report['rows'][0]
    timings = {k: r.get(k, 0) for k in ('stt_ms', 'resolve_ms', 'brain_ms')}
    # Output allowlist; no text even on the child's stdout pipe.
    row = dict(turn=request['turn'], **timings, e2e_ms=sum(timings.values()),
               memory_recall='ok', fault=None, verdict=r['verdict'],
               route=r.get('route'), heard_hash=hashlib.sha256(r.get('transcript', '').encode()).hexdigest())
    print(json.dumps(row, allow_nan=False))

if __name__ == '__main__':
    try:
        main()
    except BaseException:
        # No traceback: upstream exceptions can contain a transcript.
        sys.exit(2)
