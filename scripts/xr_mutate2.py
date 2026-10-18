"""Apply ONE named break-the-fix mutation to the working tree (throwaway verify branch only)."""
import pathlib
import sys

root = pathlib.Path('.').resolve()
which = sys.argv[1]
SH = root / 'scripts/maintenance/cross_review.sh'
POLLY = root / 'scripts/maintenance/cross_review_polly.sh'
AM = root / 'scripts/maintenance/omnigent_claude_auth_mode.py'

MUT = {
    'M1 polly never used': (SH, '  local budget left out="$WORK/polly.out" rc vendor\n', '  local budget left out="$WORK/polly.out" rc vendor\n  return 1\n'),
    'M2 codex dropped': (SH, '    codex) CODEX_TRIED=1; try_codex && exit 0 ;;\n', '    codex) CODEX_TRIED=1 ;;\n'),
    'M3 pi loses --no-tools': (SH, '    --no-tools --no-session', '    --no-session'),
    'M4 all-fail exits 0': (SH, '\nexit 2\n', '\nexit 0\n'),
    'M5 anthropic attestation accepted': (SH, '  if printf \'%s\' "$vendor" | grep -q -i -E "$ANTHROPIC_RE"; then', '  if false; then'),
    'M6 report floor too high': (SH, 'MIN_REPORT_BYTES=40', 'MIN_REPORT_BYTES=100'),
    'M7 polly window not clamped': (SH, '  [ "$left" -lt "$budget" ] && budget=$left\n', ''),
    'M8 sentinel ignored': (SH, "CROSS-VENDOR-REVIEW-UNAVAILABLE' \"$out\"; then", "NEVER-MATCHES' \"$out\"; then"),
    'M9 polly brief loses the codex model rule': (POLLY, 'When dispatching the codex worker, OMIT args.model (use its default; ids such as gpt-5-codex are rejected on this account). ', ''),
    'M10 api mode without the key': (AM, '        if not env.get(KEY_VAR):\n', '        if False:\n'),
    'M11 pi guard removed': (SH, '[ -n "$CODEX_TRIED" ] || {', 'true || {'),
    'M12 partial verdict kept': (SH, "sed -i -E 's/^([*` ]*VERDICT:", "true 's/^([*` ]*VERDICT:"),
    'M13 no diff cap for pi': (SH, '  if [ "$total" -gt "$max" ]; then', '  if false; then'),
    'M14 codex success does not stop the chain': (SH, '    codex) CODEX_TRIED=1; try_codex && exit 0 ;;\n', '    codex) CODEX_TRIED=1; try_codex || true ;;\n'),
}
if which == 'list':
    print('\n'.join(MUT))
    sys.exit(0)
path, old, new = MUT[which]
text = path.read_text()
assert old in text, f'mutation target missing: {which}'
path.write_text(text.replace(old, new, 1))
