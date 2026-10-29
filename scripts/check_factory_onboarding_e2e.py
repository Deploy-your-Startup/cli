"""Exercise public Factory setup and bootstrap cancellation with real local tools.

Run with the installed candidate CLI: uv run --extra dev python scripts/check_factory_onboarding_e2e.py.
Only GitHub identity is substituted; no cloud resources or repositories are created.
"""

import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from cli.factory_commands import FACTORY_TEMPLATE, FACTORY_VERSION


def main():
    required = ("mise", "uv", "yq", "git", "gh")
    missing = [tool for tool in required if not shutil.which(tool)]
    if missing:
        raise SystemExit("Install E2E prerequisites: " + ", ".join(missing))
    runtime_bin = Path(sys.executable).parent
    with tempfile.TemporaryDirectory(prefix="factory-onboarding-e2e-") as temporary:
        root = Path(temporary)
        home, binaries, factory = root / "home", root / "bin", root / "my factory"
        (home / ".codex").mkdir(parents=True)
        binaries.mkdir()
        gh = binaries / "gh"
        gh.write_text(
            '#!/bin/sh\ncase "$*" in\n'
            "  *--version*) echo 'gh identity boundary' ;;\n"
            "  *api*user*) echo example-owner ;;\n"
            '  skill\\ install*|skills\\ install*) exec "$STARTUP_E2E_REAL_GH" "$@" ;;\n'
            "  *) echo 'Unexpected GitHub mutation' >&2; exit 99 ;;\nesac\n"
        )
        gh.chmod(0o755)
        environment = {
            **os.environ,
            "HOME": str(home),
            "PATH": os.pathsep.join(
                (str(binaries), str(runtime_bin), os.environ["PATH"])
            ),
            "MISE_DATA_DIR": os.environ.get(
                "MISE_DATA_DIR", str(Path.home() / ".local/share/mise")
            ),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "NO_COLOR": "1",
            "GH_TOKEN": "",
            "GITHUB_TOKEN": "",
            "STARTUP_E2E_REAL_GH": shutil.which("gh") or "",
        }
        # GIVEN a new home and the real published Factory source and runtimes.
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "cli.startup",
                "factory",
                "onboard",
                "--project-dir",
                str(factory),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
        )
        assert process.stdout is not None and process.stdin is not None
        output = bytearray()
        steps = [
            (b"Project name (for example, my-startup)", b"example-app\n"),
            (b"Which stack would you like to start with?", b"3\n"),
            (b"Domain (for example, example.com)", b"app.example.com\n"),
            (b"Create the repository under example-owner/example-app?", b"y\n"),
            (b"Create this project and start setup?", b"n\n"),
        ]
        try:
            deadline = time.monotonic() + 240
            # WHEN setup completes, choose a landing page and decline its launch plan.
            while time.monotonic() < deadline:
                if select.select([process.stdout], [], [], 0.2)[0]:
                    chunk = os.read(process.stdout.fileno(), 8192)
                    if not chunk:
                        break
                    output.extend(chunk)
                    if steps and steps[0][0] in output:
                        _, answer = steps.pop(0)
                        process.stdin.write(answer)
                        process.stdin.flush()
            else:
                raise AssertionError("Factory onboarding timed out")
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            process.stdout.close()
            process.stdin.close()
            print(output.decode())
        text = output.decode()
        # THEN the neutral workspace exists, setup cloned all public tooling and cancellation creates no startup.
        assert process.returncode == 0 and not steps
        assert "Cancelled. No resources were created." in text
        assert str(factory / "projects/startups/example-app") in text
        registry = yaml.safe_load((factory / "projects.yaml").read_text())
        assert registry["projects"] == []
        for entry in registry["tooling"]:
            assert (factory / "projects/tooling" / entry["name"] / ".git").is_dir()
        answers = yaml.safe_load((factory / ".copier-answers.yml").read_text())
        # The CLI's own pin, so a reviewed template bump needs no second edit here.
        assert answers["_commit"] == FACTORY_VERSION
        assert answers["_src_path"] == FACTORY_TEMPLATE
        assert (home / ".agents/skills/deploy-your-startup/SKILL.md").is_file()
        assert not (factory / "projects/startups/example-app").exists()
        assert "Sprowi (agent loop, beta) is not set up" in text
        # A fresh CLI process discovers the saved folder and resumes real setup.
        for options in (["status"], ["init", "--resume"]):
            result = subprocess.run(
                [sys.executable, "-m", "cli.startup", "factory", *options],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert str(factory) in result.stdout
        print(
            "Factory onboarding E2E passed: public source, real setup, startup plan and cancellation."
        )


if __name__ == "__main__":
    main()
