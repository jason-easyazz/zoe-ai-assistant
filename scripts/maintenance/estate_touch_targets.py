#!/usr/bin/env python3
"""Estate finger-target sweep: every tappable control on every estate surface must offer a
48 px hit box (docs/VISION.md; services/zoe-ui/AGENTS.md). Measures the HIT box, not the
drawing: a control's rect is unioned with its absolutely-positioned ::before extension (the
estate's way of giving a small glyph a 48 px target). Runs in a real Chromium over CDP (the
Pi headless, see docs/knowledge/ui-deep-review-2026-10-04.md) against any origin.

  python3 scripts/maintenance/estate_touch_targets.py --base https://192.168.1.218:8443
Exit 0 = no control under the floor; 1 = offenders listed; 2 = instrument failure.
"""
import argparse, json, sys, time
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="https://192.168.1.218")
ap.add_argument("--cdp", default="http://127.0.0.1:9223")
ap.add_argument("--floor", type=int, default=48)
ap.add_argument("--panel-id", default="uitest-headless")
args = ap.parse_args()

SURFACES = ["day", "calendar", "list", "weather", "music", "rooms", "reminder", "person", "timer", "ask", "settings"]

SWEEP_JS = r"""(floor) => {
  const out = [];
  const seen = new Set();
  const isTappable = (el) => {
    const tag = el.tagName;
    if (['BUTTON','SELECT','A','TEXTAREA'].includes(tag)) return true;
    if (tag === 'INPUT' && !['hidden'].includes(el.type)) return true;
    if (el.getAttribute('role') === 'button') return true;
    if (typeof el.onclick === 'function') return true;
    return false;
  };
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.pointerEvents === 'none' || +cs.opacity === 0) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) return false;
    // covered by another layer (a hidden surface still in the DOM)?
    const el2 = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!el2 && (el2 === el || el.contains(el2) || el2.contains(el));
  };
  const px = (v) => { const n = parseFloat(v); return isNaN(n) ? 0 : n; };
  for (const el of document.querySelectorAll('*')) {
    if (!isTappable(el) || !visible(el)) continue;
    // a tappable INSIDE a tappable ancestor that fully contains it is the ancestor's problem
    let r = el.getBoundingClientRect(); let w = r.width, h = r.height;
    const b = getComputedStyle(el, '::before');
    if (b.content && b.content !== 'none' && b.position === 'absolute') {
      // the pseudo-element extends the element's own hit box
      const top = px(b.top), left = px(b.left), right = px(b.right), bottom = px(b.bottom);
      const bw = px(b.width), bh = px(b.height);
      w = Math.max(w, bw || (r.width - left - right)); h = Math.max(h, bh || (r.height - top - bottom));
    }
    if (Math.min(w, h) + 0.5 >= floor) continue;
    const id = el.id ? '#' + el.id : '';
    const cls = (el.className && typeof el.className === 'string') ? '.' + el.className.trim().split(/\s+/).join('.') : '';
    const key = el.tagName + id + cls + '|' + Math.round(w) + 'x' + Math.round(h);
    if (seen.has(key)) continue; seen.add(key);
    out.push({ el: el.tagName + id + cls, text: (el.textContent || el.value || '').trim().slice(0, 24), w: Math.round(w), h: Math.round(h) });
  }
  return out;
}"""

def main():
    offenders = {}
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(args.cdp)
        ctx = b.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 720})
        ctx.add_init_script(f"try{{localStorage.setItem('zoe_kiosk','1');localStorage.setItem('zoe_panel_id','{args.panel_id}');}}catch(e){{}}")
        pg = ctx.new_page()
        pg.goto(f"{args.base}/touch/home.html", wait_until="load", timeout=30000); time.sleep(6)
        offenders["home"] = pg.evaluate(SWEEP_JS, args.floor)
        for s in SURFACES:
            try:
                pg.click("#apps"); time.sleep(0.6)
                if not pg.query_selector(f'.ltile[data-id="{s}"]'):
                    offenders[s] = [{"el": "(tile missing)", "text": "", "w": 0, "h": 0}]; continue
                pg.click(f'.ltile[data-id="{s}"]'); time.sleep(1.8)
                if s == "settings":
                    pg.evaluate("() => document.querySelectorAll('.setsec:not(.open) .sh').forEach(h => h.click())"); time.sleep(0.8)
                if s == "reminder":
                    # open the date/time picker if the surface offers it
                    pg.evaluate("() => { const b = Array.from(document.querySelectorAll('button')).find(x => /when|date|time/i.test(x.textContent||'')); if (b) b.click(); }"); time.sleep(0.8)
                offenders[s] = pg.evaluate(SWEEP_JS, args.floor)
            except Exception as e:  # instrument, not product
                offenders[s] = [{"el": "(sweep error)", "text": str(e)[:60], "w": 0, "h": 0}]
        ctx.close(); b.close()
    total = 0
    for s, items in offenders.items():
        if not items: print(f"  ok   {s}: every tappable control ≥ {args.floor} px"); continue
        total += len(items); print(f"  FAIL {s}:")
        for it in items: print(f"         {it['w']}x{it['h']}  {it['el']}  {it['text']!r}")
    print("RESULT:", "PASS" if total == 0 else f"FAIL ({total} controls under {args.floor} px)")
    return 0 if total == 0 else 1

if __name__ == "__main__":
    try: sys.exit(main())
    except Exception as e:
        print("instrument failure:", e); sys.exit(2)
