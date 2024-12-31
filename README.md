# Apache NiFi Helm Chart

This Helm chart is designed to be an easy-to-use method of deploying a NiFi cluster using secure defaults and the convenience of [cert-manager](https://cert-manager.io/) for securing traffic and managing repository encryption.

This chart deploys both [NiFi](https://nifi.apache.org/docs/nifi-docs/) and [NiFi Registry](https://nifi.apache.org/registry.html).

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
2. MySQL instance (for NiFi Registry)

## Installation

1. Customise the required sections of the Helm [values.yaml](values.yaml):
   1. `global.nifi.encryption` Sensitive properties encryption key (stored in a Kubernetes secret)
   2. `global.tls` TLS certificate issuer and keystore password secret
   3. `global.ldap` LDAP connection details for both Nifi and NiFi Registry
   5. `ingress.hostName`
   6. `jvmHeap` Min/max JVM heap size. Rough rule is to size this to be half of the pod limit
   7. `resources` Pod CPU/memory resource requests and limits
   8. `volumeClaims` Provide at least one PVC template for data persistence
   9. `nifi_registry.ingress.hostName`
   10. `nifi_registry.database`
   11. `nifi_registry.persistentVolumeClaim` PVC to use for NiFi Registry [file system persistence provider](https://nifi.apache.org/docs/nifi-registry-docs/html/administration-guide.html#filesystemflowpersistenceprovider)
2. `helm install -n nifi --create-namespace nifi -f values.yaml oci://ghcr.io/gradata-systems/helm-charts/nifi`
3. Grant each NiFi node the following controller permissions. This is required in order for nodes to automatically disconnect and offload themselves prior to pod termination:
   1. Access the controller (view)
   2. Access the controller (modify)

## Upgrades

Nifi rolling upgrades are not officially supported, due to issues with flow versioning. Upgrades must be deployed as part of a full cluster outage.

### Procedure

1. Uninstall Helm chart
2. Wait for all NiFi and NiFi Registry pods to terminate
3. Set image tags to reference new `nifi` and `nifi-registry` image versions:
   1. `image.tag`
   2. `nifi_registry.image.tag`
4. Install Helm chart

## Troubleshooting

1. If pods fail to start up in time, before the Kubernetes liveness probe kills the pod, increase the probe parameters in the `probeTimings` section of [values.yaml](values.yaml).
2. NiFi pods initially fail to become `Ready`. NiFi will fail to bootstrap if it can't reach Nifi Registry during startup. It is normal for NiFi pods to fail liveness probes and be restarted at least once, while waiting for NiFi Registry to become available.
