# Apache NiFi Helm Chart

This Helm chart is designed to be an easy-to-use method of deploying a NiFi cluster using secure defaults and the convenience of [cert-manager](https://cert-manager.io/) for securing traffic.

## Key features

1. Secure defaults: TLS for inter-node communication and LDAP for single sign-on.
2. Flexible allocation of repositories (content, flowfile and provenance) to PVCs.
3. Expose additional ports and/or Ingress routes to leverage NiFi listen processors like [ListenTCP](https://nifi.apache.org/docs/nifi-docs/components/org.apache.nifi/nifi-standard-nar/1.21.0/org.apache.nifi.processors.standard.ListenTCP/index.html).
4. Mount additional volumes such as NFS directories or Kubernetes secrets.
5. Support for NiFi [site-to-site](https://nifi.apache.org/docs/nifi-docs/html/user-guide.html#site-to-site) protocol, even when communicating with another NiFi instance external to the Kubernetes cluster. This is achieved through the use of a separate route (host name) per node.
6. Expose NiFi using [ingress-nginx](https://github.com/kubernetes/ingress-nginx), [Traefik](https://traefik.io/traefik/), the [Gateway API](https://gateway-api.sigs.k8s.io/), or your own routing resources. See [Exposing NiFi](#exposing-nifi).
7. Support for Prometheus monitoring and alerting via a [ServiceMonitor](https://github.com/prometheus-operator/prometheus-operator/blob/main/Documentation/user-guides/getting-started.md).
8. Support log shipping using [filebeat](https://www.elastic.co/guide/en/beats/filebeat/current/kafka-output.html) to a Kafka broker.

## Prerequisites

### Mandatory

1. [cert-manager](https://github.com/cert-manager/cert-manager)
2. [cert-manager csi-driver](https://github.com/cert-manager/csi-driver)

### Optional

1. One of the following, to expose NiFi outside the cluster (see [Exposing NiFi](#exposing-nifi)):
   1. [Traefik](https://traefik.io/traefik/) v3, with its Kubernetes Ingress and CRD providers enabled
   2. A [Gateway API](https://gateway-api.sigs.k8s.io/) implementation that supports `BackendTLSPolicy` and, for site-to-site, `TLSRoute`
   3. [ingress-nginx](https://github.com/kubernetes/ingress-nginx), which is end-of-life
   4. Any other ingress controller, using your own routing resources

## Installation

1. Customise the required sections of the Helm [values.yaml](values.yaml):
   1. `global.nifi.encryption` Sensitive properties encryption key (stored in a Kubernetes secret)
   2. `global.tls` TLS certificate issuer and keystore password secret
   3. `global.ldap` LDAP connection details
   4. `ingress.controller` and `ingress.hostName`
   5. `jvmHeap` Min/max JVM heap size. Rough rule is to size this to be half of the pod limit
   6. `resources` Pod CPU/memory resource requests and limits
   7. `volumeClaims` Provide at least one PVC template for data persistence
2. `helm install -n nifi --create-namespace nifi -f values.yaml oci://ghcr.io/gradata-systems/helm-charts/nifi`
3. Grant each NiFi node the following controller permissions. This is required in order for nodes to automatically disconnect and offload themselves prior to pod termination:
   1. Access the controller (view)
   2. Access the controller (modify)

## Exposing NiFi

`ingress.controller` selects the resources the chart creates to expose NiFi. Whichever you choose, the chart serves:

| Host name | Purpose |
|---|---|
| `ingress.hostName` | NiFi UI and API, plus any `extraPorts` with an `ingress` path |
| `metrics.<hostName>` | Prometheus metrics for each node at `/<pod name>` (if `metrics.enabled`) |
| `s2s.<hostName>` and `<pod name>.s2s.<hostName>` | Site-to-site. Remote NiFi instances connect to `https://s2s.<hostName>`, which directs them to each node's own host name. |

Every host name needs a DNS record that resolves to your ingress controller or Gateway. The chart's certificate (secret `<release>-tls`) covers all of them. NiFi only serves HTTPS, so the controller must connect to NiFi using TLS. It should verify NiFi's certificate against the `ca.crt` in that secret and expect the server name `<release>-http.<namespace>`.

### `nginx` (default)

Ingresses for [ingress-nginx](https://github.com/kubernetes/ingress-nginx), as in previous chart versions. ingress-nginx is end-of-life, so plan to move to another option.

The site-to-site Ingress uses a `configuration-snippet` annotation, which recent ingress-nginx versions reject by default. Enable it in the controller with `allow-snippet-annotations: "true"` and `annotations-risk-level: Critical` (Helm values `controller.allowSnippetAnnotations` and `controller.config.annotations-risk-level`).

### `traefik`

Creates Ingresses for the UI and metrics, plus these Traefik CRDs:

- `ServersTransport` to verify NiFi's certificate. A second one presents the chart's certificate when proxying metrics requests.
- `Middleware` to rewrite metrics paths, and a `TLSOption` if `metrics.ingress.requireClientCertificate` is set.
- `IngressRouteTCP` that passes site-to-site TLS connections through to NiFi.

Sticky sessions are enabled with a cookie. Set `ingress.traefik.entryPoints` to your HTTPS entry point (default `websecure`). Traefik's `allowCrossNamespace` setting isn't needed: every resource is in the release namespace.

### `gateway`

Creates `HTTPRoute`s for the UI and metrics, `TLSRoute`s for site-to-site, and a `BackendTLSPolicy`. They attach to a Gateway you manage:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: shared-gateway
  namespace: gateway-system
spec:
  gatewayClassName: example
  listeners:
    - name: https
      protocol: HTTPS
      port: 443
      hostname: "*.example.com"  # Matches `ingress.hostName` and `metrics.<hostName>`
      tls:
        mode: Terminate
        certificateRefs:
          - name: nifi-tls       # The chart's `<release>-tls` secret
            namespace: nifi
      allowedRoutes:
        namespaces:
          from: All
    # Site-to-site listeners must be more specific than the HTTPS listener, so the Gateway passes them through
    - name: s2s
      protocol: TLS
      port: 443
      hostname: s2s.nifi.example.com
      tls:
        mode: Passthrough
      allowedRoutes:
        namespaces:
          from: All
    - name: s2s-nodes
      protocol: TLS
      port: 443
      hostname: "*.s2s.nifi.example.com"
      tls:
        mode: Passthrough
      allowedRoutes:
        namespaces:
          from: All
```

```yaml
ingress:
  controller: gateway
  hostName: nifi.example.com
  gateway:
    parentRefs:
      - name: shared-gateway
        namespace: gateway-system
        sectionName: https
    siteToSite:
      parentRefs:
        - name: shared-gateway
          namespace: gateway-system
          sectionName: s2s
        - name: shared-gateway
          namespace: gateway-system
          sectionName: s2s-nodes
```

- If the Gateway is in another namespace, the chart creates a `ReferenceGrant` so it can use the `<release>-tls` secret. Disable this with `ingress.gateway.createReferenceGrant: false`.
- By default, the `BackendTLSPolicy` refers to the `<release>-tls` Secret for the CA certificate. Not every implementation supports Secret references, so you might need a ConfigMap. Set it with `ingress.gateway.backendTLS.caCertificateRefs`.
- NiFi needs each session to stay on one node, because login tokens are only valid on the node that issued them. The chart sets the `HTTPRoute`'s `sessionPersistence` to a cookie (`ingress.gateway.sessionPersistence`). That field is in the Gateway API experimental channel. If your implementation doesn't support it, configure session affinity in the implementation instead.
- NiFi only returns metrics to an authorised client, so the Gateway serving the metrics host name must present a client certificate to NiFi (`spec.tls.backend.clientCertificateRef`, set to the `<release>-tls` secret). That setting applies to every route on the Gateway. **Don't set it on the Gateway that serves the UI**, or NiFi authenticates every UI user as the certificate's identity. Instead, attach the metrics route to a separate Gateway with `ingress.gateway.metrics.parentRefs`, or scrape metrics from inside the cluster.

### `custom`

The chart creates no routing resources, but still configures NiFi and its certificates for the host names above. Define your own Ingresses or routes (you can deploy them with the chart using `extraObjects`). They must:

1. Proxy to the `<release>-http` service over HTTPS, keeping each session on the same node (for example, with a cookie). NiFi login tokens are only valid on the node that issued them.
2. Pass site-to-site TLS connections through to NiFi without terminating them, matching on SNI: `s2s.<hostName>` to the `<release>-http` service, and `<release>-<n>.s2s.<hostName>` to the `<release>-<n>` service.

### Site-to-site authentication

With `traefik`, `gateway` and pass-through `custom` routes, the remote NiFi instance authenticates to NiFi directly with its own client certificate. With `nginx`, the controller verifies the client certificate and passes its identity to NiFi in a header. Either way, grant the remote instance's certificate identity access to the relevant ports.

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
