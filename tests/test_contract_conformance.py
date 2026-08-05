"""Verifica automatica contratto ↔ implementazione (backlog R1 item 8).

Il progetto è contract-first: `openapi.yaml` è la fonte di verità. Questo
test fa fallire la CI se una rotta è implementata ma non dichiarata (o
viceversa), così il contratto non può divergere in silenzio dal codice.
"""

import re
from pathlib import Path

import yaml

from irec.config import Settings
from irec.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]

# Endpoint dichiarati nel contratto ma non ancora implementati (bozza).
# Ogni voce va giustificata: qui l'ingest diretto è rimandato a M8.
NON_IMPLEMENTATI = {("/v1/ingest", "post")}

_METODI = {"get", "post", "put", "patch", "delete"}
_PARAM = re.compile(r"\{[^}]+\}")


def _normalizza(path: str) -> str:
    """Uniforma i nomi dei path-param: il confronto è su struttura, non nomi."""
    return _PARAM.sub("{}", path)


def _rotte_implementate() -> set[tuple[str, str]]:
    """Dallo spec generato da FastAPI: è la fotografia reale delle rotte."""
    app = create_app(Settings(jwks_url=None, database_url=None))
    spec = app.openapi()
    rotte = set()
    for path, item in spec["paths"].items():
        for metodo in item:
            if metodo in _METODI:
                rotte.add((_normalizza(path), metodo))
    return rotte


def _rotte_contratto() -> set[tuple[str, str]]:
    spec = yaml.safe_load((REPO_ROOT / "openapi.yaml").read_text())
    base = spec.get("servers", [{"url": ""}])[0]["url"].rstrip("/")
    rotte = set()
    for path, item in spec["paths"].items():
        # Override per-path (health/ready servono alla root).
        prefisso = item["servers"][0]["url"].rstrip("/") if "servers" in item else base
        completo = _normalizza(f"{prefisso}{path}")
        for metodo in item:
            if metodo in _METODI:
                rotte.add((completo, metodo))
    return rotte


def test_ogni_rotta_implementata_e_nel_contratto():
    implementate = _rotte_implementate()
    contratto = _rotte_contratto()
    non_dichiarate = implementate - contratto
    assert non_dichiarate == set(), (
        f"rotte implementate ma assenti da openapi.yaml: {sorted(non_dichiarate)}"
    )


def test_ogni_rotta_del_contratto_e_implementata():
    implementate = _rotte_implementate()
    contratto = _rotte_contratto()
    mancanti = contratto - implementate - NON_IMPLEMENTATI
    assert mancanti == set(), (
        f"rotte nel contratto ma non implementate: {sorted(mancanti)} "
        f"(se intenzionale, aggiungere a NON_IMPLEMENTATI con motivazione)"
    )
