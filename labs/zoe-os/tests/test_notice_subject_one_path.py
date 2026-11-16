"""Structural: no module under zoe_os/ reads a FORGET notice's subject itself. The one reader
is ``provenance.subject_of`` (behind ``provenance.erased``); the spine only WRITES it when it
builds a notice. A handler that reads it directly can mistake an anonymous whole-person
notice for a notice about nobody.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent / 'zoe_os'
ALLOWED = {'provenance.py'}
KEYS = {'subject', 'subject_hash'}


def direct_reads(source: str) -> list[int]:
    """Lines that read 'subject'/'subject_hash' out of a payload (or a name bound to one)."""
    tree = ast.parse(source)
    aliases = {t.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
               and isinstance(node.value, ast.Attribute) and node.value.attr == 'payload'
               for t in node.targets if isinstance(t, ast.Name)}

    def payloadish(expr) -> bool:
        return 'payload' in ast.unparse(expr) or (isinstance(expr, ast.Name) and expr.id in aliases)

    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and \
                isinstance(node.slice, ast.Constant) and node.slice.value in KEYS and payloadish(node.value):
            hits.append(node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' \
                and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in KEYS \
                and payloadish(node.func.value):
            hits.append(node.lineno)
    return hits


def test_no_module_reads_a_notice_subject_directly():
    found = {str(path.relative_to(ROOT)): lines for path in sorted(ROOT.rglob('*.py'))
             if path.name not in ALLOWED and (lines := direct_reads(path.read_text()))}
    assert found == {}


def test_control_the_scanner_catches_each_shape():
    for planted in ("x = ev.payload.get('subject')", "x = notice.payload['subject_hash']",
                    "p = ev.payload\nx = p.get('subject')"):
        assert direct_reads(planted), planted
    assert direct_reads("identity['subject'] = s") == []          # a write, as the spine builds a notice
