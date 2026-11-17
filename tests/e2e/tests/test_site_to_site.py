"""A separate NiFi instance sends data to the cluster over HTTP site-to-site, through the ingress controller.

The client only knows `https://s2s.<host>`. NiFi must answer its peer request with each node's own site-to-site
host name (`<pod>.s2s.<host>:443`), which the client then connects to through the controller. Data reaching every
node shows that both the peer routing in NiFi and the per-node routes in the controller work.
"""

from support import environment as e
from support.nifi import STANDARD
from support.shell import wait_for


def test_site_to_site_delivers_to_every_node(env, name, target_group, client_group):
    target, client = env.target, env.client

    # Target: a remote input port that queues whatever arrives
    port = target.create_input_port(target_group, name, remote=True)
    funnel = target.create_funnel(target_group)
    received = target.connect(target_group, (port, "INPUT_PORT"), (funnel, "FUNNEL"))
    for identity in (e.CLIENT_NODE_DN, e.TARGET_INGRESS_DN):
        target.grant("write", f"/data-transfer/input-ports/{port}", identity)
    target.set_run_status("input-ports", port, "RUNNING")

    # Client: generate FlowFiles and send them to the target's port
    rpg = client.create_remote_process_group(client_group, f"https://{e.S2S_HOST}")
    remote_port = client.remote_input_port(rpg, name)
    generator = client.create_processor(client_group, f"{STANDARD}.GenerateFlowFile", "generate",
                                        {"Batch Size": "5", "File Size": "1 KB"}, scheduling_period="1 sec")
    client.connect(client_group, (generator, "PROCESSOR"), (remote_port, "REMOTE_INPUT_PORT", rpg), ["success"])
    client.set_run_status("remote-process-groups", rpg, "TRANSMITTING")
    client.start_processor(generator)

    def every_node_received():
        by_node = target.queued(received, nodewise=True)
        return len(by_node) == e.TARGET_NODES and all(count > 0 for count in by_node.values())

    wait_for("FlowFiles to arrive on every node via site-to-site", every_node_received, timeout=300, interval=10)
