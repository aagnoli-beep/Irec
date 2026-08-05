"""Brief giornaliero: unisce KPI (letture) e notifiche non lette (M6)."""

from irec.adapters.db.repository import TenantRepository
from irec.domain.brief import Brief, componi_brief
from irec.services.letture import calcola_kpi
from irec.services.notifiche import conteggio_per_tipo


def componi_brief_giornaliero(repo: TenantRepository) -> Brief:
    kpi = calcola_kpi(repo)
    return componi_brief(
        affidato=kpi.affidato,
        recuperato=kpi.recuperato,
        da_recuperare=kpi.da_recuperare,
        passato_a_recupero=kpi.passato_a_recupero,
        notifiche_per_tipo=conteggio_per_tipo(repo),
    )
