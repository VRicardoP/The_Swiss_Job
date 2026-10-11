import scripts.check_core_release as check


def _fake_runtime(monkeypatch, names):
    services = {
        name: {"image": f"image:{name}", "container_name": f"container-{name}"}
        for name in names
    }
    monkeypatch.setattr(
        check,
        "_compose_json",
        lambda *args: {"services": services},
    )
    monkeypatch.setattr(check, "_id_de_imagen", lambda reference: "sha256:same")
    monkeypatch.setattr(check, "_id_de_contenedor", lambda name: "sha256:same")


def test_requires_capture_before_retirement(monkeypatch):
    _fake_runtime(
        monkeypatch,
        (*check.SERVICIOS_OBLIGATORIOS, check.SERVICIO_CAPTURE),
    )

    failures, count = check._revisa_imagenes(capture_retirado=False)

    assert failures == []
    assert count == 5


def test_accepts_exact_four_service_topology_after_retirement(monkeypatch):
    _fake_runtime(monkeypatch, check.SERVICIOS_OBLIGATORIOS)

    failures, count = check._revisa_imagenes(capture_retirado=True)

    assert failures == []
    assert count == 4


def test_rejects_capture_still_declared_after_retirement(monkeypatch):
    _fake_runtime(
        monkeypatch,
        (*check.SERVICIOS_OBLIGATORIOS, check.SERVICIO_CAPTURE),
    )

    failures, _ = check._revisa_imagenes(capture_retirado=True)

    assert failures == ["core-capture: el compose aún declara la captura retirada"]


def test_rejects_capture_missing_before_retirement(monkeypatch):
    _fake_runtime(monkeypatch, check.SERVICIOS_OBLIGATORIOS)

    failures, _ = check._revisa_imagenes(capture_retirado=False)

    assert failures == ["core-capture: el compose no declara imagen"]


def test_remote_compose_uses_standalone_binary_and_live_file(monkeypatch):
    called = []

    def run(command, **kwargs):
        called.append(command)
        return check.subprocess.CompletedProcess(command, 0, '{"services": {}}', "")

    monkeypatch.setattr(check, "SSH_HOST", "nas")
    monkeypatch.setattr(check, "COMPOSE_BIN", "/qnap/docker-compose")
    monkeypatch.setattr(check, "COMPOSE_FILE", "/deploy/core.configured.yml")
    monkeypatch.setattr(check, "COMPOSE_PROJECT_NAME", "swissjob-r5")
    monkeypatch.setattr(check.subprocess, "run", run)

    assert check._compose_json("config", "--format", "json") == {"services": {}}
    assert called == [
        [
            "ssh",
            "nas",
            "/qnap/docker-compose -p swissjob-r5 -f /deploy/core.configured.yml config --format json",
        ]
    ]


def test_remote_ready_is_fetched_inside_api_container(monkeypatch):
    called = []

    def run(command):
        called.append(command)
        return check.subprocess.CompletedProcess(
            command,
            0,
            '{"status":"ready","release":"abc","authoritative":true}',
            "",
        )

    monkeypatch.setattr(check, "SSH_HOST", "nas")
    monkeypatch.setattr(check, "DOCKER_BIN", "/qnap/docker")
    monkeypatch.setattr(check, "READY_CONTAINER", "core-api")
    monkeypatch.setattr(check, "REMOTE_READY_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(check, "_run", run)

    assert check._ready_json("http://unused")["status"] == "ready"
    assert called[0][:5] == [
        "/qnap/docker",
        "exec",
        "core-api",
        "python",
        "-c",
    ]
    assert "http://127.0.0.1:8000/v1/ready" in called[0][5]
