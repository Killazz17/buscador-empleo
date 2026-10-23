"""Modelo común del dominio.

Cada API de empleo devuelve un JSON distinto. Lo convertimos todo a `Oferta`
para que el resto del pipeline (filtros, deduplicación, IA) trabaje con un único
formato y no le importe de qué fuente vino la oferta.
"""

import re
from dataclasses import dataclass, field

# Caracteres de control salvo \n y \t. Incluye ESC (\x1b) y CSI (\x9b): con ellos un texto de
# terceros puede borrar la pantalla u ocultar líneas en tu terminal (p. ej. mientras apruebas).
_CONTROLES = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def sin_controles(valor: str) -> str:
    return _CONTROLES.sub("", valor)


def texto(valor) -> str:
    """None → "", cualquier otra cosa → str SIN caracteres de control.

    Ojo: dict.get(k, "") NO sirve si la clave existe con valor null.
    """
    return "" if valor is None else sin_controles(str(valor))


def lista_de_textos(valor) -> list[str]:
    """Normaliza a lista de str: None → [], "java" → ["java"], ["a", None, 7] → ["a", "7"]."""
    if isinstance(valor, str):
        return [texto(valor)]
    if not isinstance(valor, list):
        return []
    return [texto(v) for v in valor if v is not None]


@dataclass
class Oferta:
    id_fuente: str          # id de la oferta dentro de su fuente
    fuente: str             # "remotive", "arbeitnow"…
    titulo: str
    empresa: str
    url: str                # enlace oficial para postular (lo abre el usuario, nunca la IA)
    ubicacion: str          # dónde se acepta candidatos, ej. "Worldwide", "LATAM"
    tipo: str               # full_time, contract, internship…
    publicada: str          # fecha ISO 8601 tal como la da la fuente
    descripcion: str        # puede venir en HTML; se limpia en pasos posteriores
    etiquetas: list[str] = field(default_factory=list)
    remoto: bool = False    # campo propio: es un dato que se usa para puntuar, no solo para mostrar

    @classmethod
    def desde_dict(cls, datos) -> "Oferta":
        """Reconstruye una Oferta desde el JSON guardado en data/ (dato NO confiable: pudo editarse).

        Exige los campos que la identifican; el resto se rellena con valores seguros.
        Acepta archivos antiguos sin `remoto`.
        """
        if not isinstance(datos, dict):
            raise TypeError(f"se esperaba un objeto, llegó {type(datos).__name__}")
        faltan = [c for c in ("id_fuente", "fuente", "titulo") if not isinstance(datos.get(c), str) or not datos[c]]
        if faltan:
            raise ValueError(f"faltan campos obligatorios: {', '.join(faltan)}")
        return cls(
            id_fuente=texto(datos["id_fuente"]),
            fuente=texto(datos["fuente"]),
            titulo=texto(datos["titulo"]),
            empresa=texto(datos.get("empresa")),
            url=texto(datos.get("url")),
            ubicacion=texto(datos.get("ubicacion")),
            tipo=texto(datos.get("tipo")),
            publicada=texto(datos.get("publicada")),
            descripcion=texto(datos.get("descripcion")),
            etiquetas=lista_de_textos(datos.get("etiquetas")),
            remoto=datos.get("remoto") is True,
        )
