# Breaking changes
* `shutdown.podTerminationGracePeriodSeconds` now defaults to `300` (previously `60`). The chart fails to render if it isn't at least 40 seconds longer than `shutdown.gracefulShutdownSeconds`. Increase any lower override before upgrading.
* A PodDisruptionBudget (`maxUnavailable: 1`) is now created by default, so `kubectl drain` evicts only one NiFi pod at a time. Disable with `podDisruptionBudget.enabled: false`.
* Removed the `ui.refreshInterval` value. NiFi 2 no longer supports `nifi.ui.autorefresh.interval`.

# Enhancements
* The pre-stop hook stops waiting before the pod's termination grace period ends. This leaves `gracefulShutdownSeconds` + 10 seconds for NiFi to stop, instead of the pod being killed mid-shutdown.
* The pre-stop hook logs a warning to `logs/k8s-pre-stop.log` when it gives up waiting for the node to disconnect or offload.

# Bug fixes
* Pre-stop could wait indefinitely for a node to disconnect or offload. Its retry limit only counted failed API calls, so the pod was killed at the end of its grace period.
* Pre-stop now detects a failed disconnect. Previously it always went on to offload the node.
* Pre-stop now finishes when the node has offloaded but the cluster coordinator didn't record it. This happens intermittently when the node being stopped is the coordinator: the cluster reports the node as `DISCONNECTED` instead of `OFFLOADED`.
* The NiFi Registry setting is no longer added to the NiFi configuration when NiFi Registry is disabled.

**Full Changelog**: https://github.com/gradata-systems/nifi-helm-chart/compare/v0.28.0...v1.0.0
