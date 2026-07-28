"""Implementacion LOCAL de la capacidad colegios — A.SEAM (plan §15bis).

El listado del router era una comprension sobre la config estatica
`SCHOOLS`: se mueve VERBATIM aqui — con routing 'local' el comportamiento es
byte-identico al previo a la costura. NO cambiar semantica aqui sin contract
test.
"""

from scrapers.swiss_schools_config import SCHOOLS


class LocalSchools:
    """Fuente actual: config estatica de colegios vigilados (en el repo)."""

    async def list(self) -> dict:
        return {
            "schools": [
                {
                    "id": s.id,
                    "name": s.name,
                    "city": s.city,
                    "group_tier": s.group_tier,
                    "policy": s.policy,
                    "contact_email": s.contact_email,
                    "contact_name": s.contact_name,
                    "template_id": s.template_id,
                    "application_url": s.application_url,
                    "careers_url": s.careers_url,
                    "notes": s.notes,
                }
                for s in SCHOOLS
            ]
        }
