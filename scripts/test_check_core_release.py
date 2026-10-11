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

    assert failures == [
        "core-capture: el compose aún declara la captura retirada"
    ]


def test_rejects_capture_missing_before_retirement(monkeypatch):
    _fake_runtime(monkeypatch, check.SERVICIOS_OBLIGATORIOS)

    failures, _ = check._revisa_imagenes(capture_retirado=False)

    assert failures == ["core-capture: el compose no declara imagen"]
