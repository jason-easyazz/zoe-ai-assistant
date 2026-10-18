"""CI-side driver: control run, then each mutation alone; print which tests go red."""
import os
import subprocess
import sys

FILES = ['tests/unit/test_cross_review_fallback.py', 'tests/unit/test_omnigent_claude_auth_mode.py']
env = {**os.environ, 'PYTHONPATH': os.getcwd()}


def run_tests():
    p = subprocess.run([sys.executable, '-m', 'pytest', *FILES, '-q', '-rf', '--no-header', '-p', 'no:cacheprovider',
                        '--override-ini=asyncio_mode=auto'], capture_output=True, text=True, env=env)
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith('FAILED')]
    tail = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else '(no output)'
    return p.returncode, lines, tail


def restore():
    subprocess.run(['git', 'checkout', '-q', '--', 'scripts', 'modules'], check=True)


names = subprocess.run([sys.executable, 'scripts/xr_mutate2.py', 'list'], capture_output=True, text=True, check=True).stdout.splitlines()
rc, fails, tail = run_tests()
print(f'CONTROL (unmutated): rc={rc} :: {tail}')
bad = 0
for n in names:
    restore()
    subprocess.run([sys.executable, 'scripts/xr_mutate2.py', n], check=True)
    rc, fails, tail = run_tests()
    verdict = 'RED (caught)' if rc != 0 else 'GREEN (NOT CAUGHT!)'
    print(f'MUTATION {n}: {verdict} :: {tail}')
    for f in fails:
        print('     ', f.split(' - ')[0].replace('FAILED tests/unit/', ''))
    bad += rc == 0
restore()
print(f'UNCAUGHT MUTATIONS: {bad}')
sys.exit(0)
