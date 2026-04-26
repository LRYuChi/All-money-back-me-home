"""Pre-deploy validation for docker-compose.prod.yml.

Catches the silent-break failure modes that have hit production this week:

  * Duplicate `environment:` blocks under one service (R55 — manual edit
    accidentally created two; the SECOND wins, the first is silently dropped)
  * Missing volume mounts that downstream code assumes (R55 — api missed
    trading_log/, dashboard endpoints returned `journal_dir_exists: false`)
  * Required env vars referenced in compose but not declared in .env.example
    (operator uses .env.example as a template, runs deploy, things silently
    fall back to defaults that are wrong for production)
  * Mismatched compose vs config values (e.g. SUPERTREND_JOURNAL_DIR pointing
    one place in the freqtrade service, another in cron sidecar — endpoints
    end up reading different journals)

Exit codes:
    0  — all checks passed
    1  — at least one check failed (deploy.sh aborts)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_PATH = REPO_ROOT / "docker-compose.prod.yml"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"


# Each rule is (service, mount_target_path) — these MUST be present or the
# downstream feature silently breaks. Add new rules here when wiring new
# cross-service shared state.
REQUIRED_MOUNTS = [
    ("freqtrade",       "/freqtrade/trading_log"),    # R46 journal write target
    ("api",             "/app/trading_log"),          # R55 dashboard read source
    ("supertrend-cron", "/app/trading_log"),          # R56 daily/weekly read source
    ("api",             "/app/strategies"),           # R55 router imports strategies.journal
    ("supertrend-cron", "/app/strategies"),           # R56 cron CLIs use strategies.*
    # R127: guards/ mounts — R104 root cause was freqtrade container 沒法
    # import guards.base (silent fall-through to None → 完全不擋單). 即使
    # 程式碼層加了 sys.path.insert (R104 fix)，若 compose 哪天誤刪 mount 整個
    # guards layer 還是會 silent disable. 把 4 個關鍵 mount 加入硬性檢查。
    ("freqtrade",       "/freqtrade/user_data/strategies/guards"),  # R104 root mount
    ("api",             "/app/guards"),                              # /operations endpoint
    ("telegram-bot",    "/app/guards"),                              # bot imports guards
    ("supertrend-cron", "/app/guards"),                              # cron tools use guards
]

# Env vars referenced via ${VAR} in compose that operators are expected to
# set. Confirms .env.example documents them (vs operator discovering at runtime).
# Format: substring check on the .env.example contents.
REQUIRED_ENV_REFERENCES = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "TELEGRAM_TOKEN",
    "TELEGRAM_CHAT_ID",
    "FT_USER",
    "FT_PASS",
]


# Cross-service consistency rules: if the LEFT side is set on service A,
# the RIGHT side MUST resolve to the same path on service B.
# Format: (service_a, env_name_a, service_b, env_name_b)
CROSS_SERVICE_CONSISTENCY = [
    # journal dir must be the same path-effective volume between freqtrade
    # writes and api/cron reads. We check the env-var match here; mount
    # path equivalence is checked by REQUIRED_MOUNTS.
    ("api",             "SUPERTREND_JOURNAL_DIR",
     "supertrend-cron", "SUPERTREND_JOURNAL_DIR"),
]


def _load_compose() -> dict[str, Any]:
    with open(COMPOSE_PATH) as f:
        return yaml.safe_load(f)


def _yaml_count_keys_under_service(svc_name: str, key: str) -> int:
    """Walk the raw YAML text and count how many `<key>:` lines appear at
    indent +4 from the service header. yaml.safe_load silently dedups
    these — we need raw text to spot duplicates."""
    text = COMPOSE_PATH.read_text()
    # Find the service block (greedy: until next service or end)
    pattern = re.compile(
        rf"^  {re.escape(svc_name)}:\s*\n((?:    .*\n|\n)+)",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        return 0
    block = m.group(1)
    # Count keys at indent of exactly 4 spaces (service-direct properties)
    keypat = re.compile(rf"^    {re.escape(key)}:\s*$", re.MULTILINE)
    return len(keypat.findall(block))


def _service_volumes(compose: dict, svc: str) -> list[str]:
    raw = (compose.get("services", {}).get(svc, {}) or {}).get(
        "volumes", []
    )
    return list(raw) if raw else []


def _service_envs(compose: dict, svc: str) -> dict[str, str]:
    """Return a name→raw-value dict (raw value includes ${...:-default} text)."""
    raw = (compose.get("services", {}).get(svc, {}) or {}).get(
        "environment", []
    )
    out: dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            if "=" in item:
                k, v = item.split("=", 1)
                out[k] = v
    elif isinstance(raw, dict):
        for k, v in raw.items():
            out[k] = str(v)
    return out


# =================================================================== #
# Checks
# =================================================================== #
def check_no_duplicate_blocks(compose: dict) -> list[str]:
    """A duplicate `environment:` or `volumes:` block under a service is
    silently dropped by yaml.safe_load. This is a real production hazard."""
    errors: list[str] = []
    for svc in compose.get("services", {}):
        for k in ("environment", "volumes"):
            n = _yaml_count_keys_under_service(svc, k)
            if n > 1:
                errors.append(
                    f"service '{svc}' has {n} `{k}:` blocks — yaml.safe_load "
                    f"silently keeps only the last; the first is dropped."
                )
    return errors


def check_required_mounts(compose: dict) -> list[str]:
    """Each (service, target_path) in REQUIRED_MOUNTS must appear in that
    service's volume list (matched by ':TARGET' suffix or full string)."""
    errors: list[str] = []
    for svc, target in REQUIRED_MOUNTS:
        vols = _service_volumes(compose, svc)
        # Volume entries are "host:container" or "host:container:ro" or "named:container"
        hits = [
            v for v in vols
            if isinstance(v, str) and (
                v.endswith(f":{target}") or v.endswith(f":{target}:ro")
                or f":{target}:" in v
            )
        ]
        if not hits:
            errors.append(
                f"service '{svc}' missing required mount target '{target}' "
                f"(see REQUIRED_MOUNTS rule in scripts/preflight_check.py)"
            )
    return errors


def check_env_example_references() -> list[str]:
    """Operators copy .env.example → .env. Anything compose expects to be
    set must be discoverable in the example (commented or set)."""
    errors: list[str] = []
    if not ENV_EXAMPLE_PATH.exists():
        return [".env.example missing — operators have no template to copy from"]
    text = ENV_EXAMPLE_PATH.read_text()
    for var in REQUIRED_ENV_REFERENCES:
        if var not in text:
            errors.append(
                f".env.example does not reference '{var}' — operators "
                f"won't know to set it; compose will fall through to defaults."
            )
    return errors


def check_cross_service_consistency(compose: dict) -> list[str]:
    """If two services BOTH set the same env var, the values must match —
    otherwise they read/write different paths despite being the 'same' setting."""
    errors: list[str] = []
    for svc_a, env_a, svc_b, env_b in CROSS_SERVICE_CONSISTENCY:
        envs_a = _service_envs(compose, svc_a)
        envs_b = _service_envs(compose, svc_b)
        val_a = envs_a.get(env_a)
        val_b = envs_b.get(env_b)
        if val_a is None or val_b is None:
            continue
        if val_a != val_b:
            errors.append(
                f"cross-service inconsistency: {svc_a}.{env_a}={val_a!r} "
                f"vs {svc_b}.{env_b}={val_b!r} — these must match or "
                f"the two services read different state."
            )
    return errors


# =================================================================== #
# Entry
# =================================================================== #
def run() -> int:
    if not COMPOSE_PATH.exists():
        print(f"ERROR: {COMPOSE_PATH} missing", file=sys.stderr)
        return 1

    try:
        compose = _load_compose()
    except yaml.YAMLError as e:
        print(f"ERROR: docker-compose.prod.yml is not valid YAML: {e}",
              file=sys.stderr)
        return 1

    all_errors: list[str] = []
    all_errors += check_no_duplicate_blocks(compose)
    all_errors += check_required_mounts(compose)
    all_errors += check_env_example_references()
    all_errors += check_cross_service_consistency(compose)

    if all_errors:
        print("=== Preflight FAILED ===")
        for e in all_errors:
            print(f"  ✗ {e}")
        print(f"\n{len(all_errors)} issue(s) — fix before deploying.")
        return 1

    print("=== Preflight OK ===")
    print("Compose YAML, mounts, envs, and cross-service consistency all valid.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
