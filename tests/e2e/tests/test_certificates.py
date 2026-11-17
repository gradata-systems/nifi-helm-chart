"""Every external host name serves a certificate that chains to the cluster CA and matches the host name."""

import pytest

from support import environment as e
from support import net

HOSTS = [e.HOST, e.METRICS_HOST, e.S2S_HOST, *e.NODE_S2S_HOSTS]


@pytest.mark.parametrize("host", HOSTS)
def test_certificate_matches_host(env, host):
    # Verifies the chain against the CA and the certificate against the host name
    certificate = net.tls_peer_certificate(host, 443, env.cafile)
    assert host in net.subject_alt_names(certificate)


@pytest.mark.parametrize("index", range(e.TARGET_NODES))
def test_site_to_site_reaches_node(env, index):
    """With pass-through, each node's site-to-site host name is served by that node's own certificate."""
    if not env.passthrough:
        pytest.skip(f"{env.controller} terminates site-to-site TLS")
    certificate = net.tls_peer_certificate(e.NODE_S2S_HOSTS[index], 443, env.cafile)
    node_host = f"{e.TARGET_FULLNAME}-{index}.{e.TARGET_FULLNAME}.{e.TARGET_NAMESPACE}"
    assert node_host in net.subject_alt_names(certificate)
