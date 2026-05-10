#!/usr/bin/env python3
"""stage_widget.py — Stage a new dashboard widget for preview.

Usage:
    python3 stage_widget.py --name "moon-phase" --description "Shows moon phase" --code "<html>...</html>"
    python3 stage_widget.py --spec-file widget_spec.json

Output (stdout, one per line):
    preview_url: /dist/_preview/<task_id>/widget.html
    task_id: <task_id>
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone

DIST_DIR = Path(__file__).resolve().parents[2] / "services" / "zoe-ui" / "dist"
PREVIEW_DIR = DIST_DIR / "_preview"
MANIFEST_PATH = PREVIEW_DIR / "manifest.json"


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text())
        except Exception:
            pass
    return {"widgets": {}}


def save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))


def stage_widget(name: str, description: str, code: str, task_id: str | None = None) -> tuple[str, str]:
    """Write widget preview files. Returns (preview_url, task_id)."""
    task_id = task_id or str(uuid.uuid4())[:8]
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower())
    preview_slug = f"{safe_name}-{task_id}"
    preview_path = PREVIEW_DIR / preview_slug
    preview_path.mkdir(parents=True, exist_ok=True)

    # Write the widget HTML
    widget_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name} — Preview</title>
<style>
  :root {{
    --zoe-bg: #0e0e1a;
    --zoe-accent: #6c63ff;
    --zoe-text: #e0e0e0;
  }}
  body {{
    margin: 0; padding: 16px;
    background: var(--zoe-bg);
    color: var(--zoe-text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }}
  .preview-badge {{
    position: fixed; top: 8px; right: 8px;
    background: var(--zoe-accent); color: #fff;
    font-size: 11px; padding: 3px 8px; border-radius: 4px;
    letter-spacing: 0.04em;
  }}
</style>
</head>
<body>
<div class="preview-badge">PREVIEW</div>
{code}
</body>
</html>
"""
    (preview_path / "widget.html").write_text(widget_html)

    # Write metadata
    meta = {
        "task_id": task_id,
        "name": name,
        "safe_name": safe_name,
        "description": description,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "preview",
        "preview_url": f"/_preview/{preview_slug}/widget.html",
    }
    (preview_path / "meta.json").write_text(json.dumps(meta, indent=2))

    # Update manifest
    manifest = load_manifest()
    manifest["widgets"][task_id] = meta
    save_manifest(manifest)

    return meta["preview_url"], task_id


def promote_widget(task_id: str) -> bool:
    """Promote a previewed widget to production (copies to widgets dir)."""
    manifest = load_manifest()
    if task_id not in manifest.get("widgets", {}):
        print(f"ERROR: task_id {task_id!r} not found in manifest", file=sys.stderr)
        return False

    meta = manifest["widgets"][task_id]
    safe_name = meta["safe_name"]
    preview_slug = next(
        (d.name for d in PREVIEW_DIR.iterdir() if d.is_dir() and task_id in d.name),
        None
    )
    if not preview_slug:
        print(f"ERROR: preview directory for {task_id} not found", file=sys.stderr)
        return False

    src = PREVIEW_DIR / preview_slug / "widget.html"
    dst_dir = DIST_DIR / "js" / "widgets" / "custom"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{safe_name}.html"
    dst.write_text(src.read_text())

    manifest["widgets"][task_id]["status"] = "promoted"
    manifest["widgets"][task_id]["production_path"] = str(dst.relative_to(DIST_DIR))
    save_manifest(manifest)
    print(f"Promoted to: {dst.relative_to(DIST_DIR)}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Stage a Zoe dashboard widget preview")
    parser.add_argument("--name", help="Widget name (e.g. moon-phase)")
    parser.add_argument("--description", help="One-line description")
    parser.add_argument("--code", help="Widget HTML+JS code")
    parser.add_argument("--spec-file", help="JSON spec file with name/description/code keys")
    parser.add_argument("--task-id", help="Reuse a specific task ID (optional)")
    parser.add_argument("--promote", metavar="TASK_ID", help="Promote a previewed widget to production")
    args = parser.parse_args()

    if args.promote:
        success = promote_widget(args.promote)
        sys.exit(0 if success else 1)

    if args.spec_file:
        spec = json.loads(Path(args.spec_file).read_text())
        name = spec["name"]
        description = spec.get("description", "")
        code = spec["code"]
    elif args.name and args.code:
        name = args.name
        description = args.description or ""
        code = args.code
    else:
        parser.error("Provide --name + --code, or --spec-file, or --promote TASK_ID")

    preview_url, task_id = stage_widget(name, description, code, args.task_id)
    print(f"preview_url: {preview_url}")
    print(f"task_id: {task_id}")


if __name__ == "__main__":
    main()
