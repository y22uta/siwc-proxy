#!/usr/bin/env bash
# Incorporate this tested gateway into a GitHub fork of the actual upstream siwc-proxy.
# Usage: bash scripts/publish-to-fork.sh y22uta/siwc-proxy
set -euo pipefail
TARGET="${1:-}"
UPSTREAM="d-nyamkhuu/siwc-proxy"
if [[ ! "$TARGET" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "Usage: bash scripts/publish-to-fork.sh owner/repository" >&2
  exit 2
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OWNER="${TARGET%%/*}"
# Create the fork automatically when GitHub CLI is present and authorized.
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  if ! gh repo view "$TARGET" >/dev/null 2>&1; then
    echo "Creating official GitHub fork via authenticated gh CLI..."
    gh repo fork "$UPSTREAM" --clone=false
  fi
fi
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# Cloning the fork preserves upstream Git history and fork relationship.
git clone "https://github.com/${TARGET}.git" "$WORK/repo"
cd "$WORK/repo"
CURRENT="$(git branch --show-current)"
if [[ -e muse-gateway ]]; then
  echo "Abort: muse-gateway/ already exists in fork. No files were overwritten." >&2
  exit 1
fi
mkdir -p muse-gateway
# Use tar streaming instead of rsync to support a fresh WSL install.
tar -C "$SOURCE_DIR" --exclude='./.git' --exclude='./.env' --exclude='*.sqlite3*' --exclude='*/__pycache__' --exclude='*.pyc' --exclude='./.pytest_cache' --exclude='./*.egg-info' --exclude='./dist' --exclude='./build' --exclude='./.venv' -cf - . | tar -C "$WORK/repo/muse-gateway" -xf -
git add muse-gateway
git commit -m 'Add self-hosted Muse MCP bridge for SIWC ChatGPT plan usage'
git push origin "HEAD:${CURRENT}"
echo "Published: https://github.com/${TARGET}/tree/${CURRENT}/muse-gateway"
