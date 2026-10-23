"""Interfaz (el "puerto") de cualquier IA que redacte borradores: patrón Strategy.

El resto del programa solo conoce `ProveedorIA.generar()`. Claude, Gemini o un simulador
son estrategias intercambiables: cambiar de IA no toca la lógica de preparación.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class IAError(Exception):
    """La IA no pudo generar el texto (credenciales, red, rechazo, respuesta cortada…)."""


@dataclass(frozen=True)
class RespuestaIA:
    texto: str
    modelo: str             # el modelo que RESPONDIÓ (con fallback puede no ser el pedido)
    tokens_entrada: int     # para saber cuánto costó cada borrador
    tokens_salida: int


class ProveedorIA(ABC):
    # Nombre corto: se usa en config/ia.toml y se guarda junto a cada borrador.
    nombre: str
    modelo: str

    @abstractmethod
    def generar(self, instrucciones: str, mensaje: str) -> RespuestaIA:
        """`instrucciones`: cómo comportarse (plantilla). `mensaje`: los datos (CV + oferta)."""
