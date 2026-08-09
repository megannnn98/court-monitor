#!/bin/bash
# Install fedsfm.ru CA certificates into system trust store (Arch Linux).
# Install CA into system trust store for non-Firefox applications
# (curl, wget, Python without pinned CA, etc.).
# IMPORTANT: Firefox on Arch Linux does NOT use the system trust store
# by default — use import_fedsfm_ca_firefox.py for Firefox.
#
# Usage:
#   sudo scripts/install_fedsfm_ca.sh
#
# The certificates are pinned for fedsfm.ru only in the project's HTTP client
# (see config/ca/README.md). This script is for non-Firefox applications:
# curl, wget, Python without the project's pinned CA, etc.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CA_BUNDLE="$PROJECT_ROOT/config/ca/fedsfm_ru_chain.pem"
SYSTEM_DIR="/etc/ca-certificates/trust-source/anchors"

if [ ! -f "$CA_BUNDLE" ]; then
    echo "CA bundle not found: $CA_BUNDLE" >&2
    exit 1
fi

if [ "$EUID" -ne 0 ]; then
    echo "This script requires root privileges." >&2
    echo "Run: sudo $0" >&2
    exit 1
fi

echo "Installing CA certificates for fedsfm.ru..."
echo "Source: $CA_BUNDLE"

mkdir -p "$SYSTEM_DIR"
cp "$CA_BUNDLE" "$SYSTEM_DIR/fedsfm_ru_chain.pem"

echo "Updating system trust store..."
trust extract-compat

echo "Done. System CA store updated."
echo "Note: Firefox on Arch uses its own NSS store (see import_fedsfm_ca_firefox.py)."
echo "Verify: curl -v https://fedsfm.ru/ 2>&1 | grep 'issuer:'"
