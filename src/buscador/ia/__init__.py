"""Proveedores de IA (patrón Strategy) y su configuración.

Para añadir una IA (p. ej. Gemini): crea `ia/gemini.py` con una clase que herede de
ProveedorIA, regístrala en PROVEEDORES y escribe sus tests. Nada más cambia.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from buscador.ia.base import IAError, ProveedorIA
from buscador.ia.claude import ClaudeAPI
from buscador.ia.manual import Manual
from buscador.ia.simulado import Simulado

PROVEEDORES: dict[str, type[ProveedorIA]] = {
    Simulado.nombre: Simulado,
    ClaudeAPI.nombre: ClaudeAPI,
    Manual.nombre: Manual,
}
CLAVES = {"proveedor", "modelo", "esfuerzo"}
ESFUERZOS = {"low", "medium", "high", "xhigh", "max"}


@dataclass(frozen=True)
class ConfigIA:
    proveedor: str
    opciones: dict = field(default_factory=dict)  # se pasan tal cual al constructor del proveedor


def cargar_config_ia(ruta: Path) -> ConfigIA:
    try:
        datos = tomllib.loads(ruta.read_text(encoding="utf-8"))
    except OSError as e:
        raise IAError(f"No se pudo leer la configuración de IA {ruta}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise IAError(f"{ruta} no es TOML válido: {e}") from e

    desconocidas = sorted(set(datos) - CLAVES)
    if desconocidas:
        raise IAError(f"clave(s) desconocida(s) en {ruta.name}: {', '.join(desconocidas)}. "
                      f"Válidas: {', '.join(sorted(CLAVES))}")
    proveedor = datos.get("proveedor")
    if proveedor not in PROVEEDORES:
        raise IAError(f"'proveedor' debe ser uno de: {', '.join(sorted(PROVEEDORES))} (encontrado: {proveedor!r})")
    if "modelo" in datos and (not isinstance(datos["modelo"], str) or not datos["modelo"].strip()):
        raise IAError("'modelo' debe ser un texto no vacío")
    if "esfuerzo" in datos and datos["esfuerzo"] not in ESFUERZOS:
        raise IAError(f"'esfuerzo' debe ser uno de: {', '.join(sorted(ESFUERZOS))}")
    return ConfigIA(proveedor, {k: datos[k] for k in ("modelo", "esfuerzo") if k in datos})


def crear_proveedor(config: ConfigIA) -> ProveedorIA:
    return PROVEEDORES[config.proveedor](**config.opciones)
