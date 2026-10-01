"""Static and executable contracts for the Termux deployment scripts."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
PHONE = ROOT / "phone"


def test_every_phone_shell_script_has_valid_bash_syntax() -> None:
    scripts = sorted(PHONE.glob("*.sh"))
    assert {script.name for script in scripts} >= {
        "install.sh",
        "backup.sh",
        "run.sh",
        "stop.sh",
        "update.sh",
    }
    for script in scripts:
        result = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


def test_phone_dependencies_exclude_local_models_and_non_site_services() -> None:
    requirements = "\n".join(
        line
        for line in (PHONE / "requirements.txt").read_text(encoding="utf-8").lower().splitlines()
        if line and not line.startswith("#")
    )

    for excluded in (
        "torch",
        "transformers",
        "sentence-transformers",
        "gliner",
        "dagster",
        "aiogram",
        "weasyprint",
        "psycopg[binary]",
    ):
        assert excluded not in requirements
    for required in ("fastapi", "uvicorn", "sqlalchemy", "psycopg", "markdown-it-py"):
        assert required in requirements


def test_install_checks_the_phone_and_has_a_pinned_pgvector_fallback() -> None:
    install = (PHONE / "install.sh").read_text(encoding="utf-8")

    assert "termux-info" in install
    assert "/proc/meminfo" in install
    assert "PGVECTOR_VERSION=v0.8.6" in install
    assert "CREATE EXTENSION IF NOT EXISTS vector" in install
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in install
    assert '--branch "$PGVECTOR_VERSION"' in install
    assert "https://github.com/pgvector/pgvector.git" in install
    assert "sys.version_info < (3, 13)" in install
    assert "OPENROUTER_API_KEY=%q" in install


def test_dump_restore_drops_its_temporary_superuser_on_both_paths() -> None:
    install = (PHONE / "install.sh").read_text(encoding="utf-8")

    assert install.count("ALTER ROLE court_monitor NOSUPERUSER") == 2
    assert "if ! pg_restore" in install


def test_update_records_rollback_commit_and_orders_deploy_steps() -> None:
    update = (PHONE / "update.sh").read_text(encoding="utf-8")

    assert "git rev-parse HEAD" in update
    assert update.index("git pull --ff-only") < update.index("pip install")
    assert update.index("pip install") < update.index("alembic upgrade head")
    assert update.index("alembic upgrade head") < update.index("stop.sh")
    assert update.index("stop.sh") < update.index("run.sh")


def test_backup_script_uses_configured_rclone_and_keeps_fourteen_dumps() -> None:
    backup = (PHONE / "backup.sh").read_text(encoding="utf-8")

    assert "PHONE_BACKUP_REMOTE" in backup
    assert "PHONE_BACKUP_PATH" in backup
    assert "PHONE_BACKUP_RETENTION" in backup
    assert "pg_dump -Fc" in backup
    assert "rclone copyto" in backup
    assert "rclone deletefile" in backup
