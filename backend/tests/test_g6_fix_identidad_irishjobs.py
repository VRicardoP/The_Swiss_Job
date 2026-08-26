"""G6/P2-3 — la deriva de identidad NO se limitaba a arbeitnow/jobgether.

El diagnóstico de G3/P3-13 se acotó a dos providers **por inspección, no por
medición**. La alarma `find_same_source_clone` que G5 construyó para vigilar esas
dos fuentes es el primer instrumento que MIDE el fenómeno, y lo encuentra también
en `irishjobs`: 89 grupos con descripción idéntica byte a byte sobre 919 filas
activas, de los que 38 son reediciones del SLUG conservando el `-job<id>`.

Y de paso refuta el docstring del scraper, que afirmaba que los dos hosts
«comparten el mismo `id` de oferta»: medido en producción, los ids presentes en
los DOS hosts son **0**, y las 43 descripciones idénticas que aparecen en ambos
hosts tienen las 43 un id distinto.
"""

from scrapers.irishjobs import IrishJobsScraper, canonical_identity_url

_IJ = "https://www.irishjobs.ie"
_JI = "https://www.jobs.ie"


class TestIdentidadEstableFrenteAlSlug:
    """La reedición del título en la URL ya no crea un clon."""

    def test_dos_slugs_con_el_mismo_id_dan_la_MISMA_identidad(self):
        """El caso real: '…/lead-ms-fabric-architect/…' → '…/lead-architect/…'."""
        antes = f"{_IJ}/job/lead-ms-fabric-architect/ntt-data-services-inc-job107803777"
        despues = f"{_IJ}/job/lead-architect/ntt-data-services-inc-job107803777"
        assert canonical_identity_url(antes) == canonical_identity_url(despues)

        scraper = IrishJobsScraper()
        titulo, empresa = "Lead MS Fabric Architect", "NTT Data Services Inc"
        assert scraper.compute_hash(
            titulo, empresa, canonical_identity_url(antes)
        ) == scraper.compute_hash(titulo, empresa, canonical_identity_url(despues))

    def test_el_hash_persistido_usa_la_identidad_canonica(self):
        """`normalize_job` es quien tiene que aplicarla, no solo el helper."""
        scraper = IrishJobsScraper()
        base = {"title": "Lead Architect", "company": "NTT", "description": ""}
        uno = scraper.normalize_job({**base, "url": f"{_IJ}/job/aaa/ntt-job107803777"})
        dos = scraper.normalize_job({**base, "url": f"{_IJ}/job/bbb/ntt-job107803777"})
        assert uno["hash"] == dos["hash"]
        # La `url` PUBLICADA sigue siendo la real: solo cambia la identidad.
        assert uno["url"] != dos["url"]

    def test_el_host_no_cambia_la_identidad(self):
        """El id de plataforma es global de StepStone, no por host."""
        assert canonical_identity_url(f"{_JI}/job/x/y-job107803777") == (
            canonical_identity_url(f"{_IJ}/job/z/w-job107803777")
        )

    def test_ids_DISTINTOS_siguen_siendo_ofertas_distintas(self):
        """Los 35 grupos cross-host tienen ids distintos: NO deben fusionarse."""
        assert canonical_identity_url(f"{_JI}/job/fbp/acme-job107773663") != (
            canonical_identity_url(f"{_IJ}/job/fbp/acme-job107860932")
        )

    def test_sin_id_reconocible_se_conserva_la_url(self):
        """Identidad volátil antes que identidad ambigua."""
        suelta = f"{_IJ}/jobs/work-from-home"
        assert canonical_identity_url(suelta) == suelta

    def test_la_barra_final_no_parte_la_identidad(self):
        assert canonical_identity_url(f"{_IJ}/job/x/y-job900001/") == (
            canonical_identity_url(f"{_IJ}/job/x/y-job900001")
        )
