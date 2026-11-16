#!/usr/bin/env bash
# Real Docker-network verification for controlled egress. Requires Docker
# Compose v2 and internet access. Uses an isolated Compose project and removes
# its containers/networks/volumes on exit; it does not stop the normal stack.
set -euo pipefail

# Git Bash/MSYS must pass Linux paths such as /dev/null to containers unchanged.
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! docker info >/dev/null 2>&1; then
  echo "FAIL: Docker daemon is unavailable" >&2
  exit 2
fi
if [[ ! -f .env ]]; then
  echo "Create .env first: cp .env.example .env (no real API key is needed for this test)" >&2
  exit 2
fi

COMPOSE=(docker compose -p fropti-egress-test -f docker-compose.yml)
cleanup() {
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${COMPOSE[@]}" up -d --build egress-proxy api
"${COMPOSE[@]}" exec -T egress-proxy squid -k parse >/dev/null

proxy_curl=(curl --noproxy "" --proxy http://egress-proxy:3128 --max-time 20)

echo "[1/5] Public HTTPS through the proxy must succeed"
"${COMPOSE[@]}" exec -T api "${proxy_curl[@]}" -fsS -o /dev/null https://example.com/
echo "PASS: allowed public destination"

expect_proxy_denial() {
  local label="$1"
  shift
  if "${COMPOSE[@]}" exec -T api "${proxy_curl[@]}" -fsS -o /dev/null "$@" >/dev/null 2>&1; then
    echo "FAIL: ${label} was allowed" >&2
    exit 1
  fi
  echo "PASS: ${label} denied"
}

echo "[2/5] Loopback and cloud metadata destinations must be denied"
expect_proxy_denial "loopback" http://127.0.0.1/
expect_proxy_denial "link-local metadata" http://169.254.169.254/latest/meta-data/

echo "[3/5] A redirect to a private destination must be denied"
# httpbin performs the public redirect; curl follows it through the same proxy,
# whose next destination check rejects 127.0.0.1.
expect_proxy_denial "redirect to loopback" -L \
  'https://httpbin.org/redirect-to?url=http%3A%2F%2F127.0.0.1%2F'

echo "[4/5] Non-HTTP ports must be denied"
expect_proxy_denial "unsafe destination port" http://example.com:22/

echo "[5/5] Bypassing the proxy must not provide direct internet access"
if "${COMPOSE[@]}" exec -T api curl --noproxy '*' -fsS \
    --connect-timeout 3 --max-time 5 -o /dev/null http://1.1.1.1/ >/dev/null 2>&1; then
  echo "FAIL: direct egress leaked" >&2
  exit 1
fi
echo "PASS: direct egress unavailable"
echo "All controlled-egress integration checks passed"
