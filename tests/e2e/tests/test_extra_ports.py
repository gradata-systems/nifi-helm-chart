"""An `extraPorts` entry with an Ingress path routes to a NiFi listen processor."""

import requests

from support import environment as e
from support.nifi import STANDARD
from support.shell import wait_for

PORT = 9443  # values/target.yaml: extraPorts.listen
TLS_DIR = "/opt/tls"


def test_listen_http_via_ingress(env, target_group):
    nifi = env.target
    # The ingress controller connects to backends over HTTPS, so the listener uses the node's keystore
    ssl_context = nifi.create_controller_service(target_group, "org.apache.nifi.ssl.StandardSSLContextService", "tls", {
        "Keystore Filename": f"{TLS_DIR}/keystore.p12",
        "Keystore Password": e.PASSWORD,
        "Keystore Type": "PKCS12",
    })
    nifi.enable_controller_service(ssl_context)
    listener = nifi.create_processor(target_group, f"{STANDARD}.ListenHTTP", "listen", {
        "Listening Port": str(PORT),
        "Base Path": "listen",
        "SSL Context Service": ssl_context,
    })
    funnel = nifi.create_funnel(target_group)
    connection = nifi.connect(target_group, (listener, "PROCESSOR"), (funnel, "FUNNEL"), ["success"])
    nifi.start_processor(listener)

    # The listener takes a moment to bind on every node
    wait_for("ListenHTTP to accept a request via the ingress", lambda: post(env, "/listen").status_code == 200,
             timeout=120, interval=3)
    for _ in range(4):
        assert post(env, "/listen").status_code == 200

    wait_for("received FlowFiles to queue", lambda: nifi.queued(connection) >= 5, timeout=60, interval=3)


def test_other_paths_go_to_nifi(env):
    """Paths other than the extra port's are served by NiFi itself, which redirects them to the UI."""
    response = post(env, "/listen-other")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/nifi/")


def post(env, path):
    # Don't follow redirects: NiFi redirects unknown paths to its UI, which would hide a request that missed the listener
    return requests.post(f"https://{e.HOST}{path}", data=b"hello", verify=env.cafile, timeout=30,
                         allow_redirects=False)
