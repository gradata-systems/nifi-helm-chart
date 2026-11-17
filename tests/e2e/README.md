# End-to-end tests

These tests deploy a real NiFi cluster with the chart, in a local [kind](https://kind.sigs.k8s.io/) cluster, and check that it works through each supported ingress controller.

| Test | What it checks |
|---|---|
| `test_cluster.py` | Both nodes join the cluster. The UI loads and LDAP login works through the controller, and the session keeps working across requests. |
| `test_certificates.py` | Every external host name serves a certificate that chains to the cluster CA and matches the name. With TLS passthrough, each node's site-to-site host name reaches that node. |
| `test_metrics.py` | Each node's Prometheus metrics are served at `metrics.<host>/<pod>`. |
| `test_extra_ports.py` | An `extraPorts` Ingress path reaches a `ListenHTTP` processor. |
| `test_site_to_site.py` | A second NiFi release sends data over HTTP site-to-site via `https://s2s.<host>`, and it arrives on every node. |
| `test_offload.py` (slow) | Deleting a pod offloads its queued FlowFiles to the other node, and none are lost. |

Each test run covers the `nginx`, `traefik` and `gateway` (Envoy Gateway) controllers in turn.

## Prerequisites

- Docker, with at least 8 GB of memory available to it
- [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation), `kubectl` and `helm` on your `PATH`
- Python 3.10+

With [uv](https://docs.astral.sh/uv/), no setup is needed. Prefix the commands below with `uv run --no-project --with-requirements requirements.txt`, for example:

```shell
cd tests/e2e
uv run --no-project --with-requirements requirements.txt pytest --controller traefik
```

Or use a virtualenv:

```shell
cd tests/e2e
python -m venv .venv
.venv/Scripts/activate      # Windows. On Linux or macOS: source .venv/bin/activate
pip install -r requirements.txt
```

### WSL

The suite runs in WSL 2 with Docker Desktop's WSL integration enabled. Install `kind`, `kubectl` and `helm` inside the distribution, and run the tests from the repository's Windows path (e.g. `/mnt/c/...`). Don't share a virtualenv between Windows and WSL: use `uv run` as above, or create the WSL one outside the repository.

## Running

```shell
pytest                          # Every controller
pytest --controller traefik     # One controller
pytest -m "not slow"            # Skip the tests that restart NiFi nodes
pytest -k site_to_site          # One test module
```

The first run creates a kind cluster named `nifi-e2e`. It installs cert-manager, the cert-manager CSI driver, a test CA, an LDAP server and the controllers. Then it installs two NiFi releases from your working copy of the chart:

- `nifi` (namespace `nifi`): the 2-node cluster under test, at `nifi.test`
- `client` (namespace `nifi-client`): a single node that sends site-to-site data to it

Starting NiFi takes several minutes. Later runs reuse the cluster and upgrade the releases in place, which is much faster. If a chart change alters NiFi's startup configuration, the setup restarts the nodes.

| Option | Effect |
|---|---|
| `--controller nginx,traefik` | Test only these controllers |
| `--fresh` | Uninstall both NiFi releases and delete their data first |
| `--delete-cluster` | Delete the kind cluster when the tests finish |

To remove everything: `kind delete cluster --name nifi-e2e`.

The tests only ever use the `kind-nifi-e2e` kube context. They don't touch your other clusters.

## How it works

- **Host names:** `*.nifi.test` resolves to the ingress controller inside the cluster, through a CoreDNS `template` rule that the setup adds. From your machine, the tests connect through `kubectl port-forward` but keep the real host name, so SNI, the `Host` header and certificate checks behave as they would over DNS.
- **Credentials:** NiFi logins use LDAP user `admin` with password `nifi-e2e-password`. The same value is used for the keystore password and the sensitive properties key.
- **Permissions:** the setup grants the policies a real deployment would need. That includes each node's controller access (used by the pre-stop hook), the ingress identity's metrics access, and site-to-site access for the client node.
- **Leftovers:** each test builds its components in its own `e2e-*` process group and deletes it afterwards. Leftovers from interrupted runs are removed at the next start.
- **Versions:** the versions of the supporting components are pinned in [`support/environment.py`](support/environment.py).

## Troubleshooting

- `kubectl --context kind-nifi-e2e -n nifi get pods` shows the cluster's state. Logs from NiFi's startup and pre-stop scripts are in `logs/` inside each pod.
- If a run is interrupted while NiFi is starting, run again: setup is idempotent.
- `--fresh` resets NiFi's flows, users and policies.
