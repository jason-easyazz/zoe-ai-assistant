"""Settings > Bluetooth devices: the broker between the household's UI and each panel's Bluetooth agent.

Flag-dark: ``ZOE_BLUETOOTH_SETTINGS`` (default OFF). Off = ``routers/bluetooth.py`` is not mounted (404) and nothing here runs.
Design + operator steps: ``docs/knowledge/bluetooth-settings.md``; the panel side is ``services/panel-bluetooth``.

PULL, not proxy. A panel's agent opens NO port. It long-polls ``/api/bluetooth/agent/poll`` with its device token, and zoe-data
queues commands for it. So "proxy to a chosen panel's agent" is a per-panel command queue here, the panel is authenticated by the
device token it already has (zoe-data stores only token HASHES, so it could not authenticate a call *to* the panel anyway), and
nothing about a panel's address is needed.

State is in memory on purpose: it is a live control surface (jobs live minutes), not a record. The durable record is the AUDIT LOG.

PRIVACY. A device's BLE address never reaches zoe-data: the agent speaks in ``device_id`` (16 hex of a hash). Everything the agent
sends is bounded and string-truncated (``clean``) before it is stored or shown - a panel is trusted to be a panel, not to be a
well-formed JSON author.

AUDIT. Every connect, disconnect, forget and firmware update (request AND outcome) is appended to a JSONL file; a request whose
audit line cannot be written is REFUSED (fail closed), because "who updated the pendant" must never be unknowable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from typed_env import env_bool, env_str

logger = logging.getLogger(__name__)

ONLINE_S = 45.0                 # a panel that has not polled in this long is shown as offline
FORGET_PANEL_S = 600.0          # ...and dropped from the list after this long
QUEUED_TTL_S = 60.0             # a queued command the panel never picked up fails with a plain sentence
RUNNING_TTL_S = 25 * 60.0       # a running job that stops reporting (a long update has progress every few seconds)
MAX_JOBS = 60
MAX_WAIT_S = 25.0
MAX_PAYLOAD_CHARS = 24_000

DEVICE_ID_RX = re.compile(r"^[0-9a-f]{16}$")
PANEL_ID_RX = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
#: operations the UI may request, and whether each is audited
USER_OPS = {"scan": False, "connect": True, "disconnect": True, "forget": True, "info": False, "status": False,
            "update_check": False, "update_start": True, "update_cancel": True, "omi_bond": True}
DEVICE_OPS = frozenset(USER_OPS) - {"scan", "status"}
TERMINAL = ("succeeded", "failed", "cancelled")


#: the only params each op forwards to the panel (anything else the browser sends is dropped)
FORWARDED_PARAMS = {"update_start": ("confirm", "ack_no_undo"), "omi_bond": ("confirm", "ack_wipe", "replace", "force")}


def _forwarded_params(op: str, params: dict) -> dict:
    return {k: params[k] for k in FORWARDED_PARAMS.get(op, ()) if k in params}


def enabled() -> bool:
    """``ZOE_BLUETOOTH_SETTINGS`` - default OFF (per-call read)."""
    return env_bool("ZOE_BLUETOOTH_SETTINGS", default=False)


def audit_path() -> Path:
    raw = env_str("ZOE_BLUETOOTH_AUDIT_PATH", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".zoe" / "bluetooth-audit.jsonl"


class BrokerError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def clean(obj: Any, depth: int = 0) -> Any:
    """Bound anything that came from a panel: depth, string length, list length. Keys are kept short and stringy."""
    if depth > 6:
        return None
    if isinstance(obj, str):
        return obj[:300]
    if isinstance(obj, (bool, int, float)) or obj is None:
        return obj
    if isinstance(obj, (list, tuple)):
        return [clean(x, depth + 1) for x in list(obj)[:60]]
    if isinstance(obj, dict):
        return {str(k)[:40]: clean(v, depth + 1) for k, v in list(obj.items())[:40]}
    return None


def audit(event: dict) -> bool:
    """Append one JSON line (0600). Returns False if it could not be written. Never contains addresses, tokens or free text."""
    rec = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **event}
    try:
        p = audit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        return True
    except OSError as exc:
        logger.error("bluetooth audit log not writable: %s", exc)
        return False


@dataclass
class Job:
    id: str
    panel_id: str
    op: str
    device_id: Optional[str]
    actor: dict
    state: str = "queued"           # queued | sent | running | succeeded | failed | cancelled
    progress: dict = field(default_factory=dict)
    result: Optional[dict] = None
    error: Optional[dict] = None
    created: float = 0.0
    updated: float = 0.0
    params: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {"job_id": self.id, "panel_id": self.panel_id, "op": self.op, "device_id": self.device_id, "state": self.state,
                "progress": self.progress, "result": self.result, "error": self.error, "age_s": int(time.monotonic() - self.created)}


@dataclass
class PanelState:
    panel_id: str
    last_seen: float = 0.0
    snapshot: dict = field(default_factory=dict)
    scan: Optional[dict] = None
    queue: deque = field(default_factory=deque)
    event: asyncio.Event = field(default_factory=asyncio.Event)

    def online(self, now: float) -> bool:
        return now - self.last_seen <= ONLINE_S


class Broker:
    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.panels: dict[str, PanelState] = {}
        self.jobs: dict[str, Job] = {}

    # ---------------------------------------------------------------- agent side ----------------------------
    def heartbeat(self, panel_id: str, snapshot: Any) -> PanelState:
        st = self.panels.get(panel_id)
        if st is None:
            st = self.panels[panel_id] = PanelState(panel_id)
        st.last_seen = self.clock()
        snap = clean(snapshot) if isinstance(snapshot, dict) else {}
        st.snapshot = snap
        return st

    async def take_commands(self, panel_id: str, wait_s: float) -> list:
        st = self.panels[panel_id]
        wait_s = max(0.0, min(MAX_WAIT_S, wait_s))
        if not st.queue and wait_s > 0:
            st.event.clear()
            try:
                await asyncio.wait_for(st.event.wait(), wait_s)
            except asyncio.TimeoutError:
                pass
        st.last_seen = self.clock()
        out = []
        while st.queue:
            job: Job = st.queue.popleft()
            if job.state != "queued":
                continue
            job.state, job.updated = "sent", self.clock()
            out.append({"job_id": job.id, "op": job.op, "device_id": job.device_id, "params": job.params})
        return out

    def report(self, panel_id: str, payload: Any) -> Optional[Job]:
        if not isinstance(payload, dict):
            raise BrokerError(400, "bad report")
        job = self.jobs.get(str(payload.get("job_id", "")))
        if job is None or job.panel_id != panel_id:           # a panel may only report on ITS OWN jobs
            raise BrokerError(404, "unknown job")
        if job.state in TERMINAL:
            return job
        state = payload.get("state")
        if state not in ("running", "succeeded", "failed", "cancelled"):
            raise BrokerError(400, "bad state")
        job.updated = self.clock()
        prog = payload.get("progress")
        if isinstance(prog, dict):
            job.progress = clean(prog)
        if state == "running":
            job.state = "running"
            return job
        job.state = state
        if state == "succeeded":
            job.result = clean(payload.get("result")) if isinstance(payload.get("result"), dict) else {}
        else:
            err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            job.error = clean({"code": err.get("code", "internal"), "message": err.get("message", "The panel reported a failure."),
                               "detail": err.get("detail")})
        st = self.panels.get(panel_id)
        if st is not None and job.op == "scan" and state == "succeeded":
            st.scan = {"at": time.time(), **(job.result or {})}
        if USER_OPS.get(job.op):
            audit({"phase": "finished", "action": job.op, "panel_id": panel_id, "device_id": job.device_id, "job_id": job.id,
                   "actor": job.actor.get("user_id"), "outcome": state, "error_code": (job.error or {}).get("code")})
        return job

    # ---------------------------------------------------------------- UI side -------------------------------
    def _sweep(self) -> None:
        now = self.clock()
        for pid in [p for p, s in self.panels.items() if now - s.last_seen > FORGET_PANEL_S]:
            del self.panels[pid]
        for job in self.jobs.values():
            if job.state in ("queued", "sent") and now - job.created > QUEUED_TTL_S:
                self._time_out(job, "The panel did not pick that up. Is it on and online?")
            elif job.state == "running" and now - job.updated > RUNNING_TTL_S:
                self._time_out(job, "The panel stopped reporting on this task.")
        if len(self.jobs) > MAX_JOBS:
            for jid in sorted(self.jobs, key=lambda j: self.jobs[j].created)[: len(self.jobs) - MAX_JOBS]:
                if self.jobs[jid].state in TERMINAL:
                    del self.jobs[jid]

    def _time_out(self, job: Job, message: str) -> None:
        job.state, job.error, job.updated = "failed", {"code": "panel_unreachable", "message": message}, self.clock()
        if USER_OPS.get(job.op):
            audit({"phase": "finished", "action": job.op, "panel_id": job.panel_id, "device_id": job.device_id, "job_id": job.id,
                   "actor": job.actor.get("user_id"), "outcome": "failed", "error_code": "panel_unreachable"})

    def panel(self, panel_id: str) -> PanelState:
        self._sweep()
        st = self.panels.get(panel_id)
        if st is None:
            raise BrokerError(404, "No Bluetooth agent has reported from that panel.")
        return st

    def list_panels(self) -> list:
        self._sweep()
        now = self.clock()
        out = []
        for st in self.panels.values():
            agent = st.snapshot.get("agent") if isinstance(st.snapshot.get("agent"), dict) else {}
            out.append({"panel_id": st.panel_id, "online": st.online(now), "last_seen_s": int(now - st.last_seen),
                        "agent_version": agent.get("version"), "ota_enabled": bool(agent.get("ota_enabled")),
                        "adapter": agent.get("hci")})
        return out

    def active_job(self, panel_id: str) -> Optional[Job]:
        self._sweep()
        for j in self.jobs.values():
            if j.panel_id == panel_id and j.state in ("queued", "sent", "running") and j.op not in ("status", "update_cancel"):
                return j
        return None

    def view(self, panel_id: str) -> dict:
        """Everything the UI needs for one panel: online state, devices (scan merged with remembered), the active job."""
        st = self.panel(panel_id)
        now = self.clock()
        snap = st.snapshot
        devices: dict[str, dict] = {}
        for d in (snap.get("devices") or []):
            if isinstance(d, dict) and DEVICE_ID_RX.match(str(d.get("id", ""))):
                devices[d["id"]] = {"id": d["id"], "name": d.get("name") or "Device", "driver": d.get("driver") or "generic",
                                    "label": d.get("label") or "Bluetooth device", "remembered": True, "connected": bool(d.get("connected")),
                                    "lease_s": d.get("lease_s", 0), "supports_update": bool(d.get("supports_update")), "rssi": None,
                                    "in_range": False}
        scan = st.scan or {}
        for d in (scan.get("devices") or []):
            if not (isinstance(d, dict) and DEVICE_ID_RX.match(str(d.get("id", "")))):
                continue
            cur = devices.setdefault(d["id"], {"id": d["id"], "remembered": bool(d.get("remembered")), "connected": bool(d.get("connected")),
                                               "lease_s": 0, "supports_update": d.get("driver") == "omi"})
            cur.update({"name": d.get("name") or cur.get("name") or "Device", "driver": d.get("driver") or cur.get("driver") or "generic",
                        "label": d.get("label") or cur.get("label") or "Bluetooth device", "rssi": d.get("rssi"), "in_range": True,
                        "specific_driver": bool(d.get("specific_driver"))})
        rows = sorted(devices.values(), key=lambda r: (not r.get("connected"), not r.get("specific_driver", r.get("driver") != "generic"),
                                                       -(r["rssi"] if isinstance(r.get("rssi"), (int, float)) else -999)))
        job = self.active_job(panel_id)
        return {"panel_id": panel_id, "online": st.online(now), "last_seen_s": int(now - st.last_seen),
                "agent": snap.get("agent") or {}, "radio_holder": snap.get("radio_holder") or "", "devices": rows,
                "scan": {"at": scan.get("at"), "hidden_unnamed": scan.get("hidden_unnamed", 0)} if scan else None,
                "busy": job.public() if job else None}

    def enqueue(self, panel_id: str, op: str, device_id: Optional[str], params: Optional[dict], actor: dict) -> Job:
        if op not in USER_OPS:
            raise BrokerError(400, "unknown action")
        st = self.panel(panel_id)
        if not st.online(self.clock()):
            raise BrokerError(409, "That panel is offline.")
        if op in DEVICE_OPS and not (device_id and DEVICE_ID_RX.match(device_id)):
            raise BrokerError(400, "bad device id")
        params = clean(params) if isinstance(params, dict) else {}
        if op == "update_start" and not (params.get("confirm") is True and params.get("ack_no_undo") is True):
            raise BrokerError(400, "A firmware update must be confirmed, including that it cannot be undone.")
        if op == "omi_bond" and not (params.get("confirm") is True and params.get("ack_wipe") is True):
            raise BrokerError(400, "Secure pairing must be confirmed, including that the pendant erases its stored audio.")
        if op not in ("status", "update_cancel"):
            busy = self.active_job(panel_id)
            if busy is not None:
                raise BrokerError(409, "That panel is busy with another Bluetooth task.")
        job = Job(id=uuid.uuid4().hex[:20], panel_id=panel_id, op=op, device_id=device_id if op in DEVICE_OPS else None,
                  actor={"user_id": actor.get("user_id"), "role": actor.get("role")}, created=self.clock(), updated=self.clock(),
                  params=_forwarded_params(op, params))
        if USER_OPS[op]:
            if not audit({"phase": "requested", "action": op, "panel_id": panel_id, "device_id": job.device_id, "job_id": job.id,
                          "actor": actor.get("user_id"), "role": actor.get("role")}):
                raise BrokerError(503, "The audit log is unavailable, so this action was not started.")
        self.jobs[job.id] = job
        st.queue.append(job)
        st.event.set()
        return job

    def get_job(self, panel_id: str, job_id: str) -> Job:
        self._sweep()
        job = self.jobs.get(job_id)
        if job is None or job.panel_id != panel_id:
            raise BrokerError(404, "unknown job")
        return job


BROKER = Broker()
