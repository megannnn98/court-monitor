"""The pinned trust anchor for fedsfm.ru, and the Dockerfile that installs it.

Two things are worth a test and neither is about cryptography. The first is that the
certificates in `docker/certs` are the ones `docker/certs/README.md` claims — a rotated
certificate with a stale fingerprint written beside it would leave the reader trusting
a document that describes something else. The second is the extension: `update-ca-certificates`
in Debian takes only `*.crt`, and a file named `.pem` is copied into the directory and
silently never enters the bundle. That failure is invisible until the download stops
working, which is how it was found the first time.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CERTS = ROOT / "docker" / "certs"
README = (CERTS / "README.md").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")

# The chain the fedsfm.ru certificate needs, as it is pinned: root and the one
# intermediate the site does not send.
PINNED = {
    "russian-trusted-root-ca.crt": (
        "D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:"
        "B5:BD:70:3E:97:88:CA:8E:CF:31"
    ),
    "russian-trusted-sub-ca-2024.crt": (
        "21:55:78:50:36:C9:00:DB:B5:F1:BB:2A:15:69:C8:0C:55:59:5B:D6:BF:94:"
        "86:7A:29:BB:DD:BC:7D:88:A3:F2"
    ),
}


def _fingerprint(path: Path) -> str:
    out = subprocess.run(
        ["openssl", "x509", "-in", str(path), "-noout", "-fingerprint", "-sha256"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split("=", 1)[1].strip()


@pytest.mark.parametrize("name", sorted(PINNED))
def test_the_pinned_certificate_is_the_one_described(name: str) -> None:
    """The README is a record of what is trusted. If the file and the record disagree,
    the record is worse than nothing: it looks like a source of truth."""
    path = CERTS / name

    assert path.is_file(), f"{name} нет в docker/certs"
    assert _fingerprint(path) == PINNED[name], f"{name} не совпадает с заявленным отпечатком"
    assert PINNED[name] in README, f"отпечаток {name} не записан в README"


@pytest.mark.parametrize("name", sorted(PINNED))
def test_every_pinned_certificate_is_still_valid(name: str) -> None:
    """An expired anchor does not fail the build; it fails later, on a download, with a
    message about a network. Said here instead."""
    path = CERTS / name
    out = subprocess.run(
        ["openssl", "x509", "-in", str(path), "-noout", "-enddate"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    expires = re.sub(r"^notAfter=", "", out.strip())

    assert (
        subprocess.run(
            ["openssl", "x509", "-in", str(path), "-noout", "-checkend", "0"],
            capture_output=True,
        ).returncode
        == 0
    ), f"{name} истёк {expires}"


def test_the_certificates_keep_a_crt_extension() -> None:
    """`update-ca-certificates` takes only `*.crt`. A `.pem` here is copied into
    `/usr/local/share/ca-certificates/` and never enters the bundle — the download then
    fails with no clue why, which is exactly how it failed once."""
    certificates = [p for p in CERTS.iterdir() if p.suffix in {".pem", ".crt"}]

    assert certificates, "в docker/certs нет ни одного сертификата"
    assert {p.suffix for p in certificates} == {".crt"}, sorted(p.name for p in certificates)


def test_the_dockerfile_installs_them_into_the_system_store() -> None:
    """Into the system bundle, not a path only our client knows about: anything else
    would work for `curl_cffi` and fail for everything else in the image."""
    assert "docker/certs/" in DOCKERFILE
    assert "/usr/local/share/ca-certificates/" in DOCKERFILE
    assert "update-ca-certificates" in DOCKERFILE


def test_the_readme_records_provenance_and_a_limit() -> None:
    """Provenance, fingerprints and expiry, or the pin is a mystery blob."""
    for needle in ("SHA-256", "2032", "2029", "Russian Trusted Root CA"):
        assert needle in README, f"в README нет «{needle}»"

    # The 2022 intermediate is deliberately not pinned; saying so stops someone from
    # "fixing" the chain by adding every Russian certificate they can find.
    assert "не закреплён" in README
