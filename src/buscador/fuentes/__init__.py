"""Adaptadores de fuentes de empleo. Cada fuente hereda de `Fuente`.

Registro: para añadir una fuente nueva, crea su archivo y agrégala a FUENTES.
La CLI lee este diccionario, así que no hay que tocarla.
"""

from buscador.fuentes.arbeitnow import Arbeitnow
from buscador.fuentes.base import Fuente
from buscador.fuentes.getonboard import GetOnBoard
from buscador.fuentes.remotive import Remotive
from buscador.fuentes.weworkremotely import WeWorkRemotely

FUENTES: dict[str, type[Fuente]] = {
    Remotive.nombre: Remotive,
    Arbeitnow.nombre: Arbeitnow,
    WeWorkRemotely.nombre: WeWorkRemotely,  # remoto global (RSS)
    GetOnBoard.nombre: GetOnBoard,          # Latinoamérica
}
