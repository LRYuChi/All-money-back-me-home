"""Tests for scripts/preflight_check.py — R64.

Each prod issue we hit gets a regression test that triggers the same
silent-break shape via a synthetic compose YAML.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Bootstrap scripts/ on sys.path
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import preflight_check as pf   # noqa: E402


VALID_COMPOSE_YAML = """
services:
  freqtrade:
    image: freqtradeorg/freqtrade:stable
    volumes:
      - ./trading_log:/freqtrade/trading_log
      - ./guards:/freqtrade/user_data/strategies/guards
    environment:
      - SUPERTREND_LIVE=0
  api:
    build: ./apps/api
    volumes:
      - ./trading_log:/app/trading_log:ro
      - ./strategies:/app/strategies:ro
      - ./guards:/app/guards
    environment:
      - SUPABASE_URL=${SUPABASE_URL}
      - SUPERTREND_JOURNAL_DIR=/app/trading_log/journal
  supertrend-cron:
    build: ./apps/api
    volumes:
      - ./trading_log:/app/trading_log
      - ./strategies:/app/strategies:ro
      - ./guards:/app/guards:ro
    environment:
      - SUPERTREND_JOURNAL_DIR=/app/trading_log/journal
  telegram-bot:
    build: ./apps/api
    volumes:
      - ./guards:/app/guards
    environment:
      - SUPABASE_URL=${SUPABASE_URL}
"""


@pytest.fixture
def fake_compose(tmp_path, monkeypatch):
    """Point preflight_check at a temp compose file."""
    p = tmp_path / "docker-compose.prod.yml"
    p.write_text(VALID_COMPOSE_YAML)
    monkeypatch.setattr(pf, "COMPOSE_PATH", p)
    monkeypatch.setattr(pf, "ENV_EXAMPLE_PATH", tmp_path / ".env.example")
    # Provide a minimal env example that satisfies REQUIRED_ENV_REFERENCES
    (tmp_path / ".env.example").write_text(
        "\n".join(pf.REQUIRED_ENV_REFERENCES) + "\n"
    )
    return p


# =================================================================== #
# check_no_duplicate_blocks
# =================================================================== #
def test_no_duplicate_blocks_passes_clean(fake_compose):
    errors = pf.check_no_duplicate_blocks(pf._load_compose())
    assert errors == []


def test_detects_duplicate_environment_block(fake_compose):
    """Repro of R55 bug — two `environment:` blocks under one service."""
    fake_compose.write_text(VALID_COMPOSE_YAML.replace(
        "  api:\n    build: ./apps/api\n    volumes:\n",
        "  api:\n    build: ./apps/api\n"
        "    environment:\n      - DUPE=1\n"
        "    volumes:\n",
    ))
    # Now api has two `environment:` blocks
    errors = pf.check_no_duplicate_blocks(pf._load_compose())
    assert any("api" in e and "environment" in e for e in errors), \
        f"Expected duplicate-detection error; got {errors}"


def test_detects_duplicate_volumes_block(fake_compose):
    fake_compose.write_text(VALID_COMPOSE_YAML.replace(
        "  freqtrade:\n    image: freqtradeorg/freqtrade:stable\n    volumes:\n",
        "  freqtrade:\n    image: freqtradeorg/freqtrade:stable\n"
        "    volumes:\n      - ./other:/other\n"
        "    environment:\n      - X=1\n"
        "    volumes:\n",
    ))
    errors = pf.check_no_duplicate_blocks(pf._load_compose())
    assert any("freqtrade" in e and "volumes" in e for e in errors)


# =================================================================== #
# check_required_mounts
# =================================================================== #
def test_required_mounts_pass_clean(fake_compose):
    errors = pf.check_required_mounts(pf._load_compose())
    assert errors == []


def test_detects_missing_api_trading_log_mount(fake_compose, monkeypatch):
    """Repro of R55 bug — api missed trading_log/ mount."""
    yaml_no_api_log = VALID_COMPOSE_YAML.replace(
        "      - ./trading_log:/app/trading_log:ro\n", "",
    )
    fake_compose.write_text(yaml_no_api_log)
    errors = pf.check_required_mounts(pf._load_compose())
    assert any("api" in e and "/app/trading_log" in e for e in errors)


def test_detects_missing_freqtrade_journal_mount(fake_compose):
    yaml_no_ft = VALID_COMPOSE_YAML.replace(
        "      - ./trading_log:/freqtrade/trading_log\n", "",
    )
    fake_compose.write_text(yaml_no_ft)
    errors = pf.check_required_mounts(pf._load_compose())
    assert any("freqtrade" in e and "/freqtrade/trading_log" in e
               for e in errors)


# =================================================================== #
# check_env_example_references
# =================================================================== #
def test_env_example_references_pass_when_complete(fake_compose):
    errors = pf.check_env_example_references()
    assert errors == []


def test_env_example_references_fail_when_missing_one(
    fake_compose, monkeypatch,
):
    """If .env.example is missing a required var, operator won't know to set it."""
    incomplete = "\n".join(pf.REQUIRED_ENV_REFERENCES[:-1])
    (fake_compose.parent / ".env.example").write_text(incomplete)
    errors = pf.check_env_example_references()
    missing_var = pf.REQUIRED_ENV_REFERENCES[-1]
    assert any(missing_var in e for e in errors)


def test_env_example_missing_entirely(fake_compose, monkeypatch):
    (fake_compose.parent / ".env.example").unlink()
    errors = pf.check_env_example_references()
    assert any(".env.example missing" in e for e in errors)


# =================================================================== #
# check_cross_service_consistency
# =================================================================== #
def test_cross_service_consistency_passes_when_matched(fake_compose):
    errors = pf.check_cross_service_consistency(pf._load_compose())
    assert errors == []


def test_cross_service_inconsistency_caught(fake_compose):
    """If api and cron sidecar set SUPERTREND_JOURNAL_DIR to different paths,
    they read/write different journals — silent split-brain."""
    bad = VALID_COMPOSE_YAML.replace(
        "      - SUPERTREND_JOURNAL_DIR=/app/trading_log/journal\n"
        "  supertrend-cron:",
        "      - SUPERTREND_JOURNAL_DIR=/some/other/place\n"
        "  supertrend-cron:",
    )
    fake_compose.write_text(bad)
    errors = pf.check_cross_service_consistency(pf._load_compose())
    assert any("inconsistency" in e for e in errors)


# =================================================================== #
# end-to-end run()
# =================================================================== #
def test_run_returns_0_on_clean_compose(fake_compose):
    assert pf.run() == 0


def test_run_returns_1_on_any_failure(fake_compose):
    # Drop a required mount → run() must exit 1
    yaml_no_ft = VALID_COMPOSE_YAML.replace(
        "      - ./trading_log:/freqtrade/trading_log\n", "",
    )
    fake_compose.write_text(yaml_no_ft)
    assert pf.run() == 1


def test_run_returns_1_when_compose_missing(fake_compose, monkeypatch):
    monkeypatch.setattr(pf, "COMPOSE_PATH", fake_compose.parent / "nope.yml")
    assert pf.run() == 1


def test_run_returns_1_on_invalid_yaml(fake_compose):
    fake_compose.write_text("services: {api: {volumes: [unterminated\n")
    assert pf.run() == 1


# =================================================================== #
# R134: check_cron_module_resolution
# =================================================================== #
def test_cron_module_resolution_passes_for_existing_modules(tmp_path, monkeypatch):
    """Sanity: invocations referencing modules that DO exist on the host pass."""
    cron_file = tmp_path / "crontab"
    # market_monitor.report_collector EXISTS in repo
    cron_file.write_text(
        "0 */6 * * * docker compose exec -T telegram-bot "
        "python -m market_monitor.report_collector\n"
    )
    monkeypatch.setattr(pf, "CRON_PATH", cron_file)
    errors = pf.check_cron_module_resolution()
    assert errors == [], f"unexpected errors: {errors}"


def test_cron_module_resolution_detects_missing_module(tmp_path, monkeypatch):
    """R133 regression: 'python -m src.jobs.daily_report' should fail
    because apps/api/src/jobs/daily_report.py does not exist."""
    cron_file = tmp_path / "crontab"
    cron_file.write_text(
        "0 8 * * * docker compose exec -T api python -m src.jobs.daily_report\n"
    )
    monkeypatch.setattr(pf, "CRON_PATH", cron_file)
    errors = pf.check_cron_module_resolution()
    assert len(errors) == 1
    assert "src.jobs.daily_report" in errors[0]
    assert "missing module" in errors[0]
    assert "R133" in errors[0]


def test_cron_module_resolution_skips_unmapped_prefixes(tmp_path, monkeypatch):
    """Modules outside CRON_MODULE_HOST_DIRS are skipped (no false-positive)."""
    cron_file = tmp_path / "crontab"
    cron_file.write_text(
        "0 0 * * * python -m some.unknown.thing\n"
    )
    monkeypatch.setattr(pf, "CRON_PATH", cron_file)
    assert pf.check_cron_module_resolution() == []


def test_cron_module_resolution_skips_comments(tmp_path, monkeypatch):
    """Commented-out cron lines must not trigger errors."""
    cron_file = tmp_path / "crontab"
    cron_file.write_text(
        "# 0 8 * * * python -m src.jobs.daily_report\n"  # commented = OK
        "# python -m market_monitor.does_not_exist  (yet)\n"
    )
    monkeypatch.setattr(pf, "CRON_PATH", cron_file)
    assert pf.check_cron_module_resolution() == []
