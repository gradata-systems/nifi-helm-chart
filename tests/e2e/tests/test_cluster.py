"""The cluster forms and the UI is usable through the ingress controller."""

import requests

from support import environment as e


def test_all_nodes_connected(env):
    summary = env.target.cluster_summary()
    assert summary["clustered"]
    assert summary["connectedNodeCount"] == e.TARGET_NODES
    nodes = env.target.get("/controller/cluster")["cluster"]["nodes"]
    assert sorted(n["status"] for n in nodes) == ["CONNECTED"] * e.TARGET_NODES


def test_ui_is_served(env):
    response = requests.get(f"https://{e.HOST}/nifi/", verify=env.cafile, timeout=30)
    assert response.status_code == 200
    assert "<html" in response.text.lower()


def test_ldap_login_identity(env):
    assert env.target.current_user() == "admin"


def test_session_survives_repeated_requests(env):
    """Requests in a logged-in session keep working wherever the controller sends them."""
    for _ in range(20):
        assert env.target.get("/flow/current-user", retries=1)["identity"] == "admin"


def test_rejects_bad_credentials(env):
    response = requests.post(f"https://{e.HOST}/nifi-api/access/token", verify=env.cafile, timeout=30,
                             data={"username": "admin", "password": "wrong-password"})
    assert response.status_code in (400, 401, 403)
