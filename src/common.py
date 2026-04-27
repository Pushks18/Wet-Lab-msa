"""Shared utilities: rate-limited HTTP, checkpointing, logging."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CHECKPOINT_DIR = DATA_DIR / "checkpoints"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"

for d in (RAW_DIR, CHECKPOINT_DIR, OUTPUT_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

API_LOG = LOG_DIR / "api_log.jsonl"


def user_agent() -> str:
    email = os.getenv("USER_AGENT_EMAIL", "contact@example.com")
    return f"EcosystemAnalysis ({email})"


def log_api(source: str, url: str, status: int, ms: int, extra: dict | None = None) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "url": url,
        "status": status,
        "ms": ms,
    }
    if extra:
        rec.update(extra)
    with API_LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=16))
def http_get(url: str, source: str, **kwargs: Any) -> requests.Response:
    headers = kwargs.pop("headers", {}) or {}
    headers.setdefault("User-Agent", user_agent())
    t0 = time.time()
    r = requests.get(url, headers=headers, timeout=60, **kwargs)
    log_api(source, url, r.status_code, int((time.time() - t0) * 1000))
    r.raise_for_status()
    return r


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=16))
def http_post(url: str, source: str, json_body: dict | None = None, **kwargs: Any) -> requests.Response:
    headers = kwargs.pop("headers", {}) or {}
    headers.setdefault("User-Agent", user_agent())
    t0 = time.time()
    r = requests.post(url, headers=headers, json=json_body, timeout=60, **kwargs)
    log_api(source, url, r.status_code, int((time.time() - t0) * 1000),
            {"body_keys": list((json_body or {}).keys())})
    r.raise_for_status()
    return r


def load_msa_config() -> dict:
    return json.loads((CONFIG_DIR / "msa_config.json").read_text())


def write_manifest(phase: int, payload: dict) -> None:
    payload = {**payload, "completed_at": datetime.now(timezone.utc).isoformat()}
    (CHECKPOINT_DIR / f"phase_{phase}.manifest.json").write_text(json.dumps(payload, indent=2))


def manifest_exists(phase: int) -> bool:
    return (CHECKPOINT_DIR / f"phase_{phase}.manifest.json").exists()


class RateLimiter:
    def __init__(self, per_sec: float) -> None:
        self.interval = 1.0 / per_sec if per_sec > 0 else 0
        self._last = 0.0

    def wait(self) -> None:
        if self.interval == 0:
            return
        delta = time.time() - self._last
        if delta < self.interval:
            time.sleep(self.interval - delta)
        self._last = time.time()
