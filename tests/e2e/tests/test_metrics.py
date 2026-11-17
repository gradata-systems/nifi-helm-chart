"""Each node's Prometheus metrics are available at metrics.<host>/<pod name>."""

import pytest
import requests

from support import environment as e


@pytest.mark.parametrize("index", range(e.TARGET_NODES))
def test_node_metrics(env, index):
    response = requests.get(f"https://{e.METRICS_HOST}/{e.TARGET_FULLNAME}-{index}", verify=env.cafile, timeout=30)
    assert response.status_code == 200, response.text
    assert "nifi_" in response.text


def test_metrics_paths_are_exact(env):
    if env.controller == "nginx":
        pytest.skip("ingress-nginx treats paths as regular expressions when `rewrite-target` is set, so they match as prefixes")
    response = requests.get(f"https://{e.METRICS_HOST}/{e.TARGET_FULLNAME}-0/extra", verify=env.cafile, timeout=30)
    assert response.status_code == 404
