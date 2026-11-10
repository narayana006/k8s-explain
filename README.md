# k8s-explain

Pipe `kubectl` output in; get a plain-English diagnosis, the likely fix and read-only commands to confirm it.

**Status: working first version (v0.1.0).** Twenty rules cover the common pod failures (CrashLoopBackOff, OOMKilled, image pull errors, unschedulable pods, failing probes, volume and config errors, and a few application-log errors). Diagnosis is rule-based and runs entirely offline. The tool reads text and prints text; it never talks to your cluster.

## Install

Python 3.10 or newer, no dependencies.

```bash
git clone https://github.com/narayana006/k8s-explain
cd k8s-explain
pip install .
```

## Try it

With a real cluster:

```bash
kubectl describe pod api-7d9f | k8s-explain
```

Without one, use a sample from `examples/`:

```bash
k8s-explain < examples/describe-oomkilled.txt
```

```text
1 problem found (pod api-7d9f5c8b46-x2k4q, namespace shop)

1. Container killed for exceeding its memory limit (OOMKilled)

   Evidence:
     > Reason: OOMKilled
     > Exit Code: 137

   What is happening:
     The kernel killed the container because it used more memory than its limit allows
     (exit code 137 = SIGKILL). Kubernetes restarts it, and if it keeps happening the
     pod goes into CrashLoopBackOff.

   Likely cause:
     - The memory limit is lower than what the application needs under real load, or the
       application leaks memory.

   Likely fix:
     - Raise the memory limit to fit real usage, e.g. `kubectl set resources
       deployment/<name> -n shop --limits=memory=512Mi` (or change
       resources.limits.memory in the manifest and re-apply).
     - If usage grows without bound, look for a leak or cap the runtime heap (JVM -Xmx /
       MaxRAMPercentage, Node --max-old-space-size, GOMEMLIMIT).

   Verify (read-only):
     $ kubectl get pod api-7d9f5c8b46-x2k4q -n shop -o jsonpath='{.status.containerStatuses[*].lastState.terminated.reason}'
     $ kubectl top pod api-7d9f5c8b46-x2k4q -n shop --containers
     $ kubectl get pod api-7d9f5c8b46-x2k4q -n shop -o jsonpath='{.spec.containers[*].resources}'

k8s-explain only reads text. It has not contacted or changed your cluster.
```

Note that the pod's visible state in that sample is `CrashLoopBackOff`; the tool reports the underlying OOM kill instead of the symptom.

## What to pipe in

Anything textual that `kubectl` prints about a broken workload:

```bash
kubectl describe pod <pod> -n <ns> | k8s-explain
kubectl get events -n <ns> | k8s-explain
kubectl logs <pod> -n <ns> --previous | k8s-explain
k8s-explain saved-describe.txt            # a file works too
```

`kubectl describe pod` gives the best results, because it contains container states, exit codes and events together. The pod name and namespace are picked up from the input and filled into the suggested commands; when they cannot be found the commands show `<pod>` and `-n <namespace>` instead.

Sample inputs to experiment with:

| File | Shows |
| --- | --- |
| `examples/describe-oomkilled.txt` | CrashLoopBackOff caused by an OOM kill |
| `examples/describe-imagepull.txt` | ImagePullBackOff with a registry auth failure |
| `examples/describe-pending.txt` | Pending pod with three scheduling reasons at once |
| `examples/events.txt` | `kubectl get events` with a missing secret and failing probes |
| `examples/app.log` | application log with DNS failures |

## Options

| Option | Meaning |
| --- | --- |
| `--json` | Print findings as JSON (`pod`, `namespace`, `findings[]` with `id`, `title`, `explanation`, `causes`, `fixes`, `verify`, `evidence`, `priority`). |
| `--exit-code` | Exit with status 1 when at least one problem is found, for use in scripts. |
| `--list-rules` | List every problem the tool recognises. |
| `--version` | Print the version. |

Exit status is `0` normally, `1` with `--exit-code` when something was found, and `2` for empty input, an unreadable file or bad arguments.

## What it recognises

Output of `k8s-explain --list-rules`:

```text
bad-command              Container command cannot be started
config-error             Container cannot be configured (CreateContainerConfigError)
crash-loop               Container keeps crashing after start (CrashLoopBackOff)
evicted                  Pod was evicted by the node
image-pull               Image cannot be pulled (ImagePullBackOff / ErrImagePull)
invalid-image-name       Image reference is malformed (InvalidImageName)
liveness-probe           Liveness probe is failing, so the container is being restarted
log-connection-refused   Application cannot connect to a dependency
log-dns                  Application cannot resolve a hostname
log-permission-denied    Application hit a filesystem permission error
node-not-ready           Node is NotReady
oom-killed               Container killed for exceeding its memory limit (OOMKilled)
pvc-provisioning         PersistentVolumeClaim cannot be provisioned
quota                    Blocked by a ResourceQuota or LimitRange
rbac                     Kubernetes API request denied by RBAC
readiness-probe          Readiness probe is failing, so the pod receives no traffic
sandbox                  Pod network sandbox cannot be created (FailedCreatePodSandBox)
startup-probe            Startup probe is failing, so the container is killed before it finishes starting
unschedulable            Pod cannot be scheduled onto any node (Pending / FailedScheduling)
volume-mount             A volume cannot be attached or mounted (FailedMount / FailedAttachVolume)
```

How findings are chosen:

- Each rule is a set of regular expressions matched line by line; up to three matching lines are shown as evidence.
- Many rules have variants that narrow the cause when the same line says more. `Failed to pull image ... 401 Unauthorized` gives the credentials explanation rather than the generic one, and a `FailedScheduling` message that lists several reasons gets one cause and fix per reason.
- A symptom is dropped when its cause was also found: `CrashLoopBackOff` is not reported separately next to `OOMKilled` or a command that cannot start.
- For a plain crash loop the container exit code (1, 127, 139, 143, ...) is explained.
- Findings are ordered so that the most fundamental problem comes first.

Rules live as plain data in [`k8s_explain/rules.py`](k8s_explain/rules.py); adding one is a matter of adding an entry and a test.

## Read-only by design

`k8s-explain` has no code path that runs `kubectl` or opens a network connection, and a test fails the build if the package ever imports `subprocess`, `socket` or an HTTP client. Commands printed under **Verify** are restricted by another test to `kubectl get`, `describe`, `logs`, `top` and `auth can-i`. Commands under **Likely fix** may change your cluster, which is why they are only ever printed for you to review and run yourself.

## Features

- [x] Reads `kubectl describe`, events and logs from stdin (or a file)
- [x] Plain-English diagnosis (rule-based, 20 rules)
- [x] Likely fix with commands
- [x] Verification steps, filled in with the pod and namespace from the input
- [x] Read-only by design
- [x] JSON output and `--exit-code`
- [ ] Rules for Services/Ingress, Deployments rollouts, Jobs and HPA
- [ ] Container name in suggested commands for multi-container pods
- [ ] Optional model-written explanation for input no rule matches

## Limitations

- It only knows the patterns in its rule list. Input that matches none of them gets "No known problem pattern found", even if something is clearly wrong.
- Matching is textual. A log line that merely mentions `connection refused` or `permission denied` triggers the corresponding finding, so treat application-log findings as leads.
- It reads one blob of text and assumes it is about one pod. If you pipe events for several pods, the findings are merged and commands use `<pod>`.
- Suggested commands do not add `-c <container>`; for multi-container pods you may need to add it to `kubectl logs`.
- Fix commands contain placeholders such as `deployment/<name>` and example values such as `memory=512Mi`. They are starting points, not something to paste blindly.
- Tested against kubectl output in English from recent Kubernetes versions; message wording differs slightly across versions and container runtimes.

## Contributing

New rules are the most valuable contribution: open an issue with the (redacted) kubectl output that was not recognised, or send a pull request adding the rule and a test.

```bash
pip install -e ".[dev]"
pytest
```

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
