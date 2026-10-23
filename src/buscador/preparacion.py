"""Paso 5: preparar borradores de carta con la IA configurada.

La IA REDACTA; nunca aprueba ni envía. El borrador queda en estado 'preparada'
esperando a que el usuario lo revise y lo apruebe en su terminal.
"""

import hashlib
import re
from dataclasses import dataclass

from buscador.almacenamiento import ESTADOS_PREPARABLES, Preparacion, Repositorio, TransicionInvalida
from buscador.filtros import limpiar_html
from buscador.ia.base import ProveedorIA
from buscador.modelos import Oferta, sin_controles

# Tope de la descripción enviada a la IA: limita el coste y la superficie de inyección de prompt.
MAX_DESCRIPCION = 8000

_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_CORREO = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_NUMERO_LARGO = re.compile(r"\d{7,}")
_CIFRA = re.compile(r"\b\d{2,}\b")


@dataclass(frozen=True)
class Prompt:
    instrucciones: str
    mensaje: str
    huella: str  # identifica la versión de la plantilla: si la editas, sabrás qué cartas usaron cuál


def _neutralizar(texto: str) -> str:
    """El texto de la oferta es de TERCEROS: no le dejamos abrir ni cerrar etiquetas del prompt."""
    return texto.replace("<", "‹").replace(">", "›")


def _texto_limpio(html: str) -> str:
    texto = limpiar_html(html)
    # Colapsa espacios de cada línea y quita líneas vacías sobrantes, conservando los saltos.
    lineas = (re.sub(r"[ \t]+", " ", linea).strip() for linea in texto.splitlines())
    return "\n".join(linea for linea in lineas if linea)


def construir_prompt(oferta: Oferta, cv: str, plantilla: str) -> Prompt:
    descripcion = _texto_limpio(oferta.descripcion)
    if len(descripcion) > MAX_DESCRIPCION:
        descripcion = descripcion[:MAX_DESCRIPCION] + "\n[…descripción recortada]"
    datos = "\n".join([
        f"Título: {oferta.titulo}",
        f"Empresa: {oferta.empresa}",
        f"Ubicación: {oferta.ubicacion}",
        f"Remoto: {'sí' if oferta.remoto else 'no'}",
        f"Etiquetas: {', '.join(oferta.etiquetas)}",
        "Descripción:",
        descripcion,
    ])
    mensaje = (
        f"<cv>\n{cv}\n</cv>\n\n"
        f"<oferta>\n{_neutralizar(datos)}\n</oferta>\n\n"
        "Redacta la carta de presentación para esta oferta siguiendo tus instrucciones."
    )
    huella = hashlib.sha256(plantilla.encode("utf-8")).hexdigest()[:12]
    return Prompt(plantilla, mensaje, huella)


def alertas_borrador(texto: str, cv: str) -> list[str]:
    """Señales de que la oferta pudo "inyectar" algo en la carta, o de que la IA inventó cifras.

    No bloquea nada: son avisos para que el humano mire con lupa antes de aprobar.
    """
    alertas = [f"contiene un enlace: {u.rstrip('.,;)')}" for u in _URL.findall(texto)]
    alertas += [f"contiene un correo: {c.rstrip('.')}" for c in _CORREO.findall(texto)]
    largos = set(_NUMERO_LARGO.findall(texto))
    alertas += [f"contiene un número largo (¿teléfono o documento?): {n}" for n in sorted(largos)]
    cifras_del_cv = set(re.findall(r"\d+", cv))
    for cifra in sorted(set(_CIFRA.findall(texto)) - cifras_del_cv - largos):
        alertas.append(f"la cifra '{cifra}' no aparece en tu CV (¿inventada?)")
    return alertas


def preparar_oferta(repo: Repositorio, proveedor: ProveedorIA, oferta_id: int, cv: str, plantilla: str,
                    ahora: str | None = None) -> Preparacion:
    guardada = repo.obtener(oferta_id)
    # Se comprueba ANTES de llamar a la IA: no pagamos un borrador que no se podría guardar.
    if guardada.estado not in ESTADOS_PREPARABLES:
        raise TransicionInvalida(f"la oferta #{oferta_id} está '{guardada.estado}': "
                                 "solo se preparan ofertas nuevas o ya preparadas")
    prompt = construir_prompt(guardada.oferta, cv, plantilla)
    respuesta = proveedor.generar(prompt.instrucciones, prompt.mensaje)
    # La salida de la IA tampoco es de fiar del todo: sin caracteres de control antes de guardarla.
    return repo.guardar_preparacion(
        oferta_id, sin_controles(respuesta.texto), proveedor.nombre, respuesta.modelo, prompt.huella,
        tokens_entrada=respuesta.tokens_entrada, tokens_salida=respuesta.tokens_salida, ahora=ahora,
    )
