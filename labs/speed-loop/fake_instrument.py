"""[lab] Offline instrument with real child env semantics; no service imports."""
import json
import os
import sys
import time
import subprocess
from pathlib import Path

r = json.load(sys.stdin)
if os.environ.get('SPEED_LOOP_FAKE_SLEEP'):
    if os.environ.get('SPEED_LOOP_FAKE_PID_FILE'):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        Path(os.environ['SPEED_LOOP_FAKE_PID_FILE']).write_text(str(child.pid))
    time.sleep(60)
turn = r['turn']
effect = 30 if os.environ.get('ZOE_FLUE_PREPOST_CONCURRENT') == '1' else 0
if os.environ.get('ZOE_BRAIN_VOLATILE_AFTER_USER') == '1': effect -= 30
# Arbitrary turn-to-turn spread, perfectly paired; no fabricated real noise claim.
brain = 500 + turn * 11 - effect
print(json.dumps(dict(turn=turn, stt_ms=100, resolve_ms=20, brain_ms=brain,
    e2e_ms=120+brain, verdict='OK', route={'lane':'brain'}, memory_recall='ok',
    heard_hash='ab'*32, transcript='PLANTED_SYNTHETIC_PRIVATE_TEXT',
    reply='PLANTED_SYNTHETIC_PRIVATE_TEXT')))
