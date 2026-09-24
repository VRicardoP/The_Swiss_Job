"""Real nginx/Docker test, without data or published ports.

Run: python3 scripts/test_frontend_proxy_isolation.py IMAGE
The old image must fail because both backends advertise the alias `backend`.
"""

from pathlib import Path
import subprocess
import sys
import uuid


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main(image):
    network = "proxy-isolation-" + uuid.uuid4().hex[:12]
    containers = []
    fixture = Path(__file__).parent / "fixtures/proxy_backend.conf.template"
    docker("network", "create", "--internal", network)
    try:
        for name, marker in (
            ("swissjob-backend", "production"),
            ("swissjob-backend-r5", "rehearsal"),
        ):
            containers.append(
                docker(
                    "run",
                    "-d",
                    "--network",
                    network,
                    "--network-alias",
                    "backend",
                    "--network-alias",
                    name,
                    "-e",
                    "PROXY_TEST_MARKER=" + marker,
                    "-v",
                    str(fixture.resolve())
                    + ":/etc/nginx/templates/default.conf.template:ro",
                    image,
                )
            )
        for host, expected in (
            ("swissjob-backend", "production"),
            ("swissjob-backend-r5", "rehearsal"),
        ):
            frontend = docker(
                "run",
                "-d",
                "--network",
                network,
                "-e",
                "SWISSJOB_BACKEND_HOST=" + host,
                image,
            )
            containers.append(frontend)
            docker("exec", frontend, "nginx", "-t")
            observed = [
                docker(
                    "exec",
                    frontend,
                    "wget",
                    "-q",
                    "-O",
                    "-",
                    "http://127.0.0.1/api/probe",
                )
                for _ in range(30)
            ]
            assert set(observed) == {expected}, (host, sorted(set(observed)))
        print(
            "PASS: 30/30 production + 30/30 rehearsal, shared ambiguous alias ignored"
        )
    finally:
        for container in reversed(containers):
            subprocess.run(
                ["docker", "rm", "-f", container],
                check=False,
                stdout=subprocess.DEVNULL,
            )
        subprocess.run(
            ["docker", "network", "rm", network], check=True, stdout=subprocess.DEVNULL
        )


if __name__ == "__main__":
    main(sys.argv[1])
