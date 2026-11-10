"""Command-line entry point. Reads text, prints a diagnosis, runs nothing."""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from dataclasses import asdict
from typing import Sequence

from . import __version__
from .engine import Context, Finding, diagnose
from .rules import RULES

_WIDTH = 88


def _wrap(text: str, indent: str, first: str | None = None) -> str:
    return textwrap.fill(
        text,
        width=_WIDTH,
        initial_indent=indent if first is None else first,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def render_text(findings: list[Finding], ctx: Context) -> str:
    out: list[str] = []
    if not findings:
        out.append("No known problem pattern found in the input.")
        out.append(
            "k8s-explain recognises the issues listed by `k8s-explain --list-rules`. "
            "Try piping in `kubectl describe pod <pod>` or `kubectl get events`."
        )
        return "\n".join(out)

    target = []
    if ctx.pod:
        target.append(f"pod {ctx.pod}")
    if ctx.namespace:
        target.append(f"namespace {ctx.namespace}")
    count = f"{len(findings)} problem{'s' if len(findings) != 1 else ''} found"
    out.append(f"{count} ({', '.join(target)})" if target else count)

    for number, finding in enumerate(findings, 1):
        out.append("")
        out.append(f"{number}. {finding.title}")
        out.append("")
        out.append("   Evidence:")
        out.extend(f"     > {line}" for line in finding.evidence)
        out.append("")
        out.append("   What is happening:")
        out.append(_wrap(finding.explanation, "     "))
        out.append("")
        out.append("   Likely cause:")
        out.extend(_wrap(cause, "       ", "     - ") for cause in finding.causes)
        out.append("")
        out.append("   Likely fix:")
        out.extend(_wrap(fix, "       ", "     - ") for fix in finding.fixes)
        out.append("")
        out.append("   Verify (read-only):")
        out.extend(f"     $ {command}" for command in finding.verify)
    out.append("")
    out.append("k8s-explain only reads text. It has not contacted or changed your cluster.")
    return "\n".join(out)


def render_json(findings: list[Finding], ctx: Context) -> str:
    return json.dumps(
        {
            "pod": ctx.pod,
            "namespace": ctx.namespace,
            "findings": [asdict(finding) for finding in findings],
        },
        indent=2,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="k8s-explain",
        description="Explain kubectl describe, events or log output read from stdin "
        "(or a file). Read-only: no command is ever executed.",
    )
    parser.add_argument("file", nargs="?", help="file to read instead of stdin")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.add_argument(
        "--exit-code", action="store_true", help="exit with status 1 when a problem is found"
    )
    parser.add_argument("--list-rules", action="store_true", help="list the problems the tool recognises")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None, stdin=None, stdout=None) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_rules:
        for rule in sorted(RULES, key=lambda r: r.id):
            print(f"{rule.id:<24} {rule.title}", file=stdout)
        return 0

    if args.file and args.file != "-":
        try:
            with open(args.file, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as exc:
            print(f"k8s-explain: {exc}", file=sys.stderr)
            return 2
    else:
        if stdin.isatty():
            parser.error("no input: pipe kubectl output in, e.g. kubectl describe pod <pod> | k8s-explain")
        text = stdin.read()

    if not text.strip():
        print("k8s-explain: input is empty", file=sys.stderr)
        return 2

    findings, ctx = diagnose(text)
    render = render_json if args.json else render_text
    print(render(findings, ctx), file=stdout)
    return 1 if (args.exit_code and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
