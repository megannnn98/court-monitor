"""Probe the court sources that known-risks records as dead.

Run once before the VPN and once through it; the two outputs are the answer to
"is bsr.sudrf.ru blocked for us, or gone".

The hypothesis on record: bsr.sudrf.ru (84.42.111.136) times out while its
neighbours .138 (cdep.ru) and .139 (sudrf.ru) answer in ~0.09 s, which looks
like one host being blocked rather than a network being unreachable.
"""

from __future__ import annotations

import socket
import ssl
import sys
import time
from urllib.request import Request, urlopen

HOSTS = [
    ("bsr.sudrf.ru", "Банк судебных решений — тот, ради которого всё"),
    ("sudrf.ru", "портал-навигатор (сосед по подсети, .139)"),
    ("cdep.ru", "Судебный департамент (сосед по подсети, .138)"),
    ("vsrf.ru", "Верховный Суд"),
    ("docs.sudrf.ru", "docs — раньше отдавал 404"),
]

TIMEOUT = 10.0


def tcp(host: str, port: int = 443) -> tuple[str, float]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT):
            return "ok", time.perf_counter() - started
    except TimeoutError:
        return "timeout", time.perf_counter() - started
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}", time.perf_counter() - started


def http(host: str) -> str:
    req = Request(f"https://{host}/", headers={"User-Agent": "court-monitor/probe"})
    ctx = ssl.create_default_context()
    try:
        with urlopen(req, timeout=TIMEOUT, context=ctx) as resp:  # noqa: S310
            body = resp.read(4096)
            return f"HTTP {resp.status}, {len(body)}+ bytes"
    except Exception as exc:  # noqa: BLE001 - every failure mode is a result here
        return f"{type(exc).__name__}: {str(exc)[:70]}"


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    print(f"=== {label} ===")
    try:
        egress = (
            urlopen(  # noqa: S310
                Request("https://api.ipify.org", headers={"User-Agent": "curl/8"}), timeout=TIMEOUT
            )
            .read()
            .decode()
        )
        print(f"внешний IP: {egress}")
    except Exception as exc:  # noqa: BLE001
        print(f"внешний IP: не определён ({type(exc).__name__})")

    for host, note in HOSTS:
        try:
            addr = socket.gethostbyname(host)
        except OSError as exc:
            print(f"{host:16} DNS FAIL ({exc}) — {note}")
            continue
        state, elapsed = tcp(host)
        line = f"{host:16} {addr:16} tcp={state:28} {elapsed:5.2f}s"
        if state == "ok":
            line += f"  {http(host)}"
        print(f"{line}  — {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
