"""Il dominio resta puro: nessun import di infrastruttura (review M2, arch 3).

TID251 copre solo SQLAlchemy; questo test copre tutto il resto: qualunque
import non-stdlib e non-domain dentro `irec/domain/` fa fallire la CI.
"""

import ast
import sys
from pathlib import Path

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "irec" / "domain"

MODULI_AMMESSI_PREFISSI = ("irec.domain",)


def _import_di(path: Path) -> set[str]:
    albero = ast.parse(path.read_text())
    moduli: set[str] = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Import):
            moduli.update(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            moduli.add(nodo.module)
    return moduli


def test_il_dominio_importa_solo_stdlib_e_se_stesso():
    violazioni: list[str] = []
    for file in DOMAIN_DIR.glob("*.py"):
        for modulo in _import_di(file):
            radice = modulo.split(".")[0]
            if modulo.startswith(MODULI_AMMESSI_PREFISSI):
                continue
            if radice in sys.stdlib_module_names:
                continue
            violazioni.append(f"{file.name}: import {modulo}")
    assert violazioni == [], violazioni
