"""Estrategia falsa: no llama a ninguna IA. Gratis, determinista y sin red.

Sirve para los tests y para probar el flujo completo (preparar → aprobar) sin gastar dinero.
"""

from buscador.ia.base import IAError, ProveedorIA, RespuestaIA


class Simulado(ProveedorIA):
    nombre = "simulado"

    def __init__(self, modelo: str = "simulado", esfuerzo: str | None = None, falla: bool = False,
                 texto: str | None = None):
        self.modelo = modelo
        self.falla = falla
        self.texto = texto  # respuesta fija (para probar qué pasa con un borrador concreto)
        self.llamadas: list[tuple[str, str]] = []  # para que los tests comprueben qué se pidió

    def generar(self, instrucciones: str, mensaje: str) -> RespuestaIA:
        self.llamadas.append((instrucciones, mensaje))
        if self.falla:
            raise IAError("fallo simulado de la IA")
        texto = self.texto or "Carta simulada (sin IA real). Datos recibidos:\n" + mensaje[:300]
        return RespuestaIA(texto, self.modelo, tokens_entrada=len(instrucciones + mensaje) // 4, tokens_salida=100)
