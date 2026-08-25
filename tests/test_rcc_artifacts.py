import json

from jat.process import ProcessResult
from jat.rcc_artifacts import RCCArtifactAdapter


class RecordingRunner:
    def __init__(self, responses):
        self.calls = []
        self.responses = list(responses)

    def run(self, argv, timeout=None, foreground=False, secrets=()):
        self.calls.append((argv, timeout, foreground, secrets))
        return self.responses.pop(0)


def result(argv=(), stdout="", exit_status=0, stderr=""):
    return ProcessResult(argv=list(argv), stdout=stdout, stderr=stderr, exit_status=exit_status)


def test_rcc_adapter_uses_released_json_commands_and_returns_metadata(tmp_path):
    source = tmp_path / "robot.yaml"
    source.write_text("tasks: {}\n")
    archive = tmp_path / "environment.rcca"
    runner = RecordingRunner(
        [
            result(stdout="rcc v18.19.1\n"),
            result(stdout=json.dumps({"artifact": "sha256:" + "a" * 64})),
            result(stdout="published\n"),
            result(stdout=json.dumps({"artifact": "sha256:" + "a" * 64, "archive": str(archive)})),
            result(stdout="rcc v18.19.1\n"),
            result(stdout="variables\n"),
        ]
    )
    adapter = RCCArtifactAdapter(runner, executable="/tools/rcc")

    metadata = adapter.publish_and_export(source, archive)
    acquired = adapter.acquire(archive)
    adapter.verify(source)

    assert metadata.artifact == "sha256:" + "a" * 64
    assert acquired.artifact == metadata.artifact
    assert [call[0] for call in runner.calls] == [
        ["/tools/rcc", "version"],
            ["/tools/rcc", "env", "publish", "--environment", str(source), "--provider", "local", "--json"],
        ["/tools/rcc", "env", "export", "--artifact", metadata.artifact, "--provider", "local", "--output", str(archive)],
        ["/tools/rcc", "env", "acquire", "--archive", str(archive), "--permissive-local", "--json"],
        ["/tools/rcc", "version"],
        ["/tools/rcc", "--no-build", "ht", "vars", "--robot", str(source)],
    ]
