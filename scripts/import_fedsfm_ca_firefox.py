"""Import fedsfm.ru CA certificates into Firefox profiles.

Firefox uses its own NSS database per profile, separate from the system trust
store. This script imports the pinned CA chain so Firefox can open fedsfm.ru
without certificate errors.

Usage:
    uv run python scripts/import_fedsfm_ca_firefox.py

The script finds all Firefox profiles and imports the CA into each. Firefox
must be restarted for changes to take effect.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CA_BUNDLE = PROJECT_ROOT / "config" / "ca" / "fedsfm_ru_chain.pem"
FIREFOX_DIR = Path.home() / ".mozilla" / "firefox"

CERT_NICKNAME = "fedsfm.ru CA chain"
TRUST_ARGS = "C,,"


def find_firefox_profiles() -> list[Path]:
    """Find all Firefox profile directories with cert9.db."""
    if not FIREFOX_DIR.exists():
        return []

    profiles = []
    for profile_dir in FIREFOX_DIR.iterdir():
        if not profile_dir.is_dir():
            continue
        cert_db = profile_dir / "cert9.db"
        if cert_db.exists():
            profiles.append(profile_dir)
    return profiles


def import_ca_to_profile(profile_dir: Path) -> tuple[bool, str]:
    """Import CA bundle into a single Firefox profile's NSS database.

    Returns (success, error_message). error_message is empty on success.
    """
    cert_db_dir = f"sql:{profile_dir}"

    try:
        subprocess.run(
            [
                "certutil",
                "-D",
                "-n",
                CERT_NICKNAME,
                "-d",
                cert_db_dir,
            ],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            [
                "certutil",
                "-A",
                "-n",
                CERT_NICKNAME,
                "-t",
                TRUST_ARGS,
                "-i",
                str(CA_BUNDLE),
                "-d",
                cert_db_dir,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return True, ""
    except subprocess.CalledProcessError as exc:
        return False, exc.stderr.strip()
    except FileNotFoundError:
        return False, "certutil not found. Install nss (Arch) or libnss3-tools (Debian)."


def main() -> int:
    """Import CA into all Firefox profiles."""
    if not CA_BUNDLE.exists():
        print(f"CA bundle not found: {CA_BUNDLE}", file=sys.stderr)
        return 1

    profiles = find_firefox_profiles()
    if not profiles:
        print("No Firefox profiles found.", file=sys.stderr)
        return 1

    print(f"Found {len(profiles)} Firefox profile(s)")
    print(f"CA bundle: {CA_BUNDLE}")
    print()

    success_count = 0
    failed_profiles: list[tuple[str, str]] = []
    for profile in profiles:
        print(f"Importing into {profile.name}...")
        ok, error = import_ca_to_profile(profile)
        if ok:
            print("  OK")
            success_count += 1
        else:
            print(f"  FAILED: {error}")
            failed_profiles.append((profile.name, error))

    print()
    print(f"Imported into {success_count}/{len(profiles)} profile(s)")

    if failed_profiles:
        print("\nFailed profiles:")
        for name, error in failed_profiles:
            print(f"  {name}: {error}")

    print("\nRestart Firefox for changes to take effect.")

    return 0 if success_count == len(profiles) else 1


if __name__ == "__main__":
    sys.exit(main())
