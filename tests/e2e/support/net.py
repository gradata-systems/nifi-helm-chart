"""Route connections for test host names (e.g. `nifi.test:443`) to local port-forwards.

Requests keep the real host name, so SNI, the `Host` header and certificate hostname verification all behave as they
would for a client resolving the name through DNS. Only the TCP connection is redirected.
"""

import socket
import ssl
import threading

import urllib3.util.connection

_routes = {}
_lock = threading.Lock()
_original_create_connection = urllib3.util.connection.create_connection


def route(host, port, port_forward):
    """Send connections for host:port to the given PortForward."""
    with _lock:
        _routes[(host.lower(), port)] = port_forward


def route_suffix(suffix, port, port_forward):
    """Send connections for any host ending with `suffix` (and `suffix` itself, without the leading dot) to the PortForward."""
    with _lock:
        _routes[("*" + suffix.lower(), port)] = port_forward


def clear():
    with _lock:
        _routes.clear()


def resolve(host, port):
    host = host.lower()
    with _lock:
        port_forward = _routes.get((host, port))
        if port_forward is None:
            # Longest matching suffix wins, so specific host names can override a wildcard
            for (pattern, p), candidate in sorted(_routes.items(), key=lambda item: -len(item[0][0])):
                if p == port and pattern.startswith("*") and (host.endswith(pattern[1:]) or host == pattern[2:]):
                    port_forward = candidate
                    break
    if port_forward is None:
        return host, port
    return "127.0.0.1", port_forward.ensure_running()


def _create_connection(address, *args, **kwargs):
    return _original_create_connection(resolve(*address), *args, **kwargs)


def install():
    urllib3.util.connection.create_connection = _create_connection


def tls_peer_certificate(host, port, cafile, *, client_cert=None):
    """Handshake with host:port (via its route), verifying the chain and host name. Returns the peer certificate dict."""
    context = ssl.create_default_context(cafile=cafile)
    if client_cert:
        context.load_cert_chain(*client_cert)
    with socket.create_connection(resolve(host, port), timeout=15) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls:
            return tls.getpeercert()


def subject_alt_names(certificate):
    return sorted(value for kind, value in certificate.get("subjectAltName", ()) if kind == "DNS")
