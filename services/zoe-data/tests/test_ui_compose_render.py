"""zoe-compose renderer — behavioral node-harness tests (escaping, actions)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "zoe-ui" / "dist" / "touch"


def _run_node(harness_path, *args):
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    proc = subprocess.run([node, str(harness_path), *map(str, args)],
                          check=True, capture_output=True, text=True)
    return json.loads(proc.stdout)


def test_compose_renderer_escapes_and_wires_actions(tmp_path):
    harness = tmp_path / "compose_harness.cjs"
    harness.write_text(
        """
const fs = require('fs');
const vm = require('vm');
const sandbox = { window: {} };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
const Z = sandbox.window.ZoeCompose;
const tree = { component: 'Stack', children: [
  { component: 'Text', text: 'Hello <script>alert(1)</script>', role: 'title' },
  { component: 'Row', children: [
    { component: 'Stat', value: '19\\u00b0', label: 'Geraldton <b>x</b>' },
    { component: 'Badge', text: 'Clear', tone: 'success' }
  ]},
  { component: 'ListRow', title: 'milk "quoted"', detail: 'dairy', variant: 'check', checked: true },
  { component: 'Progress', value: 62, label: 'Day' },
  { component: 'Image', src: 'https://evil.example/x.png', alt: 'nope' },
  { component: 'Image', src: '/touch/img/ok.png', alt: 'ok' },
  { component: 'ActionButton', action: { label: 'Show <em>week</em>', query: 'show my week', kind: 'primary' } },
  { component: 'TotallyFake', text: 'nope' }
]};
const html = Z.render(tree);
process.stdout.write(JSON.stringify({
  no_raw_script: !html.includes('<script>alert(1)</script>'),
  escaped_script: html.includes('&lt;script&gt;'),
  escaped_label_markup: !html.includes('<em>week</em>') && html.includes('&lt;em&gt;week&lt;/em&gt;'),
  action_attrs: /zx-action[^>]*data-sky-action=\\"query\\"[^>]*data-query=\\"show my week\\"/.test(html),
  primary_kind: html.includes('zx-primary'),
  foreign_image_dropped: !html.includes('evil.example'),
  same_origin_image_kept: html.includes('/touch/img/ok.png'),
  checked_row: html.includes('zx-checked'),
  progress_width: html.includes('width:62%'),
  unknown_inert: html.includes('zx-unknown') && html.includes('TotallyFake'),
  quotes_escaped: html.includes('milk &quot;quoted&quot;')
}));
""",
        encoding="utf-8",
    )
    checks = _run_node(harness, UI / "js" / "zoe-compose.js")
    assert all(checks.values()), f"compose renderer failed: {checks}"


def test_steps_and_compare_render_and_escape(tmp_path):
    harness = tmp_path / "sc.cjs"
    harness.write_text(
        """
const fs=require('fs'),vm=require('vm');const s={window:{}};vm.createContext(s);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),s);const Z=s.window.ZoeCompose;
const t={component:'Stack',children:[
 {component:'Steps',children:[{component:'Step',title:'Blot <b>x</b>',detail:'don\\'t rub'}]},
 {component:'Compare',children:[{component:'Option',label:'Drive',value:'4h',tone:'warm',status:'best <i>y</i>'}]}]};
const h=Z.render(t);
process.stdout.write(JSON.stringify({
  steps_ol:h.includes('zx-steps')&&h.includes('zx-step'),
  compare:h.includes('zx-compare')&&h.includes('zx-option'),
  option_tone:h.includes('zx-t-warm'),
  status_badge:h.includes('zx-option-status'),
  escaped:h.includes('&lt;b&gt;')&&!h.includes('<b>x</b>')&&!h.includes('<i>y</i>')
}));
""", encoding="utf-8")
    checks = _run_node(harness, UI / "js" / "zoe-compose.js")
    assert all(checks.values()), f"steps/compare render failed: {checks}"
