from pathlib import Path

import pytest

from k8s_explain.engine import diagnose, extract_context

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def ids(text):
    return [finding.id for finding in diagnose(text)[0]]


def example(name):
    return (EXAMPLES / name).read_text()


def test_oomkilled_is_reported_instead_of_generic_crashloop():
    findings, ctx = diagnose(example("describe-oomkilled.txt"))
    assert [f.id for f in findings] == ["oom-killed"]
    assert ctx.pod == "api-7d9f5c8b46-x2k4q"
    assert ctx.namespace == "shop"
    assert "Reason: OOMKilled" in findings[0].evidence
    assert findings[0].verify[1] == "kubectl top pod api-7d9f5c8b46-x2k4q -n shop --containers"


def test_plain_crashloop_explains_exit_code():
    text = """Name:         worker-1
Namespace:    jobs
Containers:
  worker:
    State:          Waiting
      Reason:       CrashLoopBackOff
    Last State:     Terminated
      Reason:       Error
      Exit Code:    1
"""
    findings, _ = diagnose(text)
    assert [f.id for f in findings] == ["crash-loop"]
    assert any(cause.startswith("Exit code 1:") for cause in findings[0].causes)
    assert "kubectl logs worker-1 -n jobs --previous --tail=50" in findings[0].verify


def test_image_pull_auth_variant():
    findings, _ = diagnose(example("describe-imagepull.txt"))
    assert [f.id for f in findings] == ["image-pull"]
    assert "credentials" in findings[0].causes[0]
    assert len(findings[0].causes) == 1


@pytest.mark.parametrize(
    "message, expected",
    [
        ("manifest for nginx:1.99 not found: manifest unknown", "does not exist"),
        ("dial tcp: lookup registry.internal: no such host", "could not reach the registry"),
        ("toomanyrequests: You have reached your pull rate limit", "rate-limiting"),
    ],
)
def test_image_pull_other_variants(message, expected):
    findings, _ = diagnose(f'Warning Failed kubelet Failed to pull image "x": {message}')
    assert [f.id for f in findings] == ["image-pull"]
    assert expected in findings[0].causes[0]


def test_image_pull_without_detail_uses_general_cause():
    findings, _ = diagnose("web-1   0/1   ImagePullBackOff   0   2m")
    assert findings[0].causes[0].startswith("Most often a typo")


def test_unschedulable_reports_every_reason_in_the_message():
    findings, ctx = diagnose(example("describe-pending.txt"))
    assert [f.id for f in findings] == ["unschedulable"]
    causes = " ".join(findings[0].causes)
    assert "enough free capacity" in causes
    assert "taint" in causes
    assert "nodeSelector" in causes
    assert "PersistentVolumeClaim" not in causes
    assert ctx.pod == "batch-report-28594320-7xq9d"


def test_events_table_finds_mount_and_probe_problems_in_priority_order():
    findings, ctx = diagnose(example("events.txt"))
    assert [f.id for f in findings] == ["volume-mount", "liveness-probe", "readiness-probe"]
    assert "Secret or ConfigMap does not exist" in findings[0].causes[0]
    assert ctx.pod == "checkout-6b8d9c7f5d-p4w8z"
    assert ctx.namespace == "shop"


def test_application_log_rules():
    assert ids(example("app.log")) == ["log-dns"]
    assert ids("ERROR dial tcp 10.0.0.5:5432: connect: connection refused") == ["log-connection-refused"]
    assert ids("open /data/cache: permission denied") == ["log-permission-denied"]


@pytest.mark.parametrize(
    "text, rule_id",
    [
        ('Error: configmap "app-config" not found', "config-error"),
        ("Reason:  CreateContainerConfigError", "config-error"),
        ('exec: "/app/run.sh": executable file not found in $PATH', "bad-command"),
        ("standard_init_linux.go:228: exec user process caused: exec format error", "bad-command"),
        ("Reason:  InvalidImageName", "invalid-image-name"),
        ("Warning Unhealthy kubelet Startup probe failed: HTTP probe failed with statuscode: 500", "startup-probe"),
        ("Status: Failed\nReason: Evicted\nMessage: The node was low on resource: memory.", "evicted"),
        ("Warning FailedCreatePodSandBox kubelet Failed to create pod sandbox: rpc error", "sandbox"),
        ('pods "api-1" is forbidden: exceeded quota: compute, requested: memory=2Gi', "quota"),
        ('pods is forbidden: User "system:serviceaccount:shop:api" cannot list resource "pods"', "rbac"),
        ("Warning NodeNotReady node-controller Node is not ready", "node-not-ready"),
        ('Warning ProvisioningFailed storageclass.storage.k8s.io "fast" not found', "pvc-provisioning"),
        ('Warning FailedAttachVolume Multi-Attach error for volume "pvc-1"', "volume-mount"),
    ],
)
def test_each_rule_fires_on_its_message(text, rule_id):
    assert ids(text) == [rule_id]


def test_exec_format_error_names_architecture():
    findings, _ = diagnose("exec /usr/local/bin/app: exec format error")
    assert "architecture" in findings[0].causes[0]


def test_healthy_output_has_no_findings():
    text = """Name:         ok-1
Namespace:    default
Status:       Running
Containers:
  app:
    State:          Running
    Ready:          True
    Restart Count:  0
Events:  <none>
"""
    findings, ctx = diagnose(text)
    assert findings == []
    assert ctx.pod == "ok-1"


def test_placeholders_when_names_are_unknown():
    findings, ctx = diagnose("Back-off restarting failed container")
    assert ctx.pod is None and ctx.namespace is None
    assert findings[0].verify[0] == "kubectl logs <pod> -n <namespace> --previous --tail=50"


def test_context_ignores_ambiguous_event_tables():
    ctx = extract_context("1m Warning BackOff pod/a-1 x\n1m Warning BackOff pod/b-2 y\n")
    assert ctx.pod is None


def test_name_of_non_pod_describe_is_not_used_as_pod():
    ctx = extract_context("Name:   my-deployment\nNamespace:  prod\nReplicas:  3 desired\n")
    assert ctx.pod is None
    assert ctx.namespace == "prod"
