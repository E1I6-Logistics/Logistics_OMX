"""Local single-writer workstation IPC. No network commands or motor operations here.

Only the rollout writes `status.json`; only MQTT writes `command.json`.
Use session tokens so a command from an earlier process cannot start a new rollout.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


def read_json(path: str | Path) -> dict:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def atomic_json(path: str | Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def new_status(session: str, phase: str, job_id: str | None = None, **extra: object) -> dict:
    return {"session": session, "phase": phase, "job_id": job_id,
            "pid": os.getpid(), "updated_at": time.time(), **extra}


def valid_ready(status: dict, session: str) -> bool:
    return status.get("session") == session and status.get("phase") == "READY"
