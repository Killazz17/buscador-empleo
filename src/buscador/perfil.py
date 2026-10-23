"""Perfil del candidato: qué tecnologías valen cuánto y qué penaliza o descarta.

Vive en config/perfil.toml para poder ajustarlo sin tocar código. Usamos TOML y no YAML
porque `tomllib` viene incluido en Python 3.11+ (YAML necesitaría instalar PyYAML).
El archivo lo edita una persona, así que se valida de forma ESTRICTA: un typo que se
ignorara en silencio (una clave mal escrita, un término vacío, un signo cambiado)
cambiaría el ranking sin que nadie se diera cuenta.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CLAVES_PERFIL = {"puntuacion_minima", "nivel_bonus", "nivel_penaliza", "roles_excluidos",
                 "idiomas_excluyentes", "puntos", "tecnologias", "ubicacion"}
CLAVES_PUNTOS = {"nivel_bonus", "nivel_penaliza", "idioma_excluyente", "remoto", "tope_descripcion", "remoto_dudoso"}
CLAVES_UBICACION = {"pais", "remoto_aceptado", "reubicacion", "restricciones_remoto"}
CLAVES_TECNOLOGIA = {"peso", "alias"}


class PerfilError(Exception):
    """El perfil no existe, no es TOML válido o le falta/sobra algo."""


@dataclass(frozen=True)
class Perfil:
    tecnologias: dict[str, int]     # término → peso (en el título vale el doble)
    nivel_bonus: list[str]          # en el título: junior, intern…
    nivel_penaliza: list[str]       # en el título: senior, lead…
    roles_excluidos: list[str]      # en el título → oferta descartada
    idiomas_excluyentes: list[str]  # exigidos en la oferta → penalización
    puntos_nivel_bonus: int
    puntos_nivel_penaliza: int
    puntos_idioma: int
    puntos_remoto: int
    puntuacion_minima: int
    # Sinónimos: fullstack → ["full stack", "full-stack"]. Cuentan como la misma tecnología (una vez).
    alias: dict[str, list[str]] = field(default_factory=dict)
    # Máximo de puntos por tecnologías encontradas solo en la descripción (None = sin tope).
    puntos_tope_descripcion: int | None = None
    # Regla de ubicación (sección [ubicacion], opcional; con `pais` vacío no se aplica):
    pais: list[str] = field(default_factory=list)             # términos que identifican tu país/ciudades
    remoto_aceptado: list[str] = field(default_factory=list)  # regiones remotas desde las que puedes trabajar
    reubicacion: list[str] = field(default_factory=list)      # señales de ayuda para mudarte (visa, relocation)
    restricciones_remoto: list[str] = field(default_factory=list)  # "remote from eu", "us only"… en título/descripción
    puntos_remoto_dudoso: int = 0                             # remoto sin región clara: se mantiene con aviso


def cargar_perfil(ruta: Path) -> Perfil:
    try:
        datos = tomllib.loads(ruta.read_text(encoding="utf-8"))
    except OSError as e:
        raise PerfilError(f"No se pudo leer el perfil {ruta}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise PerfilError(f"{ruta} no es TOML válido: {e}") from e

    _sin_claves_desconocidas(datos, CLAVES_PERFIL, "el perfil")
    puntos = _tabla(datos, "puntos")
    _sin_claves_desconocidas(puntos, CLAVES_PUNTOS, "[puntos]")

    tecnologias: dict[str, int] = {}
    alias: dict[str, list[str]] = {}
    for termino, valor in _tabla(datos, "tecnologias").items():
        nombre = _termino(termino, "una clave de [tecnologias]")
        # "Java" y "java" serían la misma tecnología: una pisaría a la otra sin avisar.
        if nombre in tecnologias:
            raise PerfilError(f"la tecnología '{nombre}' está repetida (las mayúsculas no cuentan)")
        # Dos formas válidas: `kotlin = 3` o `fullstack = { peso = 2, alias = ["full stack"] }`.
        if isinstance(valor, dict):
            _sin_claves_desconocidas(valor, CLAVES_TECNOLOGIA, f"tecnologias.{nombre}")
            tecnologias[nombre] = _entero(valor.get("peso"), f"tecnologias.{nombre}.peso")
            alias[nombre] = _textos(valor.get("alias", []), f"tecnologias.{nombre}.alias")
        else:
            tecnologias[nombre] = _entero(valor, f"tecnologias.{nombre}")
    if not tecnologias:
        raise PerfilError("[tecnologias] está vacía: ninguna oferta podría puntuar")

    tope = puntos.get("tope_descripcion")
    dudoso = puntos.get("remoto_dudoso")
    ubicacion = datos.get("ubicacion", {})
    if not isinstance(ubicacion, dict):
        raise PerfilError("[ubicacion] debe ser una sección, ej. [ubicacion]\\npais = [\"Costa Rica\"]")
    _sin_claves_desconocidas(ubicacion, CLAVES_UBICACION, "[ubicacion]")
    return Perfil(
        pais=_textos(ubicacion.get("pais", []), "ubicacion.pais"),
        remoto_aceptado=_textos(ubicacion.get("remoto_aceptado", []), "ubicacion.remoto_aceptado"),
        reubicacion=_textos(ubicacion.get("reubicacion", []), "ubicacion.reubicacion"),
        restricciones_remoto=_textos(ubicacion.get("restricciones_remoto", []), "ubicacion.restricciones_remoto"),
        puntos_remoto_dudoso=0 if dudoso is None else _entero(dudoso, "puntos.remoto_dudoso", signo=-1),
        tecnologias=tecnologias,
        alias=alias,
        nivel_bonus=_lista(datos, "nivel_bonus"),
        nivel_penaliza=_lista(datos, "nivel_penaliza"),
        roles_excluidos=_lista(datos, "roles_excluidos"),
        idiomas_excluyentes=_lista(datos, "idiomas_excluyentes"),
        # Los signos se validan: `nivel_penaliza = 12` (sin el "-") premiaría a los senior.
        puntos_nivel_bonus=_entero(puntos.get("nivel_bonus"), "puntos.nivel_bonus", signo=+1),
        puntos_nivel_penaliza=_entero(puntos.get("nivel_penaliza"), "puntos.nivel_penaliza", signo=-1),
        puntos_idioma=_entero(puntos.get("idioma_excluyente"), "puntos.idioma_excluyente", signo=-1),
        puntos_remoto=_entero(puntos.get("remoto"), "puntos.remoto"),
        puntos_tope_descripcion=None if tope is None else _entero(tope, "puntos.tope_descripcion", signo=+1),
        puntuacion_minima=_entero(datos.get("puntuacion_minima"), "puntuacion_minima"),
    )


def _sin_claves_desconocidas(tabla: dict, permitidas: set[str], donde: str) -> None:
    # Un typo como `tope_descripion` se ignoraría en silencio: mejor avisar.
    desconocidas = sorted(set(tabla) - permitidas)
    if desconocidas:
        raise PerfilError(f"clave(s) desconocida(s) en {donde}: {', '.join(desconocidas)}. "
                          f"Válidas: {', '.join(sorted(permitidas))}")


def _entero(valor, nombre: str, signo: int = 0) -> int:
    # bool es subclase de int: `remoto = true` no debe colarse como 1.
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise PerfilError(f"'{nombre}' debe ser un número entero (encontrado: {valor!r})")
    if signo > 0 and valor < 0:
        raise PerfilError(f"'{nombre}' debe ser 0 o positivo (encontrado: {valor})")
    if signo < 0 and valor > 0:
        raise PerfilError(f"'{nombre}' debe ser 0 o negativo, ej. -6 (encontrado: {valor})")
    return valor


def _termino(valor: str, donde: str) -> str:
    # Un término vacío coincidiría con casi cualquier texto (la regex encaja en cualquier hueco).
    limpio = valor.strip().lower()
    if not limpio:
        raise PerfilError(f"hay un término vacío en {donde} (¿una coma de más?)")
    return limpio


def _lista(datos: dict, clave: str) -> list[str]:
    return _textos(datos.get(clave), clave)


def _textos(valor, nombre: str) -> list[str]:
    if not isinstance(valor, list) or not all(isinstance(v, str) for v in valor):
        raise PerfilError(f"'{nombre}' debe ser una lista de textos, ej. [\"a\", \"b\"]")
    return [_termino(v, f"'{nombre}'") for v in valor]


def _tabla(datos: dict, clave: str) -> dict:
    valor = datos.get(clave)
    if not isinstance(valor, dict):
        raise PerfilError(f"falta la sección [{clave}]")
    return valor
