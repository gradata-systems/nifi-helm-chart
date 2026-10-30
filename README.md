# Apache NiFi Helm Chart

This Helm chart is designed to be an easy-to-use method of deploying a NiFi cluster using secure defaults and the convenience of [cert-manager](https://cert-manager.io/) for securing traffic.

## Key features

1. Secure defaults: TLS for inter-node communication and LDAP for single sign-on.
2. Flexible allocation of repositories (content, flowfile and provenance) to PVCs.
3. Expose additional ports and/or Ingress routes to leverage NiFi listen processors like [ListenTCP](https://nifi.apache.org/docs/nifi-docs/components/org.apache.nifi/nifi-standard-nar/1.21.0/org.apache.nifi.processors.standard.ListenTCP/index.html).
4. Mount additional volumes such as NFS directories or Kubernetes secrets.
5. Support for NiFi [site-to-site](https://nifi.apache.org/docs/nifi-docs/html/user-guide.html#site-to-site) protocol, even when communicating with another NiFi instance external to the Kubernetes cluster. This is achieved through the use of a separate Ingress per node.
6. Support for Prometheus monitoring and alerting via a [ServiceMonitor](https://github.com/prometheus-operator/prometheus-operator/blob/main/Documentation/user-guides/getting-started.md).
7. Support log shipping using [filebeat](https://www.elastic.co/guide/en/beats/filebeat/current/kafka-output.html) to a Kafka broker.

## Prerequisites

### Mandatory

1. [cert-manager](https://github.com/cert-manager/cert-manager)
2. [cert-manager csi-driver](https://github.com/cert-manager/csi-driver)

### Optional

1. [ingress-nginx](https://github.com/kubernetes/ingress-nginx) (preferred, not tested with other Ingress controllers)

## Installation

1. Customise the required sections of the Helm [values.yaml](values.yaml):
   1. `global.nifi.encryption` Sensitive properties encryption key (stored in a Kubernetes secret)
   2. `global.tls` TLS certificate issuer and keystore password secret
   3. `global.ldap` LDAP connection details
   4. `ingress.hostName`
   5. `jvmHeap` Min/max JVM heap size. Rough rule is to size this to be half of the pod limit
   6. `resources` Pod CPU/memory resource requests and limits
   7. `volumeClaims` Provide at least one PVC template for data persistence
2. `helm install -n nifi --create-namespace nifi -f values.yaml oci://ghcr.io/gradata-systems/helm-charts/nifi`
3. Grant each NiFi node the following controller permissions. This is required in order for nodes to automatically disconnect and offload themselves prior to pod termination:
   1. Access the controller (view)
   2. Access the controller (modify)

## Upgrades

Nifi rolling upgrades are not officially supported, due to issues with flow versioning. Upgrades must be deployed as part of a full cluster outage.

### Procedure

1. Uninstall Helm chart
2. Wait for all NiFi pods to terminate
3. Set `image.tag` to reference the new `nifi` image version
4. Install Helm chart

## Troubleshooting

1. If pods fail to start up in time, before the Kubernetes liveness probe kills the pod, increase the probe parameters in the `probeTimings` section of [values.yaml](values.yaml).
2. When a pod stops (for example, during `kubectl drain`), its pre-stop hook disconnects the node and offloads its queued FlowFiles to the other nodes. It logs its progress to `logs/k8s-pre-stop.log`. If the log shows `Gave up waiting for node ... to offload`, the node was stopped with FlowFiles still queued. Increase `shutdown.podTerminationGracePeriodSeconds` so offloading has time to finish.
