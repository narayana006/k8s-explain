"""The diagnosis rules.

Each rule is plain data: regular expressions that detect a problem in kubectl
text, an explanation, fixes and commands to verify the diagnosis. A rule can
have variants that narrow the cause when a more specific message is present
(for example "ImagePullBackOff" plus "unauthorized").

In command strings ``{pod}`` and ``{ns}`` are replaced with the pod name and
``-n <namespace>`` found in the input, or with placeholders when unknown.
Commands under ``verify`` must be read-only; a test enforces this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Variant:
    """A more specific cause, chosen when ``pattern`` matches a line that
    already matched the rule."""

    pattern: str
    cause: str
    fixes: tuple[str, ...]


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    patterns: tuple[str, ...]
    explanation: str
    cause: str
    fixes: tuple[str, ...]
    verify: tuple[str, ...]
    priority: int = 50  # higher is reported first
    variants: tuple[Variant, ...] = ()
    # Drop this finding when any of these rules also matched: it is then only
    # a symptom of the more specific problem.
    symptom_of: tuple[str, ...] = ()
    # Lines matching this are never evidence for the rule. Used by the generic
    # log rules so that kubelet events are not mistaken for application logs.
    exclude: str = ""
    compiled: tuple[re.Pattern[str], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "compiled", tuple(re.compile(p, re.IGNORECASE) for p in self.patterns)
        )

    def matches(self, line: str) -> bool:
        if self.exclude and re.search(self.exclude, line, re.IGNORECASE):
            return False
        return any(p.search(line) for p in self.compiled)


# Kubelet/scheduler event text that the application-log rules must ignore.
_EVENT_LINE = r"probe failed|Failed to pull image|MountVolume|FailedScheduling|\bexec: "


RULES: tuple[Rule, ...] = (
    Rule(
        id="oom-killed",
        title="Container killed for exceeding its memory limit (OOMKilled)",
        priority=90,
        patterns=(r"\bOOMKilled\b", r"\bExit Code:\s+137\b", r"\bMemory cgroup out of memory\b"),
        explanation=(
            "The kernel killed the container because it used more memory than its "
            "limit allows (exit code 137 = SIGKILL). Kubernetes restarts it, and if "
            "it keeps happening the pod goes into CrashLoopBackOff."
        ),
        cause=(
            "The memory limit is lower than what the application needs under real "
            "load, or the application leaks memory."
        ),
        fixes=(
            "Raise the memory limit to fit real usage, e.g. "
            "`kubectl set resources deployment/<name> {ns} --limits=memory=512Mi` "
            "(or change resources.limits.memory in the manifest and re-apply).",
            "If usage grows without bound, look for a leak or cap the runtime heap "
            "(JVM -Xmx / MaxRAMPercentage, Node --max-old-space-size, GOMEMLIMIT).",
        ),
        verify=(
            "kubectl get pod {pod} {ns} -o jsonpath='{.status.containerStatuses[*].lastState.terminated.reason}'",
            "kubectl top pod {pod} {ns} --containers",
            "kubectl get pod {pod} {ns} -o jsonpath='{.spec.containers[*].resources}'",
        ),
    ),
    Rule(
        id="image-pull",
        title="Image cannot be pulled (ImagePullBackOff / ErrImagePull)",
        priority=85,
        patterns=(r"\bImagePullBackOff\b", r"\bErrImagePull\b", r"Failed to pull image"),
        explanation=(
            "The node could not download the container image, so the container "
            "never started. Kubernetes keeps retrying with an increasing delay."
        ),
        cause=(
            "Most often a typo in the image name or tag, a tag that was never "
            "pushed, or a private registry without valid pull credentials."
        ),
        fixes=(
            "Check the exact image reference in the pod spec and that the tag exists in the registry.",
            "For a private registry, create a pull secret and reference it in spec.imagePullSecrets.",
        ),
        verify=(
            "kubectl get pod {pod} {ns} -o jsonpath='{.spec.containers[*].image}'",
            "kubectl get pod {pod} {ns} -o jsonpath='{.spec.imagePullSecrets}'",
            "kubectl get events {ns} --field-selector involvedObject.name={pod}",
        ),
        variants=(
            Variant(
                r"manifest unknown|not found|does not exist|repository does not exist",
                "The registry says the image or tag does not exist: wrong name, wrong tag, "
                "or the image was never pushed.",
                (
                    "Fix the image name/tag in the workload spec, or push the missing tag. "
                    "Compare with what the registry actually lists.",
                ),
            ),
            Variant(
                r"unauthorized|authentication required|pull access denied|denied: requested access|no basic auth credentials|403 Forbidden",
                "The registry rejected the pull: credentials are missing, wrong or expired.",
                (
                    "Create or refresh the registry secret: `kubectl create secret docker-registry "
                    "regcred {ns} --docker-server=<registry> --docker-username=<user> "
                    "--docker-password=<token>`, then list it under spec.imagePullSecrets "
                    "(or attach it to the pod's service account).",
                ),
            ),
            Variant(
                r"no such host|i/o timeout|connection refused|TLS handshake timeout|x509:",
                "The node could not reach the registry (DNS, network, proxy or TLS trust problem).",
                (
                    "Check that nodes can resolve and reach the registry host, and that any "
                    "private CA is trusted by the container runtime.",
                ),
            ),
            Variant(
                r"toomanyrequests|rate limit",
                "The registry is rate-limiting anonymous pulls.",
                ("Authenticate pulls with an imagePullSecret or use a registry mirror/cache.",),
            ),
        ),
    ),
    Rule(
        id="invalid-image-name",
        title="Image reference is malformed (InvalidImageName)",
        priority=85,
        patterns=(r"\bInvalidImageName\b", r"couldn't parse image (reference|name)"),
        explanation="The image string in the pod spec is not a valid image reference, so no pull was attempted.",
        cause="Typically an upper-case letter, a stray space, a trailing colon or an unresolved template variable.",
        fixes=("Correct the image field in the workload spec; names must be lower case, `registry/repo:tag`.",),
        verify=("kubectl get pod {pod} {ns} -o jsonpath='{.spec.containers[*].image}'",),
    ),
    Rule(
        id="config-error",
        title="Container cannot be configured (CreateContainerConfigError)",
        priority=85,
        patterns=(
            r"\bCreateContainerConfigError\b",
            r"Error: (configmap|secret) \"[^\"]+\" not found",
            r"couldn't find key \S+ in (ConfigMap|Secret)",
        ),
        explanation=(
            "The kubelet could not build the container's configuration because "
            "something the pod spec refers to is missing."
        ),
        cause="A ConfigMap, Secret or key referenced by env, envFrom or a volume does not exist in this namespace.",
        fixes=(
            "Create the missing ConfigMap/Secret (or the missing key) in the pod's namespace, "
            "or fix the name in the pod spec. The pod starts on its own once it exists.",
        ),
        verify=(
            "kubectl get configmap,secret {ns}",
            "kubectl get pod {pod} {ns} -o jsonpath='{.spec.containers[*].envFrom}'",
            "kubectl describe pod {pod} {ns}",
        ),
    ),
    Rule(
        id="bad-command",
        title="Container command cannot be started",
        priority=85,
        patterns=(
            r"\b(RunContainerError|CreateContainerError|StartError)\b",
            r"executable file not found",
            r"exec format error",
            r"exec: \"[^\"]+\": (permission denied|stat )",
            r"no such file or directory: unknown",
            r"\bExit Code:\s+(126|127)\b",
        ),
        explanation="The container runtime created the container but could not execute its entrypoint.",
        cause="The command/args in the pod spec (or the image ENTRYPOINT) point at a binary that is missing or not executable.",
        fixes=(
            "Check `command` and `args` in the pod spec against what the image actually contains.",
        ),
        verify=(
            "kubectl get pod {pod} {ns} -o jsonpath='{.spec.containers[*].command} {.spec.containers[*].args}'",
            "kubectl describe pod {pod} {ns}",
        ),
        variants=(
            Variant(
                r"exec format error",
                "The image was built for a different CPU architecture than the node (for example arm64 image on an amd64 node).",
                ("Build and push a multi-arch image, or schedule the pod onto nodes of the matching architecture with a nodeSelector on kubernetes.io/arch.",),
            ),
            Variant(
                r"permission denied",
                "The entrypoint exists but is not executable by the container's user.",
                ("Make the file executable in the image (chmod +x) or adjust securityContext.runAsUser.",),
            ),
        ),
    ),
    Rule(
        id="crash-loop",
        title="Container keeps crashing after start (CrashLoopBackOff)",
        priority=70,
        symptom_of=("oom-killed", "bad-command"),
        patterns=(r"\bCrashLoopBackOff\b", r"Back-off restarting failed container"),
        explanation=(
            "The container starts, exits, and is restarted by Kubernetes with a growing "
            "delay between attempts. CrashLoopBackOff is the symptom; the reason is in "
            "the container's exit code and its logs from the previous run."
        ),
        cause=(
            "The application itself is exiting: a startup error, missing configuration "
            "or an unreachable dependency are the usual reasons."
        ),
        fixes=(
            "Read the logs of the crashed run (`--previous`) and fix the error they show.",
            "If the app needs longer to start than the liveness probe allows, add a startupProbe "
            "or raise initialDelaySeconds.",
        ),
        verify=(
            "kubectl logs {pod} {ns} --previous --tail=50",
            "kubectl get pod {pod} {ns} -o jsonpath='{.status.containerStatuses[*].lastState.terminated}'",
            "kubectl get pod {pod} {ns} -w",
        ),
    ),
    Rule(
        id="unschedulable",
        title="Pod cannot be scheduled onto any node (Pending / FailedScheduling)",
        priority=80,
        patterns=(r"\bFailedScheduling\b", r"\d+/\d+ nodes are available", r"\bUnschedulable\b"),
        explanation=(
            "The scheduler found no node that satisfies the pod's requirements, so the "
            "pod stays Pending and no container has been created."
        ),
        cause="No node currently fits the pod's resource requests or placement constraints.",
        fixes=(
            "Compare the pod's requests and constraints with what the nodes offer, then "
            "relax the constraint or add capacity.",
        ),
        verify=(
            "kubectl get events {ns} --field-selector reason=FailedScheduling",
            "kubectl describe nodes",
            "kubectl get pod {pod} {ns} -o jsonpath='{.spec.containers[*].resources.requests}'",
        ),
        variants=(
            Variant(
                r"Insufficient (cpu|memory|ephemeral-storage|[\w./-]+)",
                "No node has enough free capacity for the pod's resource requests.",
                (
                    "Lower resources.requests to what the app really needs, free capacity by "
                    "scaling down other workloads, or add nodes (check the cluster autoscaler limits).",
                ),
            ),
            Variant(
                r"untolerated taint|had taints? that the pod didn't tolerate",
                "Every candidate node has a taint that the pod does not tolerate.",
                (
                    "Add a matching toleration to the pod spec, or remove the taint from a node "
                    "that should accept this workload.",
                ),
            ),
            Variant(
                r"didn't match Pod's node affinity|node\(s\) didn't match (pod's )?node (affinity|selector)",
                "No node carries the labels required by the pod's nodeSelector or node affinity.",
                (
                    "Fix the nodeSelector/affinity in the pod spec, or label a suitable node. "
                    "Compare with `kubectl get nodes --show-labels`.",
                ),
            ),
            Variant(
                r"unbound immediate PersistentVolumeClaims|persistentvolumeclaim \"[^\"]+\" not found",
                "The pod waits for a PersistentVolumeClaim that is not bound (or does not exist).",
                (
                    "Check the claim with `kubectl get pvc {ns}`; create it, or fix its "
                    "storageClassName/size so a volume can be provisioned.",
                ),
            ),
            Variant(
                r"didn't match pod anti-affinity|didn't satisfy existing pods anti-affinity",
                "Pod anti-affinity rules exclude every node (often more replicas than nodes).",
                ("Reduce replicas, add nodes, or change the rule from required to preferred.",),
            ),
            Variant(
                r"didn't have free ports",
                "The requested hostPort is already in use on every candidate node.",
                ("Avoid hostPort where possible, or use a different port.",),
            ),
        ),
    ),
    Rule(
        id="liveness-probe",
        title="Liveness probe is failing, so the container is being restarted",
        priority=65,
        patterns=(r"Liveness probe failed", r"failed liveness probe, will be restarted"),
        explanation=(
            "The kubelet's health check on the container failed repeatedly, so the kubelet "
            "killed and restarted it. The application may be hung, or simply slower than the probe allows."
        ),
        cause="Wrong probe path/port, a probe timeout that is too tight, or an app that is slow to start or deadlocked.",
        fixes=(
            "Make sure the probe path and port match what the app serves.",
            "Give slow starters a startupProbe (or a larger initialDelaySeconds) and raise "
            "timeoutSeconds/failureThreshold if the endpoint is slow under load.",
        ),
        verify=(
            "kubectl get pod {pod} {ns} -o jsonpath='{.spec.containers[*].livenessProbe}'",
            "kubectl logs {pod} {ns} --previous --tail=50",
            "kubectl get events {ns} --field-selector involvedObject.name={pod},reason=Unhealthy",
        ),
    ),
    Rule(
        id="readiness-probe",
        title="Readiness probe is failing, so the pod receives no traffic",
        priority=60,
        patterns=(r"Readiness probe failed",),
        explanation=(
            "The pod is running but reports itself not ready, so it is removed from Service "
            "endpoints. The container is not restarted for this."
        ),
        cause="The app is not listening on the probed port/path yet, or a dependency it checks is unavailable.",
        fixes=(
            "Check that the probe path and port are correct and that the app's dependencies are reachable.",
            "If the app is just slow to warm up, raise initialDelaySeconds or add a startupProbe.",
        ),
        verify=(
            "kubectl get pod {pod} {ns} -o jsonpath='{.spec.containers[*].readinessProbe}'",
            "kubectl get pod {pod} {ns} -o wide",
            "kubectl logs {pod} {ns} --tail=50",
        ),
    ),
    Rule(
        id="startup-probe",
        title="Startup probe is failing, so the container is killed before it finishes starting",
        priority=65,
        patterns=(r"Startup probe failed",),
        explanation="The app did not pass its startup probe within failureThreshold x periodSeconds, so the kubelet restarted it.",
        cause="The app needs longer to start than the probe allows, or the probe targets the wrong path/port.",
        fixes=("Raise failureThreshold or periodSeconds on the startupProbe, or correct its path/port.",),
        verify=(
            "kubectl get pod {pod} {ns} -o jsonpath='{.spec.containers[*].startupProbe}'",
            "kubectl logs {pod} {ns} --previous --tail=50",
        ),
    ),
    Rule(
        id="volume-mount",
        title="A volume cannot be attached or mounted (FailedMount / FailedAttachVolume)",
        priority=75,
        patterns=(r"\bFailedMount\b", r"\bFailedAttachVolume\b", r"Unable to attach or mount volumes", r"MountVolume\.\w+ failed"),
        explanation="The pod is stuck in ContainerCreating because one of its volumes is not available on the node.",
        cause="A referenced Secret/ConfigMap/PVC is missing, or the storage backend could not attach the disk.",
        fixes=("Find which volume failed in the event message and fix its source.",),
        verify=(
            "kubectl get events {ns} --field-selector involvedObject.name={pod}",
            "kubectl get pvc {ns}",
            "kubectl get pod {pod} {ns} -o jsonpath='{.spec.volumes}'",
        ),
        variants=(
            Variant(
                r"MountVolume\.SetUp failed.*(secret|configmap) \"[^\"]+\" not found",
                "The volume's source Secret or ConfigMap does not exist in the pod's namespace.",
                ("Create the missing Secret/ConfigMap, or correct its name in spec.volumes.",),
            ),
            Variant(
                r"Multi-Attach error|already (exclusively )?attached|is already used by",
                "A ReadWriteOnce volume is still attached to another node or pod.",
                (
                    "Wait for the old pod to terminate (or remove it if it is stuck), or use the "
                    "Recreate rollout strategy for workloads with RWO volumes.",
                ),
            ),
            Variant(
                r"timed out waiting for the condition|timeout expired waiting for volumes",
                "The storage backend did not attach or mount the volume in time.",
                ("Check the CSI driver pods and the PVC/PV status; the node may be in a different zone than the disk.",),
            ),
        ),
    ),
    Rule(
        id="pvc-provisioning",
        title="PersistentVolumeClaim cannot be provisioned",
        priority=75,
        patterns=(r"\bProvisioningFailed\b", r"no persistent volumes available for this claim", r"storageclass\.storage\.k8s\.io \"[^\"]+\" not found"),
        explanation="The claim stays Pending because no volume could be created or matched for it.",
        cause="The storageClassName does not exist, there is no default StorageClass, or the provisioner failed.",
        fixes=("Set storageClassName to a class that exists, or install/mark a default StorageClass.",),
        verify=("kubectl get storageclass", "kubectl get pvc {ns}", "kubectl get events {ns} --field-selector reason=ProvisioningFailed"),
    ),
    Rule(
        id="evicted",
        title="Pod was evicted by the node",
        priority=80,
        patterns=(r"\bEvicted\b", r"The node was low on resource", r"The node had condition: \[?(Disk|Memory|PID)Pressure"),
        explanation="The kubelet removed the pod to protect the node, which was running out of memory, disk or PIDs.",
        cause="The node is overcommitted, or this pod uses far more than it requests (pods exceeding their requests are evicted first).",
        fixes=(
            "Set realistic resources.requests (and ephemeral-storage limits if the pod writes a lot to disk).",
            "Free or add node capacity; evicted pod objects can be cleaned up once understood.",
        ),
        verify=(
            "kubectl get pod {pod} {ns} -o jsonpath='{.status.message}'",
            "kubectl describe nodes",
            "kubectl top nodes",
        ),
    ),
    Rule(
        id="sandbox",
        title="Pod network sandbox cannot be created (FailedCreatePodSandBox)",
        priority=75,
        patterns=(r"\bFailedCreatePodSandBox\b", r"failed to set ?up (sandbox|network)", r"network plugin is not ready", r"cni plugin not initialized"),
        explanation="The container runtime could not set up networking for the pod, so no container started.",
        cause="The CNI plugin on that node is unhealthy or misconfigured, or the node ran out of pod IP addresses.",
        fixes=("Check the CNI daemonset pods on the affected node and the size of the pod CIDR / IP pool.",),
        verify=(
            "kubectl get pods -n kube-system -o wide",
            "kubectl get events {ns} --field-selector reason=FailedCreatePodSandBox",
            "kubectl get nodes",
        ),
    ),
    Rule(
        id="quota",
        title="Blocked by a ResourceQuota or LimitRange",
        priority=75,
        patterns=(r"exceeded quota", r"forbidden: (minimum|maximum) (cpu|memory) usage per", r"failed quota:"),
        explanation="The API server refused to create the pod because the namespace would go over its quota or violate a LimitRange.",
        cause="The namespace quota is used up, or the pod sets no requests/limits where the quota requires them.",
        fixes=("Lower the pod's requests/limits or replicas, or have the quota raised.",),
        verify=("kubectl describe resourcequota {ns}", "kubectl describe limitrange {ns}"),
    ),
    Rule(
        id="rbac",
        title="Kubernetes API request denied by RBAC",
        priority=70,
        patterns=(r"is forbidden: User \"[^\"]+\" cannot \w+", r"forbidden: User \S+ cannot"),
        explanation="A user or service account called the Kubernetes API without permission for that verb and resource.",
        cause="The service account the pod runs as has no Role/ClusterRole binding granting this access.",
        fixes=("Bind a Role that grants exactly the verb and resource named in the message to that service account.",),
        verify=(
            "kubectl auth can-i <verb> <resource> --as=system:serviceaccount:<namespace>:<serviceaccount> {ns}",
            "kubectl get rolebindings,clusterrolebindings -A -o wide",
        ),
    ),
    Rule(
        id="node-not-ready",
        title="Node is NotReady",
        priority=80,
        patterns=(r"\bNodeNotReady\b", r"Node is not ready", r"node\.kubernetes\.io/(not-ready|unreachable)", r"Kubelet stopped posting node status"),
        explanation="The control plane lost contact with a node's kubelet; pods on it are stuck or being rescheduled.",
        cause="The node is down, out of resources, or cut off from the control plane.",
        fixes=("Investigate the node itself (kubelet, container runtime, disk, network); drain and replace it if it does not recover.",),
        verify=("kubectl get nodes", "kubectl describe nodes", "kubectl get pods -A -o wide --field-selector status.phase!=Running"),
    ),
    Rule(
        id="log-connection-refused",
        title="Application cannot connect to a dependency",
        priority=40,
        exclude=_EVENT_LINE,
        patterns=(r"connection refused", r"ECONNREFUSED", r"dial tcp \S+: (connect: )?(i/o timeout|no route to host)"),
        explanation="The app's logs show failed outbound connections: nothing is accepting connections at the target address.",
        cause="The target Service has no ready endpoints, the host/port is wrong, or a NetworkPolicy blocks the traffic.",
        fixes=("Check the address in the app's configuration and that the target workload is running and ready.",),
        verify=("kubectl get svc,endpoints {ns}", "kubectl get pods {ns} -o wide", "kubectl get networkpolicy {ns}"),
    ),
    Rule(
        id="log-dns",
        title="Application cannot resolve a hostname",
        priority=40,
        exclude=_EVENT_LINE,
        patterns=(r"no such host", r"\bENOTFOUND\b", r"Temporary failure in name resolution", r"Name or service not known", r"\bNXDOMAIN\b"),
        explanation="The app's logs show DNS lookups failing.",
        cause="A misspelled Service name, a missing namespace suffix (`svc.namespace`), or unhealthy cluster DNS.",
        fixes=("Use the full name `<service>.<namespace>.svc.cluster.local` for cross-namespace calls and confirm the Service exists.",),
        verify=("kubectl get svc -A", "kubectl get pods -n kube-system -l k8s-app=kube-dns", "kubectl get pod {pod} {ns} -o jsonpath='{.spec.dnsPolicy}'"),
    ),
    Rule(
        id="log-permission-denied",
        title="Application hit a filesystem permission error",
        priority=35,
        exclude=_EVENT_LINE,
        patterns=(r"permission denied", r"\bEACCES\b", r"read-only file system"),
        explanation="The app's logs show it cannot read or write a path inside the container.",
        cause="The container runs as a non-root user that does not own the path, or the root filesystem is read-only.",
        fixes=("Set securityContext.fsGroup/runAsUser to match the volume's ownership, or mount a writable emptyDir at that path.",),
        verify=("kubectl get pod {pod} {ns} -o jsonpath='{.spec.securityContext} {.spec.containers[*].securityContext}'",),
    ),
)

#: What common container exit codes mean, shown alongside crash findings.
EXIT_CODES = {
    0: "the process exited normally; a long-running container should not exit, check its command",
    1: "generic application error; the logs of the previous run have the reason",
    2: "misuse of a shell builtin or bad command-line arguments",
    126: "the command was found but is not executable",
    127: "the command was not found in the image",
    134: "the process aborted (SIGABRT)",
    137: "killed with SIGKILL, usually by the out-of-memory killer",
    139: "segmentation fault (SIGSEGV)",
    143: "terminated with SIGTERM, e.g. after a failed liveness probe or during shutdown",
}
