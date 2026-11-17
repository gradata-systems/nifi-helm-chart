"""Deleting a pod offloads its queued FlowFiles to the remaining nodes before it stops (the pre-stop hook)."""

import pytest

from support import environment as e
from support.nifi import STANDARD
from support.shell import kubectl, wait_for


@pytest.mark.slow
def test_pod_deletion_offloads_queued_flowfiles(env, target_group):
    nifi = env.target
    generator = nifi.create_processor(target_group, f"{STANDARD}.GenerateFlowFile", "generate",
                                      {"Batch Size": "50", "File Size": "1 KB"}, scheduling_period="1 sec")
    funnel = nifi.create_funnel(target_group)
    queue = nifi.connect(target_group, (generator, "PROCESSOR"), (funnel, "FUNNEL"), ["success"])
    nifi.start_processor(generator)

    def queued_on_every_node():
        by_node = nifi.queued(queue, nodewise=True)
        return len(by_node) == e.TARGET_NODES and all(count >= 100 for count in by_node.values())

    wait_for("FlowFiles to queue on every node", queued_on_every_node, timeout=120, interval=3)
    nifi.set_run_status("processors", generator, "STOPPED")
    wait_for("the generator to stop",
             lambda: nifi.get(f"/processors/{generator}")["status"]["aggregateSnapshot"]["activeThreadCount"] == 0,
             timeout=60, interval=2)
    before = nifi.queued(queue)

    pod = f"{e.TARGET_FULLNAME}-{e.TARGET_NODES - 1}"
    kubectl("-n", e.TARGET_NAMESPACE, "delete", "pod", pod, "--wait=true", "--timeout=300s", timeout=330)

    # The FlowFile repository is persistent, so the deleted node would get its FlowFiles back on restart anyway.
    # Offloading is only proven if the remaining nodes hold everything while it's down.
    remaining = wait_for("the remaining nodes to report their queues", lambda: {
        address: count for address, count in nifi.queued(queue, nodewise=True).items()
        if not address.startswith(f"{pod}.")
    }, timeout=120, interval=3)
    assert sum(remaining.values()) == before, f"Remaining nodes hold {remaining}, expected {before} in total"

    wait_for(f"{pod} to be recreated and ready", lambda: kubectl(
        "-n", e.TARGET_NAMESPACE, "wait", f"pod/{pod}", "--for=condition=Ready", "--timeout=30s"), timeout=900)
    nifi.wait_for_cluster(e.TARGET_NODES)
    assert nifi.queued(queue) == before, "The FlowFile count changed after the node rejoined"
