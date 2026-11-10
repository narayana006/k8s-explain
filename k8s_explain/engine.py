"""Match kubectl text against the rules and build findings."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .rules import EXIT_CODES, RULES, Rule

_NAME = r"[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?"

#: Evidence lines shown per finding.
MAX_EVIDENCE = 3


@dataclass
class Context:
    """Names found in the input, used to fill in the suggested commands."""

    pod: str | None = None
    namespace: str | None = None
    exit_codes: list[int] = field(default_factory=list)


@dataclass
class Finding:
    id: str
    title: str
    explanation: str
    causes: list[str]
    fixes: list[str]
    verify: list[str]
    evidence: list[str]
    priority: int


def extract_context(text: str) -> Context:
    """Pull the pod name, namespace and exit codes out of kubectl text."""
    ctx = Context()
    is_pod_describe = re.search(r"^(Containers|Init Containers):\s*$", text, re.MULTILINE)
    name = re.search(rf"^Name:\s+({_NAME})\s*$", text, re.MULTILINE)
    if name and is_pod_describe:
        ctx.pod = name.group(1)
    else:
        # `kubectl get events` rows: use the pod only if they all name the same one.
        pods = set(re.findall(rf"\bpod/({_NAME})", text, re.IGNORECASE))
        if len(pods) == 1:
            ctx.pod = pods.pop()
    namespace = re.search(rf"^Namespace:\s+({_NAME})\s*$", text, re.MULTILINE)
    if namespace:
        ctx.namespace = namespace.group(1)
    else:
        # Scheduler events read "Successfully assigned <namespace>/<pod> to <node>".
        assigned = set(re.findall(rf"Successfully assigned ({_NAME})/{_NAME} to ", text))
        if len(assigned) == 1:
            ctx.namespace = assigned.pop()
    for code in re.findall(r"Exit Code:\s+(\d+)", text):
        if int(code) not in ctx.exit_codes:
            ctx.exit_codes.append(int(code))
    return ctx


def _fill(command: str, ctx: Context) -> str:
    ns = f"-n {ctx.namespace}" if ctx.namespace else "-n <namespace>"
    return command.replace("{pod}", ctx.pod or "<pod>").replace("{ns}", ns)


def _matching_lines(rule: Rule, lines: list[str]) -> list[str]:
    """Distinct lines that match the rule, with whitespace collapsed."""
    found: list[str] = []
    for line in lines:
        if rule.matches(line):
            cleaned = " ".join(line.split())
            if cleaned not in found:
                found.append(cleaned)
    return found


def diagnose(text: str) -> tuple[list[Finding], Context]:
    """Return findings for ``text``, most important first, and the context."""
    ctx = extract_context(text)
    lines = text.splitlines()
    matched: dict[str, tuple[Rule, list[str]]] = {}
    for rule in RULES:
        hits = _matching_lines(rule, lines)
        if hits:
            matched[rule.id] = (rule, hits)

    findings: list[Finding] = []
    for rule, hits in matched.values():
        if any(other in matched for other in rule.symptom_of):
            continue
        variants = [
            v
            for v in rule.variants
            if any(re.search(v.pattern, hit, re.IGNORECASE) for hit in hits)
        ]
        if variants:
            causes = [v.cause for v in variants]
            fixes = [fix for v in variants for fix in v.fixes]
        else:
            causes = [rule.cause]
            fixes = list(rule.fixes)
        if rule.id == "crash-loop":
            for code in ctx.exit_codes:
                if code in EXIT_CODES:
                    causes.append(f"Exit code {code}: {EXIT_CODES[code]}.")
        findings.append(
            Finding(
                id=rule.id,
                title=rule.title,
                explanation=rule.explanation,
                causes=causes,
                fixes=[_fill(f, ctx) for f in fixes],
                verify=[_fill(v, ctx) for v in rule.verify],
                evidence=hits[:MAX_EVIDENCE],
                priority=rule.priority,
            )
        )
    findings.sort(key=lambda f: -f.priority)
    return findings, ctx
