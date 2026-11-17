"""Builds the e2e environment: a kind cluster, its supporting services, an ingress controller and two NiFi releases."""

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import net
from .nifi import NiFi
from .shell import (CLUSTER_NAME, CONTEXT, PortForward, apply, apply_file, ensure_namespace, helm, helm_install,
                    kubectl, kubectl_json, require_tools, run, wait_for)

log = logging.getLogger(__name__)

E2E_DIR = Path(__file__).resolve().parent.parent
CHART_DIR = E2E_DIR.parent.parent
MANIFESTS = E2E_DIR / "manifests"
VALUES = E2E_DIR / "values"

CONTROLLERS = ("nginx", "traefik", "gateway")

# Pinned versions of the supporting components. Bump these deliberately.
VERSIONS = {
    "cert-manager": "v1.21.3",
    "cert-manager-csi-driver": "v0.16.1",
    "ingress-nginx": "4.15.1",
    "traefik": "42.4.0",
    "envoy-gateway": "1.10.0",
}

PASSWORD = "nifi-e2e-password"  # LDAP admin, keystore and sensitive properties key (min. 12 characters)

DOMAIN = "nifi.test"
HOST = DOMAIN
METRICS_HOST = f"metrics.{DOMAIN}"
S2S_HOST = f"s2s.{DOMAIN}"

TARGET_RELEASE, TARGET_NAMESPACE, TARGET_FULLNAME, TARGET_NODES = "nifi", "nifi", "nifi", 2
CLIENT_RELEASE, CLIENT_NAMESPACE, CLIENT_FULLNAME = "client", "nifi-client", "client-nifi"


def node_dn(fullname, namespace, index):
    return f"CN={fullname}-{index}.{fullname}.{namespace}"


TARGET_NODE_DNS = [node_dn(TARGET_FULLNAME, TARGET_NAMESPACE, i) for i in range(TARGET_NODES)]
TARGET_INGRESS_DN = f"CN={TARGET_FULLNAME}.{TARGET_NAMESPACE}"
CLIENT_NODE_DN = node_dn(CLIENT_FULLNAME, CLIENT_NAMESPACE, 0)
NODE_S2S_HOSTS = [f"{TARGET_FULLNAME}-{i}.{S2S_HOST}" for i in range(TARGET_NODES)]

# Controllers that pass site-to-site TLS through to NiFi, rather than terminating it
PASSTHROUGH_CONTROLLERS = {"traefik", "gateway"}


# --- Cluster and shared infrastructure -----------------------------------------------------------------------------

def ensure_cluster():
    require_tools("docker", "kind", "kubectl", "helm")
    clusters = run(["kind", "get", "clusters"]).stdout.split()
    if CLUSTER_NAME not in clusters:
        log.info("Creating kind cluster %s (takes a minute or two)", CLUSTER_NAME)
        run(["kind", "create", "cluster", "--name", CLUSTER_NAME, "--config", MANIFESTS / "kind.yaml", "--wait", "120s"],
            timeout=600)
    kubectl("cluster-info")


def delete_cluster():
    run(["kind", "delete", "cluster", "--name", CLUSTER_NAME], timeout=300)


def install_infrastructure():
    helm("repo", "add", "jetstack", "https://charts.jetstack.io", "--force-update")
    helm("repo", "add", "traefik", "https://traefik.github.io/charts", "--force-update")
    helm("repo", "add", "ingress-nginx", "https://kubernetes.github.io/ingress-nginx", "--force-update")
    helm("repo", "update", "jetstack", "traefik", "ingress-nginx")

    helm_install("cert-manager", "jetstack/cert-manager", "cert-manager", version=VERSIONS["cert-manager"],
                 set_values={"crds.enabled": "true"})
    helm_install("cert-manager-csi-driver", "jetstack/cert-manager-csi-driver", "cert-manager",
                 version=VERSIONS["cert-manager-csi-driver"])
    apply_file(MANIFESTS / "ca-issuer.yaml")
    wait_for("the e2e CA issuer to be ready", lambda: kubectl(
        "wait", "clusterissuer/nifi-e2e-ca", "--for=condition=Ready", "--timeout=10s"), timeout=180)

    apply_file(MANIFESTS / "ldap.yaml")
    kubectl("-n", "ldap", "rollout", "status", "deployment/ldap", "--timeout=300s", timeout=330)


# --- Ingress controllers -------------------------------------------------------------------------------------------

def install_controller(controller):
    if controller == "nginx":
        helm_install("ingress-nginx", "ingress-nginx/ingress-nginx", "ingress-nginx", version=VERSIONS["ingress-nginx"],
                     set_values={
                         "controller.service.type": "ClusterIP",
                         # The chart's site-to-site Ingress uses a configuration snippet
                         "controller.allowSnippetAnnotations": "true",
                         "controller.config.annotations-risk-level": "Critical",
                     })
    elif controller == "traefik":
        helm_install("traefik", "traefik/traefik", "traefik", version=VERSIONS["traefik"],
                     set_values={"service.spec.type": "ClusterIP"})
    elif controller == "gateway":
        helm_install("envoy-gateway", "oci://docker.io/envoyproxy/gateway-helm", "envoy-gateway-system",
                     version=VERSIONS["envoy-gateway"])
        apply_file(MANIFESTS / "gateway.yaml")
    else:
        raise ValueError(controller)


def _envoy_service(gateway_name):
    services = kubectl_json("-n", "envoy-gateway-system", "get", "service", "-l",
                            f"gateway.envoyproxy.io/owning-gateway-name={gateway_name},"
                            f"gateway.envoyproxy.io/owning-gateway-namespace=gateway")["items"]
    return services[0]["metadata"]["name"] if services else None


def controller_services(controller):
    """(namespace, service name) of the controller's HTTPS entry point, per host name group ("default", "metrics")."""
    if controller == "nginx":
        return {"default": ("ingress-nginx", "ingress-nginx-controller")}
    if controller == "traefik":
        return {"default": ("traefik", "traefik")}
    services = {}
    for key, gateway in (("default", "nifi"), ("metrics", "nifi-metrics")):
        name = wait_for(f"Envoy service for Gateway {gateway}", lambda g=gateway: _envoy_service(g), timeout=300)
        services[key] = ("envoy-gateway-system", name)
    return services


def route_cluster_dns(namespace, service):
    """Resolve *.nifi.test inside the cluster to the ingress controller, so pods reach NiFi the way external clients do."""
    cluster_ip = kubectl_json("-n", namespace, "get", "service", service)["spec"]["clusterIP"]
    config = kubectl_json("-n", "kube-system", "get", "configmap", "coredns")
    corefile = re.sub(r"\n\s*# nifi-e2e begin.*?# nifi-e2e end", "", config["data"]["Corefile"], flags=re.S)
    block = f"""
    # nifi-e2e begin
    template IN A {DOMAIN} {{
        match "^(.+\\.)?nifi\\.test\\.$"
        answer "{{{{ .Name }}}} 30 IN A {cluster_ip}"
    }}
    template IN AAAA {DOMAIN} {{
        rcode NOERROR
    }}
    # nifi-e2e end"""
    corefile = corefile.replace(".:53 {", ".:53 {" + block, 1)
    patch = json.dumps({"data": {"Corefile": corefile}})
    kubectl("-n", "kube-system", "patch", "configmap", "coredns", "--type", "merge", "-p", patch)
    kubectl("-n", "kube-system", "rollout", "restart", "deployment/coredns")
    kubectl("-n", "kube-system", "rollout", "status", "deployment/coredns", "--timeout=120s", timeout=150)


# --- NiFi releases -------------------------------------------------------------------------------------------------

def _create_secret(namespace):
    ensure_namespace(namespace)
    manifest = kubectl("-n", namespace, "create", "secret", "generic", "nifi-e2e",
                       f"--from-literal=sensitive-props-key={PASSWORD}",
                       f"--from-literal=keystore-password={PASSWORD}",
                       f"--from-literal=ldap-password={PASSWORD}",
                       "--dry-run=client", "-o", "yaml").stdout
    apply(manifest)


def _config_data(namespace, fullname):
    result = kubectl("-n", namespace, "get", "configmap", fullname, "-o", "jsonpath={.data}", check=False)
    return result.stdout if result.returncode == 0 else None


def delete_releases():
    for release, namespace in ((TARGET_RELEASE, TARGET_NAMESPACE), (CLIENT_RELEASE, CLIENT_NAMESPACE)):
        helm("uninstall", release, "--namespace", namespace, "--wait", check=False)
        kubectl("delete", "namespace", namespace, "--ignore-not-found", "--wait=true", timeout=600)


def install_release(release, namespace, fullname, values):
    _create_secret(namespace)
    before = _config_data(namespace, fullname)
    helm_install(release, CHART_DIR, namespace, values=values, wait=False)
    after = _config_data(namespace, fullname)
    # Startup scripts live in a ConfigMap, which doesn't roll the StatefulSet on its own
    if before is not None and before != after:
        log.info("NiFi configuration changed, restarting %s/%s", namespace, fullname)
        kubectl("-n", namespace, "rollout", "restart", f"statefulset/{fullname}")


def wait_for_release(namespace, fullname):
    kubectl("-n", namespace, "rollout", "status", f"statefulset/{fullname}", "--timeout=1200s", timeout=1230)


def ca_file(directory: Path):
    secret = kubectl_json("-n", TARGET_NAMESPACE, "get", "secret", f"{TARGET_FULLNAME}-tls")
    path = directory / "ca.crt"
    path.write_bytes(base64.b64decode(secret["data"]["ca.crt"]))
    return str(path)


@dataclass
class Environment:
    controller: str
    cafile: str
    target: NiFi
    client: NiFi
    port_forwards: list = field(default_factory=list)

    @property
    def passthrough(self):
        return self.controller in PASSTHROUGH_CONTROLLERS

    def close(self):
        for port_forward in self.port_forwards:
            port_forward.stop()
        net.clear()


def start(controller, workdir: Path):
    install_controller(controller)
    common = VALUES / "common.yaml"
    install_release(TARGET_RELEASE, TARGET_NAMESPACE, TARGET_FULLNAME,
                    [common, VALUES / "target.yaml", VALUES / f"{controller}.yaml"])
    install_release(CLIENT_RELEASE, CLIENT_NAMESPACE, CLIENT_FULLNAME, [common, VALUES / "client.yaml"])

    # After the release: Gateways only become programmed once the chart's ReferenceGrant allows their certificate refs
    services = controller_services(controller)
    route_cluster_dns(*services["default"])
    wait_for_release(TARGET_NAMESPACE, TARGET_FULLNAME)
    wait_for_release(CLIENT_NAMESPACE, CLIENT_FULLNAME)

    # Route test host names to local port-forwards
    net.install()
    port_forwards = []
    default = PortForward(services["default"][0], f"svc/{services['default'][1]}", 443)
    port_forwards.append(default)
    net.route_suffix("." + DOMAIN, 443, default)
    if "metrics" in services:
        metrics = PortForward(services["metrics"][0], f"svc/{services['metrics'][1]}", 443)
        port_forwards.append(metrics)
        net.route(METRICS_HOST, 443, metrics)
    client_host = f"{CLIENT_FULLNAME}-0.{CLIENT_FULLNAME}.{CLIENT_NAMESPACE}"
    apply_file(MANIFESTS / "client-relay.yaml")
    kubectl("-n", CLIENT_NAMESPACE, "rollout", "status", "deployment/relay", "--timeout=120s", timeout=150)
    client_forward = PortForward(CLIENT_NAMESPACE, "deployment/relay", 8443)
    port_forwards.append(client_forward)
    net.route(client_host, 8443, client_forward)

    cafile = ca_file(workdir)
    target = NiFi(f"https://{HOST}", cafile, "admin", PASSWORD)
    client = NiFi(f"https://{client_host}:8443", cafile, "admin", PASSWORD)
    environment = Environment(controller, cafile, target, client, port_forwards)

    try:
        wait_for(f"login to NiFi via {controller}", target.login, timeout=600, interval=10)
        target.wait_for_cluster(TARGET_NODES)
        wait_for("login to the client NiFi", client.login, timeout=600, interval=10)
        _grant_permissions(target, client)
    except Exception:
        environment.close()
        raise
    return environment


def _grant_permissions(target, client):
    for nifi, node_dns in ((target, TARGET_NODE_DNS), (client, [CLIENT_NODE_DN])):
        nifi.grant_root_access("admin")
        # Requests that touch FlowFile data (e.g. emptying a queue) are replicated by a node, which also needs data access
        root = nifi.root_id()
        for dn in node_dns:
            for action in ("read", "write"):
                nifi.grant(action, f"/data/process-groups/{root}", dn)
        nifi.delete_process_groups_named("e2e-")
    # README installation step: nodes need controller access to disconnect and offload themselves on shutdown
    for dn in TARGET_NODE_DNS:
        target.grant("read", "/controller", dn)
        target.grant("write", "/controller", dn)
    # Metrics are requested with the chart's certificate, as the ingress identity
    target.grant("read", "/flow", TARGET_INGRESS_DN)
    # Site-to-site clients: the client node directly (pass-through), or proxied by the ingress identity (nginx)
    for identity in (CLIENT_NODE_DN, TARGET_INGRESS_DN):
        target.grant("read", "/site-to-site", identity)
