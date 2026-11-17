# Breaking changes
* `ingress.enabled: false` now disables routing. Previously it was ignored and the chart always created its Ingresses. If you set it to `false`, remove the override (or set it to `true`) before upgrading to keep your Ingresses.

# Enhancements
* Choose how NiFi is exposed with `ingress.controller`, so you can move off ingress-nginx, which is end-of-life. The default (`nginx`) is unchanged. See [Exposing NiFi](README.md#exposing-nifi).
  * `traefik`: Ingresses plus the Traefik v3 resources they need, including TLS passthrough for site-to-site.
  * `gateway`: Gateway API `HTTPRoute`s, `TLSRoute`s and a `BackendTLSPolicy`, attached to a Gateway you manage. Sessions stay on one node with a cookie (`ingress.gateway.sessionPersistence`), and metrics can use a separate Gateway (`ingress.gateway.metrics.parentRefs`).
  * `custom`: the chart creates no routing resources, so you can define your own.
* Deploy additional manifests, such as your own Ingresses or routes, with `extraObjects`.
* `ingress.ingressClassName` now defaults to the controller's name.
* Node certificates now include the site-to-site host names, and NiFi also routes site-to-site requests by their `Host` header, for controllers that pass TLS through.
* Added an end-to-end test suite, which deploys the chart to a local kind cluster and tests it with each controller. See [tests/e2e](tests/e2e/README.md).

# Bug fixes
* `extraPorts` entries with a `nodePort` and an `ingress` path were routed to a `<release>-nodeport` Service, which doesn't exist.

# Notes
* With `nginx`, the site-to-site Ingress needs ingress-nginx to allow snippet annotations at risk level `Critical`. See the [README](README.md#nginx-default).

**Full Changelog**: https://github.com/gradata-systems/nifi-helm-chart/compare/v1.0.0...v1.1.0
