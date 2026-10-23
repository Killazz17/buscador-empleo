"""Estrategia gratuita: la "IA" eres tú usando la web de Claude o Gemini (lo que ya pagas).

`generar()` escribe el prompt completo en un archivo (y lo copia al portapapeles), espera a
que pegues la respuesta de la web en otro archivo y la devuelve. Mismo contrato que ClaudeAPI:
el resto del programa (guardar versión, aprobar, historial) no nota la diferencia.
"""

import re
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from buscador.ia.base import IAError, ProveedorIA, RespuestaIA

# src/buscador/ia/manual.py → parents[3] = raíz del proyecto. data/ está en .gitignore.
CARPETA_POR_DEFECTO = Path(__file__).resolve().parents[3] / "data" / "manual"


def _copiar_al_portapapeles(texto: str) -> None:
    """Mejor esfuerzo: en Windows usa `clip` con UTF-16 para conservar tildes y ñ.

    Sin BOM: comprobado en Windows que `clip` lo reconoce igual, y con BOM se colaba un carácter
    invisible (U+FEFF) al principio del texto pegado.
    """
    if sys.platform == "win32":
        subprocess.run(["clip"], input=texto.encode("utf-16-le"), check=True, timeout=5)


class Manual(ProveedorIA):
    nombre = "manual"

    def __init__(self, modelo: str = "manual", esfuerzo: str | None = None, carpeta: Path = CARPETA_POR_DEFECTO,
                 entrada: Callable[[str], str] = input, es_interactivo: Callable[[], bool] | None = None,
                 copiar: Callable[[str], None] = _copiar_al_portapapeles):
        self.modelo = modelo  # etiqueta libre de dónde lo generaste: "claude.ai", "gemini"…
        self._carpeta = Path(carpeta)
        self._entrada = entrada
        self._es_interactivo = es_interactivo or (lambda: sys.stdin is not None and sys.stdin.isatty())
        self._copiar = copiar

    def generar(self, instrucciones: str, mensaje: str) -> RespuestaIA:
        # Sin una persona delante no hay nadie que copie y pegue (p. ej. si lo lanza una IA o un cron).
        if not self._es_interactivo():
            raise IAError("El proveedor manual necesita una terminal interactiva: ejecuta 'preparar' tú mismo")

        self._carpeta.mkdir(parents=True, exist_ok=True)
        base = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        ruta_prompt = self._carpeta / f"{base}-prompt.md"
        ruta_respuesta = self._carpeta / f"{base}-respuesta.md"
        # La web no tiene "system prompt": instrucciones y datos van juntos en un solo texto.
        prompt = f"<instrucciones>\n{instrucciones}\n</instrucciones>\n\n{mensaje}"
        ruta_prompt.write_text(prompt, encoding="utf-8")
        ruta_respuesta.write_text("", encoding="utf-8")
        try:
            self._copiar(prompt)
            copiado = " (ya está copiado en tu portapapeles)"
        except (OSError, subprocess.SubprocessError):
            copiado = ""

        print(f"\n1. Abre el prompt{copiado}:\n   {ruta_prompt}")
        print(f"2. Pégalo en {self.modelo} (web) y copia SOLO la carta que te devuelva.")
        print(f"3. Pégala en este archivo y guárdalo:\n   {ruta_respuesta}")
        if self._entrada("4. Pulsa Enter cuando esté guardado (o escribe 'cancelar'): ").strip().lower() == "cancelar":
            raise IAError("preparación manual cancelada")

        texto = self._leer_respuesta(ruta_respuesta)
        if not texto:
            raise IAError(f"la respuesta está vacía: pega la carta en {ruta_respuesta.name} y vuelve a intentarlo")
        # Sin tokens: no pasó por ninguna API (coste 0).
        return RespuestaIA(texto, self.modelo, tokens_entrada=0, tokens_salida=0)

    @staticmethod
    def _leer_respuesta(ruta: Path) -> str:
        """Lee lo que pegó la persona, tal como lo dejó el editor de Windows.

        Descubierto con la primera carta real: al pegar desde el navegador en el Bloc de notas
        quedaban "\\r\\r\\n" (cada salto contaba doble) y a veces un BOM al principio.
        """
        try:
            texto = ruta.read_bytes().decode("utf-8-sig")  # -sig: quita el BOM si lo hay
        except UnicodeDecodeError as e:
            raise IAError(f"{ruta.name} no está guardado como UTF-8: guárdalo de nuevo con codificación UTF-8") from e
        texto = texto.replace("\r\n", "\n").replace("\r", "")
        return re.sub(r"\n{3,}", "\n\n", texto).strip()  # como mucho, una línea en blanco entre párrafos
