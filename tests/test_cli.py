import io
import json
from pathlib import Path

from k8s_explain.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def run(argv, stdin_text=""):
    out = io.StringIO()
    code = main(argv, stdin=io.StringIO(stdin_text), stdout=out)
    return code, out.getvalue()


def test_stdin_text_report():
    code, out = run([], (EXAMPLES / "describe-oomkilled.txt").read_text())
    assert code == 0
    assert out.startswith("1 problem found (pod api-7d9f5c8b46-x2k4q, namespace shop)")
    assert "1. Container killed for exceeding its memory limit (OOMKilled)" in out
    assert "$ kubectl top pod api-7d9f5c8b46-x2k4q -n shop --containers" in out
    assert out.rstrip().endswith("It has not contacted or changed your cluster.")


def test_file_argument_and_json():
    code, out = run([str(EXAMPLES / "events.txt"), "--json"])
    assert code == 0
    data = json.loads(out)
    assert data["pod"] == "checkout-6b8d9c7f5d-p4w8z"
    assert [f["id"] for f in data["findings"]] == ["volume-mount", "liveness-probe", "readiness-probe"]
    assert set(data["findings"][0]) == {
        "id", "title", "explanation", "causes", "fixes", "verify", "evidence", "priority",
    }


def test_exit_code_flag():
    assert run(["--exit-code"], "Reason: CrashLoopBackOff")[0] == 1
    assert run(["--exit-code"], "Status: Running")[0] == 0
    assert run([], "Reason: CrashLoopBackOff")[0] == 0


def test_no_findings_message():
    code, out = run([], "Status: Running\n")
    assert code == 0
    assert out.startswith("No known problem pattern found")


def test_list_rules():
    code, out = run(["--list-rules"])
    assert code == 0
    assert "oom-killed" in out and "unschedulable" in out


def test_empty_input_and_missing_file(capsys):
    assert run([], "  \n")[0] == 2
    assert "empty" in capsys.readouterr().err
    assert run(["/nonexistent/describe.txt"])[0] == 2
