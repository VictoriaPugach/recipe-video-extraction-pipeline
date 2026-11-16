"""Static guardrails for the Docker controlled-egress boundary.

The real network behaviour is covered separately by scripts/test_egress.sh;
these keyless tests prevent a Compose edit from silently restoring direct egress.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _service_block(compose_text: str, service: str) -> str:
    match = re.search(rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  \S|\Z)", compose_text)
    assert match, f"service {service} not found"
    return match.group(1)


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.full.yml"])
def test_compose_api_has_no_external_network(filename):
    text = (ROOT / filename).read_text(encoding="utf-8")
    api = _service_block(text, "api")
    proxy = _service_block(text, "egress-proxy")

    assert re.search(r"(?m)^    networks:\n      - internal\n", api)
    assert "- external" not in api
    assert "EGRESS_PROXY_URL: http://egress-proxy:3128" in api
    assert "condition: service_healthy" in api
    assert re.search(r"(?m)^    networks:\n      - internal\n      - external\n", proxy)
    assert not re.search(r"(?m)^    ports:", proxy), "proxy must not be published to the host"
    # ubuntu/squid ships no squidclient/curl/wget: the probe must be the
    # bash /dev/tcp one, else the container turns unhealthy on the first up.
    assert "/dev/tcp/127.0.0.1/3128" in proxy
    assert '"CMD", "squidclient"' not in proxy
    assert re.search(r"(?m)^  internal:\n    internal: true\n", text)
    assert "subnet: 172.30.5.0/24" in text


def test_squid_denies_non_public_destinations_before_allow():
    text = (ROOT / "deploy" / "egress" / "squid.conf").read_text(encoding="utf-8")
    for network in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
        "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16",
        "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4",
        "::/96", "fc00::/7", "fe80::/10", "ff00::/8",
    ):
        assert f"acl blocked_dst dst {network}" in text

    deny = text.index("http_access deny blocked_dst")
    allow = text.index("http_access allow app_net")
    final_deny = text.index("http_access deny all")
    assert deny < allow < final_deny
    assert "http_access allow all" not in text
    assert "http_access deny !Safe_ports" in text
    assert "http_access deny CONNECT !SSL_ports" in text
    assert "cache deny all" in text
    assert "access_log none" in text


def test_egress_script_preserves_linux_paths_on_windows():
    text = (ROOT / "scripts" / "test_egress.sh").read_text(encoding="utf-8")
    assert "export MSYS_NO_PATHCONV=1" in text
