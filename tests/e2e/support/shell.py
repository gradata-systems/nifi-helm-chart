"""Thin wrappers around kind, kubectl and helm, always pinned to the e2e cluster's context."""

import json
import logging
import shutil
import socket
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

CLUSTER_NAME = "nifi-e2e"
CONTEXT = f"kind-{CLUSTER_NAME}"


class CommandError(RuntimeError):
    pass


def require_tools(*names):
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise RuntimeError(f"Missing required tools on PATH: {', '.join(missing)}. See tests/e2e/README.md.")


def run(args, *, input=None, check=True, timeout=900):
    log.info("$ %s", " ".join(str(a) for a in args))
    result = subprocess.run(
        [str(a) for a in args], input=input, capture_output=True, text=True, timeout=timeout
    )
    if check and result.returncode != 0:
        raise CommandError(
            f"Command failed ({result.returncode}): {' '.join(str(a) for a in args)}\n{result.stdout}\n{result.stderr}"
        )
    return result


def kubectl(*args, **kwargs):
    return run(["kubectl", "--context", CONTEXT, *args], **kwargs)


def kubectl_json(*args):
    return json.loads(kubectl(*args, "-o", "json").stdout)


def apply(manifest: str):
    """Apply a manifest given as YAML text."""
    return kubectl("apply", "--server-side", "--force-conflicts", "-f", "-", input=manifest)


def apply_file(path: Path):
    return kubectl("apply", "--server-side", "--force-conflicts", "-f", path)


def helm(*args, **kwargs):
    return run(["helm", "--kube-context", CONTEXT, *args], **kwargs)


def helm_install(release, chart, namespace, *, values=(), set_values=None, version=None, wait=True, timeout="10m"):
    args = ["upgrade", "--install", release, chart, "--namespace", namespace, "--create-namespace"]
    if version:
        args += ["--version", version]
    for path in values:
        args += ["-f", path]
    for key, value in (set_values or {}).items():
        args += ["--set", f"{key}={value}"]
    if wait:
        args += ["--wait", "--timeout", timeout]
    return helm(*args, timeout=1200)


def ensure_namespace(name):
    apply(f"apiVersion: v1\nkind: Namespace\nmetadata:\n  name: {name}\n")


def wait_for(description, predicate, *, timeout=600, interval=5):
    """Poll `predicate` until it returns a truthy value, which is returned. Exceptions count as "not yet"."""
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except Exception as e:  # noqa: BLE001 - polling tolerates transient failures
            last_error = e
        time.sleep(interval)
    raise TimeoutError(f"Timed out after {timeout}s waiting for {description}. Last error: {last_error!r}")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PortForward:
    """Runs `kubectl port-forward` in the background, restarting it if it exits (e.g. when the target pod restarts)."""

    def __init__(self, namespace, target, remote_port):
        self.namespace = namespace
        self.target = target
        self.remote_port = remote_port
        self.local_port = free_port()
        self._process = None
        self._start()

    def _start(self):
        self._process = subprocess.Popen(
            ["kubectl", "--context", CONTEXT, "-n", self.namespace, "port-forward", "--address", "127.0.0.1",
             self.target, f"{self.local_port}:{self.remote_port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        wait_for(f"port-forward to {self.namespace}/{self.target}", self._is_listening, timeout=60, interval=0.5)

    def _is_listening(self):
        with socket.socket() as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", self.local_port)) == 0

    def ensure_running(self):
        if self._process.poll() is not None:
            log.info("Restarting port-forward to %s/%s", self.namespace, self.target)
            self._start()
        return self.local_port

    def stop(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=10)
