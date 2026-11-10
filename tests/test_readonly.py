"""The tool promises to be read-only. These tests hold it to that."""

import ast
from pathlib import Path

import k8s_explain
from k8s_explain.rules import RULES

READ_ONLY_VERBS = {"get", "describe", "logs", "top", "auth", "explain", "version"}
PACKAGE = Path(k8s_explain.__file__).parent


def test_rule_ids_are_unique_and_references_resolve():
    ids = [rule.id for rule in RULES]
    assert len(ids) == len(set(ids))
    for rule in RULES:
        assert set(rule.symptom_of) <= set(ids)
        assert rule.fixes and rule.verify and rule.patterns


def test_every_verify_command_is_a_read_only_kubectl_verb():
    for rule in RULES:
        for command in rule.verify:
            words = command.split()
            assert words[0] == "kubectl", command
            assert words[1] in READ_ONLY_VERBS, command
            if words[1] == "auth":
                assert words[2] == "can-i", command


def test_package_cannot_run_commands_or_open_connections():
    banned = {"subprocess", "socket", "http", "urllib", "requests", "kubernetes", "os"}
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] not in banned, f"{path.name} imports {name}"
