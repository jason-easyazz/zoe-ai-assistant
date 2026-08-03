"""Operator-run DISCOVERY harness: drive one store picker, record what it called.

This is the instrument, not the product. It runs ONE CloakBrowser session per
retailer, performs that retailer's store-selection dance for Geraldton WA 6530,
and prints every JSON call the front end made — URL, method, status, size, the
request headers, and where a price or a store id appears in the body.

The output is read by a human, who then writes (or does not write) a `Recipe` in
`registry.py`. Nothing here writes a recipe automatically: a captured endpoint is
a hypothesis until someone has fetched it with plain httpx and compared the
number to what the page shows. That comparison is `verify.py`.

    python3 -m websearch.locations.capture --list
    python3 -m websearch.locations.capture --retailer bws
    python3 -m websearch.locations.capture --retailer bws --show-body 2

Always under a memory cap, one at a time::

    systemd-run --user --scope -p MemoryMax=1536M -- \
        python3 -m websearch.locations.capture --retailer bws

`StoreSession.preflight()` additionally refuses to launch under 380 MB MemFree
or over load1 3.0, so a forgotten cap is not the only line of defence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

if __package__ in (None, ""):  # allow `python3 capture.py` from the package dir
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from websearch.locations.session import Captured, SessionRefused, StoreSession  # noqa: E402

GERALDTON = {"suburb": "Geraldton", "postcode": "6530", "state": "WA"}


# --------------------------------------------------------------------- scripts
#
# One coroutine per retailer. Each performs the picker dance and returns a short
# note about what it observed. They are DELIBERATELY tolerant: a selector that
# has moved returns False and the script carries on, because the capture is
# still worth having even when the dance half-fails — the calls the page made
# while trying are exactly the evidence we came for.


async def script_bws(s: StoreSession) -> str:
    """BWS via `/storelocator` — the page whose ONLY job is store selection.

    The first pass at this drove the header control on the home page and ended
    up typing `6530` into the SITE SEARCH box (`/search?searchTerm=6530`,
    "No results for 6530"). That is worth recording rather than quietly fixing:
    an input that accepts your text and returns a plausible page is exactly the
    "refusal wearing a success's clothes" this spike keeps meeting, and the
    capture is what exposed it — the network log had no store call at all.
    """
    notes = []
    read = await s.goto("https://bws.com.au/storelocator")
    notes.append(f"locator: {read.chars} chars, title={read.title!r}")
    for sel in ("input[type='search']", "input[placeholder*='ostcode']",
                "input[placeholder*='uburb']", "input[placeholder*='ocation']", "input[type='text']"):
        if await s.fill(sel, "Geraldton", timeout_ms=4000):
            notes.append(f"typed Geraldton into {sel}")
            await s.press(sel, "Enter", timeout_ms=4000)
            break
    await s.wait(4000)
    read = await s.reread()
    notes.append(f"after search: {read.chars} chars; geraldton={read.has('geraldton')}")
    for sel in ("button:has-text('Set as my store')", "button:has-text('Select this store')",
                "button:has-text('Shop this store')", "button:has-text('Set as store')",
                "button:has-text('Select store')", "button:has-text('Choose')"):
        if await s.click(sel, timeout_ms=4000):
            notes.append(f"set store via {sel}")
            break
    await s.wait(3000)
    read = await s.goto("https://bws.com.au/product/38879/emu-export-lager-cans-375ml")
    notes.append(f"product: {read.chars} chars; geraldton={read.has('geraldton')}")
    return "; ".join(notes)


async def script_liquorland(s: StoreSession) -> str:
    notes = []
    read = await s.goto("https://www.liquorland.com.au/")
    notes.append(f"home: {read.chars} chars")
    for label in ("Set store", "Select store", "Find a store", "Choose a store", "store"):
        if await s.click_text(label, timeout_ms=4000):
            notes.append(f"opened picker via {label!r}")
            break
    await s.wait(1500)
    for sel in ("input[type='search']", "input[placeholder*='ostcode']", "input[placeholder*='uburb']", "input[type='text']"):
        if await s.fill(sel, GERALDTON["postcode"], timeout_ms=4000):
            notes.append(f"typed 6530 into {sel}")
            await s.press(sel, "Enter", timeout_ms=4000)
            break
    await s.wait(3500)
    read = await s.reread()
    notes.append(f"after search: {read.chars} chars; geraldton={read.has('geraldton')}")
    if await s.click_text("Geraldton", timeout_ms=5000):
        notes.append("clicked Geraldton")
    await s.wait(2500)
    read = await s.goto(
        "https://www.liquorland.com.au/beer-and-cider/emu-export-block-can-375ml_6517858"
    )
    notes.append(f"product: {read.chars} chars")
    return "; ".join(notes)


async def script_firstchoice(s: StoreSession) -> str:
    notes = []
    read = await s.goto("https://www.firstchoiceliquor.com.au/")
    notes.append(f"home: {read.chars} chars")
    for label in ("Set store", "Select store", "Find a store", "Choose a store"):
        if await s.click_text(label, timeout_ms=4000):
            notes.append(f"opened picker via {label!r}")
            break
    await s.wait(1500)
    for sel in ("input[type='search']", "input[placeholder*='ostcode']", "input[type='text']"):
        if await s.fill(sel, GERALDTON["postcode"], timeout_ms=4000):
            await s.press(sel, "Enter", timeout_ms=4000)
            notes.append(f"typed 6530 into {sel}")
            break
    await s.wait(3500)
    read = await s.reread()
    notes.append(f"after search: {read.chars} chars; geraldton={read.has('geraldton')}")
    if await s.click_text("Geraldton", timeout_ms=5000):
        notes.append("clicked Geraldton")
    await s.wait(2500)
    read = await s.goto(
        "https://www.firstchoiceliquor.com.au/beer-and-cider/emu-export-block-can-375ml_6517858"
    )
    notes.append(f"product: {read.chars} chars")
    return "; ".join(notes)


async def script_thirstycamel(s: StoreSession) -> str:
    notes = []
    read = await s.goto("https://www.thirstycamel.com.au/product/emu-export-can-block/c4911a11ea")
    notes.append(f"product cold: {read.chars} chars; has-price-prompt={read.has('to view in store')}")
    for label in ("Select a store", "Choose your store", "Set my store", "Find a store", "Select store"):
        if await s.click_text(label, timeout_ms=4000):
            notes.append(f"opened picker via {label!r}")
            break
    await s.wait(1500)
    for sel in ("input[type='search']", "input[placeholder*='ostcode']", "input[placeholder*='uburb']", "input[type='text']"):
        if await s.fill(sel, "Geraldton", timeout_ms=4000):
            await s.press(sel, "Enter", timeout_ms=4000)
            notes.append(f"typed Geraldton into {sel}")
            break
    await s.wait(3500)
    read = await s.reread()
    notes.append(f"after search: {read.chars} chars; geraldton={read.has('geraldton')}")
    if await s.click_text("Geraldton", timeout_ms=5000):
        notes.append("clicked Geraldton")
    await s.wait(3000)
    read = await s.reread()
    notes.append(f"after select: {read.chars} chars; has-$={'$' in read.text}")
    return "; ".join(notes)


async def script_bottlemart(s: StoreSession) -> str:
    notes = []
    read = await s.goto("https://bottlemart.com.au/")
    notes.append(f"home: {read.chars} chars")
    for label in ("MY STORE", "Your Store", "Set my store", "Find a store", "Select a store"):
        if await s.click_text(label, timeout_ms=4000):
            notes.append(f"opened picker via {label!r}")
            break
    await s.wait(1500)
    for sel in ("input[type='search']", "input[placeholder*='ostcode']", "input[placeholder*='uburb']", "input[type='text']"):
        if await s.fill(sel, "Geraldton", timeout_ms=4000):
            await s.press(sel, "Enter", timeout_ms=4000)
            notes.append(f"typed Geraldton into {sel}")
            break
    await s.wait(3500)
    read = await s.reread()
    notes.append(f"after search: {read.chars} chars; geraldton={read.has('geraldton')}")
    if await s.click_text("Geraldton", timeout_ms=5000):
        notes.append("clicked Geraldton")
    await s.wait(3000)
    read = await s.reread()
    notes.append(f"after select: {read.chars} chars")
    return "; ".join(notes)


async def script_danmurphys(s: StoreSession) -> str:
    notes = []
    read = await s.goto(
        "https://www.danmurphys.com.au/product/DM_38879/emu-export-30-block-cans-375ml"
    )
    notes.append(f"product cold: {read.chars} chars; has-$={'$' in read.text}")
    for label in ("Set your store", "Select a store", "Find a store", "Your store"):
        if await s.click_text(label, timeout_ms=4000):
            notes.append(f"opened picker via {label!r}")
            break
    await s.wait(1500)
    for sel in ("input[type='search']", "input[placeholder*='ostcode']", "input[type='text']"):
        if await s.fill(sel, GERALDTON["postcode"], timeout_ms=4000):
            await s.press(sel, "Enter", timeout_ms=4000)
            notes.append(f"typed 6530 into {sel}")
            break
    await s.wait(3500)
    read = await s.reread()
    notes.append(f"after search: {read.chars} chars; geraldton={read.has('geraldton')}")
    if await s.click_text("Geraldton", timeout_ms=5000):
        notes.append("clicked Geraldton")
    await s.wait(3000)
    read = await s.reread()
    notes.append(f"after select: {read.chars} chars; has-$={'$' in read.text}")
    return "; ".join(notes)


async def script_cellarbrations(s: StoreSession) -> str:
    """Rigters Geraldton — already served prices via CloakBrowser.

    The open question here is NOT 'can we read it' but 'is what we read
    store-accurate', so this script does no picker dance: it records what the
    site itself says about which store it is showing.
    """
    notes = []
    read = await s.goto("https://cellarbrations.rsgwa.com.au/")
    notes.append(f"home: {read.chars} chars; geraldton={read.has('geraldton')}")
    for token in ("rigters", "your store", "select store", "change store"):
        notes.append(f"{token}={read.has(token)}")
    return "; ".join(notes)


SCRIPTS = {
    "bws": script_bws,
    "liquorland": script_liquorland,
    "firstchoice": script_firstchoice,
    "thirstycamel": script_thirstycamel,
    "bottlemart": script_bottlemart,
    "danmurphys": script_danmurphys,
    "cellarbrations": script_cellarbrations,
}


# ---------------------------------------------------------------------- report


def interesting(call: Captured) -> bool:
    """Filter the noise. Analytics beacons are not evidence."""
    low = call.url.lower()
    noise = (
        "google-analytics",
        "googletagmanager",
        "doubleclick",
        "facebook",
        "hotjar",
        "newrelic",
        "sentry",
        "cloudfront.net/log",
        "/collect",
        "adobedtm",
        "demdex",
        "bam.nr-data",
        "dynatrace",
    )
    return not any(n in low for n in noise)


def report(session: StoreSession, *, show_body: int) -> None:
    calls = [c for c in session.calls if interesting(c)]
    js = [c for c in calls if c.is_json and c.body]
    print(f"\n=== {session.label}: {len(session.calls)} responses "
          f"({len(calls)} after noise filter, {len(js)} JSON with a body), "
          f"{session.page_loads} page loads")

    print("\n--- JSON calls")
    for c in js:
        print("  " + c.summary())

    priced = session.captured_with_price()
    print(f"\n--- JSON calls containing a price signal ({len(priced)})")
    for c in priced:
        print("  " + c.summary())
        toks = c.price_tokens()[:8]
        if toks:
            print("      $ tokens: " + ", ".join(toks))

    print("\n--- calls whose URL mentions store/location")
    for c in calls:
        if any(t in c.url.lower() for t in ("store", "location", "fulfil", "postcode", "suburb", "site")):
            print("  " + c.summary())

    for c in priced[:show_body]:
        print(f"\n--- BODY {c.url[:160]}")
        print((c.body or "")[:4000])


async def run(retailer: str, *, show_body: int, out: pathlib.Path | None) -> int:
    script = SCRIPTS[retailer]
    async with StoreSession(label=retailer) as s:
        try:
            note = await script(s)
        except SessionRefused:
            raise
        except Exception as exc:  # noqa: BLE001 - a half-run capture is still evidence
            note = f"SCRIPT RAISED {type(exc).__name__}: {exc}"
        print(f"\nscript notes: {note}")
        cookies = await s.cookies()
        storage = await s.local_storage()
        report(s, show_body=show_body)

        print(f"\n--- cookies ({len(cookies)})")
        for ck in cookies:
            val = str(ck.get("value", ""))
            print("  %-44s %-28s %s" % (ck.get("name"), ck.get("domain"), val[:70]))
        print(f"\n--- localStorage keys ({len(storage)})")
        for k, v in storage.items():
            print("  %-46s %s" % (k[:46], str(v)[:110]))

        for read in s.reads:
            print(f"\n--- PAGE {read.url[:120]} -> {read.final_url[:120]}")
            print(f"    {read.chars} chars, strategy={read.strategy}, {read.elapsed_s:.1f}s")
            print("    " + read.text[:700].replace("\n", " "))

        if out:
            payload = {
                "retailer": retailer,
                "note": note,
                "page_loads": s.page_loads,
                "calls": [
                    {
                        "url": c.url,
                        "method": c.method,
                        "status": c.status,
                        "content_type": c.content_type,
                        "request_headers": c.request_headers,
                        "body": c.body,
                    }
                    for c in s.calls
                    if interesting(c) and c.is_json and c.body
                ],
                "cookies": cookies,
                "local_storage": storage,
                "reads": [
                    {"url": r.url, "final_url": r.final_url, "chars": r.chars, "text": r.text}
                    for r in s.reads
                ],
            }
            out.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
            print(f"\nwrote {out} ({out.stat().st_size} bytes)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--retailer", choices=sorted(SCRIPTS), help="which picker dance to run")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show-body", type=int, default=1, help="print N priced JSON bodies")
    ap.add_argument("--out", type=pathlib.Path, help="write the raw capture as JSON")
    args = ap.parse_args(argv)

    if args.list or not args.retailer:
        print("retailers: " + ", ".join(sorted(SCRIPTS)))
        return 0
    try:
        return asyncio.run(run(args.retailer, show_body=args.show_body, out=args.out))
    except SessionRefused as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
