"""Minimal NiFi REST API client for building and inspecting test flows."""

import logging
import time
import uuid

import requests

from .shell import wait_for

log = logging.getLogger(__name__)

STANDARD = "org.apache.nifi.processors.standard"


class NiFiError(RuntimeError):
    pass


class NiFi:
    def __init__(self, base_url, cafile, username, password):
        self.base_url = base_url.rstrip("/")
        self.cafile = cafile
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.verify = cafile
        self.client_id = str(uuid.uuid4())

    # --- Transport -------------------------------------------------------------------------------------------------

    def login(self):
        response = self.session.post(
            f"{self.base_url}/nifi-api/access/token",
            data={"username": self.username, "password": self.password},
            timeout=30,
        )
        if response.status_code not in (200, 201):
            raise NiFiError(f"Login failed ({response.status_code}): {response.text}")
        # Authenticate with the header only: the bearer cookie would also require a CSRF token on every write
        self.session.cookies.set("__Secure-Authorization-Bearer", None)
        self.session.headers["Authorization"] = f"Bearer {response.text}"
        return response.text

    def request(self, method, path, *, expected=(200, 201, 202), retries=5, **kwargs):
        url = path if path.startswith("https://") else f"{self.base_url}/nifi-api{path}"
        for attempt in range(retries):
            try:
                response = self.session.request(method, url, timeout=60, **kwargs)
            except requests.ConnectionError:
                if attempt == retries - 1:
                    raise
                time.sleep(3)
                continue
            if response.status_code == 401 and attempt < retries - 1:
                self.login()
                continue
            # A node that is (re)connecting rejects requests briefly
            if response.status_code in (409, 502, 503) and "not connected" in response.text.lower() and attempt < retries - 1:
                time.sleep(5)
                continue
            if response.status_code not in expected:
                raise NiFiError(f"{method} {url} returned {response.status_code}: {response.text}")
            return response.json() if "json" in response.headers.get("Content-Type", "") else response.text
        raise AssertionError("unreachable")

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, body, **kwargs):
        return self.request("POST", path, json=body, **kwargs)

    def put(self, path, body, **kwargs):
        return self.request("PUT", path, json=body, **kwargs)

    def delete(self, path, **kwargs):
        return self.request("DELETE", path, **kwargs)

    def _revision(self, entity):
        return {"version": entity["revision"]["version"], "clientId": self.client_id}

    def _new(self):
        return {"version": 0, "clientId": self.client_id}

    # --- Cluster ---------------------------------------------------------------------------------------------------

    def current_user(self):
        return self.get("/flow/current-user")["identity"]

    def cluster_summary(self):
        return self.get("/flow/cluster/summary")["clusterSummary"]

    def wait_for_cluster(self, node_count, timeout=900):
        def connected():
            summary = self.cluster_summary()
            return summary["connectedNodeCount"] == node_count and summary["totalNodeCount"] == node_count

        wait_for(f"{node_count} connected NiFi nodes at {self.base_url}", connected, timeout=timeout, interval=10)

    def root_id(self):
        return self.get("/flow/process-groups/root")["processGroupFlow"]["id"]

    # --- Users and policies ----------------------------------------------------------------------------------------

    def user_id(self, identity):
        for user in self.get("/tenants/users")["users"]:
            if user["component"]["identity"] == identity:
                return user["id"]
        created = self.post("/tenants/users", {"revision": self._new(), "component": {"identity": identity}})
        return created["id"]

    def grant(self, action, resource, identity):
        """Add `identity` to the policy for action/resource, creating the policy if it doesn't exist."""
        user_id = self.user_id(identity)
        policy = self.get(f"/policies/{action}{resource}", expected=(200, 404))
        # NiFi returns the nearest inherited policy, so check it's for this exact resource
        if isinstance(policy, dict) and policy.get("component", {}).get("resource") == resource:
            users = policy["component"]["users"]
            if any(u["id"] == user_id for u in users):
                return
            self.put(f"/policies/{policy['id']}", {
                "revision": self._revision(policy),
                "component": {"id": policy["id"], "users": users + [{"id": user_id}]},
            })
        else:
            self.post("/policies", {"revision": self._new(), "component": {
                "action": action, "resource": resource, "users": [{"id": user_id}], "userGroups": [],
            }})

    def grant_root_access(self, identity):
        root = self.root_id()
        for resource in (f"/process-groups/{root}", f"/data/process-groups/{root}", f"/operation/process-groups/{root}"):
            for action in ("read", "write"):
                self.grant(action, resource, identity)

    # --- Flow building ---------------------------------------------------------------------------------------------

    def create_process_group(self, name, parent=None):
        parent = parent or self.root_id()
        return self.post(f"/process-groups/{parent}/process-groups", {
            "revision": self._new(), "component": {"name": name, "position": {"x": 0, "y": 0}},
        })["id"]

    @staticmethod
    def _property_names(descriptors, properties):
        """Map property display names (or names) to their internal names."""
        by_display = {d["displayName"]: name for name, d in descriptors.items()}
        resolved = {}
        for key, value in properties.items():
            if key in descriptors:
                resolved[key] = value
            elif key in by_display:
                resolved[by_display[key]] = value
            else:
                raise NiFiError(f"Unknown property {key!r}. Available: {sorted(by_display)}")
        return resolved

    def create_processor(self, group, type_, name, properties=None, scheduling_period=None, auto_terminate=()):
        processor = self.post(f"/process-groups/{group}/processors", {
            "revision": self._new(),
            "component": {"type": type_, "name": name, "position": {"x": 0, "y": 0}},
        })
        config = {"autoTerminatedRelationships": list(auto_terminate)}
        if properties:
            config["properties"] = self._property_names(processor["component"]["config"]["descriptors"], properties)
        if scheduling_period:
            config["schedulingPeriod"] = scheduling_period
        processor = self.put(f"/processors/{processor['id']}", {
            "revision": self._revision(processor), "component": {"id": processor["id"], "config": config},
        })
        return processor["id"]

    def create_controller_service(self, group, type_, name, properties):
        service = self.post(f"/process-groups/{group}/controller-services", {
            "revision": self._new(), "component": {"type": type_, "name": name},
        })
        service = self.put(f"/controller-services/{service['id']}", {
            "revision": self._revision(service),
            "component": {"id": service["id"],
                          "properties": self._property_names(service["component"]["descriptors"], properties)},
        })
        return service["id"]

    def enable_controller_service(self, service_id):
        service = self.get(f"/controller-services/{service_id}")
        self.put(f"/controller-services/{service_id}/run-status",
                 {"revision": self._revision(service), "state": "ENABLED"})
        wait_for(f"controller service {service_id} to enable",
                 lambda: self.get(f"/controller-services/{service_id}")["component"]["state"] == "ENABLED",
                 timeout=120, interval=2)

    def create_input_port(self, group, name, remote=True):
        return self.post(f"/process-groups/{group}/input-ports", {
            "revision": self._new(),
            "component": {"name": name, "position": {"x": 0, "y": 0}, "allowRemoteAccess": remote},
        })["id"]

    def create_funnel(self, group):
        return self.post(f"/process-groups/{group}/funnels", {
            "revision": self._new(), "component": {"position": {"x": 0, "y": 200}},
        })["id"]

    def connect(self, group, source, destination, relationships=()):
        """`source` and `destination` are (id, type) or (id, type, group id) tuples."""
        def endpoint(spec):
            return {"id": spec[0], "type": spec[1], "groupId": spec[2] if len(spec) > 2 else group}

        return self.post(f"/process-groups/{group}/connections", {
            "revision": self._new(),
            "component": {"source": endpoint(source), "destination": endpoint(destination),
                          "selectedRelationships": list(relationships)},
        })["id"]

    def wait_until_valid(self, processor_id, timeout=120):
        def valid():
            component = self.get(f"/processors/{processor_id}")["component"]
            if component["validationStatus"] == "VALID":
                return True
            log.info("Processor %s is %s: %s", processor_id, component["validationStatus"],
                     component.get("validationErrors"))
            return False

        wait_for(f"processor {processor_id} to be valid", valid, timeout=timeout, interval=3)

    def set_run_status(self, kind, component_id, state):
        """kind: processors, input-ports, output-ports or remote-process-groups."""
        entity = self.get(f"/{kind}/{component_id}")
        self.put(f"/{kind}/{component_id}/run-status", {"revision": self._revision(entity), "state": state})

    def start_processor(self, processor_id):
        self.wait_until_valid(processor_id)
        self.set_run_status("processors", processor_id, "RUNNING")

    def queued(self, connection_id, nodewise=False):
        """Number of FlowFiles queued in a connection, cluster-wide or by node address."""
        status = self.get(f"/flow/connections/{connection_id}/status", params={"nodewise": str(nodewise).lower()})
        status = status["connectionStatus"]
        if not nodewise:
            return status["aggregateSnapshot"]["flowFilesQueued"]
        return {node["address"]: node["statusSnapshot"]["flowFilesQueued"] for node in status["nodeSnapshots"]}

    # --- Remote process groups -------------------------------------------------------------------------------------

    def create_remote_process_group(self, group, target_uri):
        return self.post(f"/process-groups/{group}/remote-process-groups", {
            "revision": self._new(),
            "component": {"targetUris": target_uri, "transportProtocol": "HTTP", "position": {"x": 0, "y": 0},
                          "communicationsTimeout": "30 sec"},
        })["id"]

    def remote_input_port(self, rpg_id, name, timeout=300):
        """Wait until the remote process group discovers the named input port. Returns the port's local id."""
        def port():
            component = self.get(f"/remote-process-groups/{rpg_id}")["component"]
            for p in component.get("contents", {}).get("inputPorts", []):
                if p["name"] == name and p.get("exists", True):
                    return p["id"]
            log.info("Remote process group %s doesn't see port %s yet. Issues: %s", rpg_id, name,
                     component.get("authorizationIssues") or component.get("validationErrors"))
            return None

        return wait_for(f"remote process group {rpg_id} to discover port {name}", port, timeout=timeout, interval=10)

    # --- Cleanup ---------------------------------------------------------------------------------------------------

    def delete_process_group(self, group_id):
        """Stop everything in a process group, empty its queues and delete it."""
        self.put(f"/flow/process-groups/{group_id}", {"id": group_id, "state": "STOPPED"})
        self.put(f"/remote-process-groups/process-group/{group_id}/run-status",
                 {"revision": self._new(), "state": "STOPPED"}, expected=(200, 201, 404))
        self.put(f"/flow/process-groups/{group_id}/controller-services", {"id": group_id, "state": "DISABLED"})
        drop = self.post(f"/process-groups/{group_id}/empty-all-connections-requests", {})
        drop_id = drop["dropRequest"]["id"]
        wait_for("queues to empty",
                 lambda: self.get(f"/process-groups/{group_id}/empty-all-connections-requests/{drop_id}")
                 ["dropRequest"]["finished"], timeout=120, interval=2)
        self.delete(f"/process-groups/{group_id}/empty-all-connections-requests/{drop_id}")

        def deleted():
            group = self.get(f"/process-groups/{group_id}")
            self.delete(f"/process-groups/{group_id}",
                        params={"version": group["revision"]["version"], "clientId": self.client_id})
            return True

        wait_for(f"process group {group_id} to delete", deleted, timeout=120, interval=5)

    def delete_process_groups_named(self, prefix):
        root = self.root_id()
        for group in self.get(f"/process-groups/{root}/process-groups")["processGroups"]:
            if group["component"]["name"].startswith(prefix):
                log.info("Deleting leftover process group %s", group["component"]["name"])
                self.delete_process_group(group["id"])
