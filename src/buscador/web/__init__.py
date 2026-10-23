"""Interfaz web LOCAL (FastAPI + HTML): otro adaptador sobre el mismo núcleo que usa la CLI.

No duplica lógica: usa Repositorio, construir_prompt y alertas_borrador igual que `cli.py`.
Seguridad (la página muestra texto de terceros y tiene un botón de aprobar):
  - Solo escucha en 127.0.0.1 (lo fija `buscador web`) y solo acepta Host 127.0.0.1/localhost
    → frena el "DNS rebinding" (una web que apunta su dominio a tu PC).
  - Token CSRF aleatorio por arranque en cada formulario → otra pestaña no puede enviar acciones.
  - Jinja2 escapa todo el texto (XSS) y los enlaces solo pueden ser http(s) (nada de javascript:).
  - Aprobar y marcar enviada exigen ESCRIBIR "si"; aprobar exige además la versión que estás viendo.
Límite honesto: un programa en tu PC que lea la página puede obtener el token. Es una barrera
contra errores y webs ajenas, no contra software malicioso que ya corre con tu usuario.
"""

import re
import secrets
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from buscador.almacenamiento import (
    ESTADOS,
    AlmacenamientoError,
    OfertaNoEncontrada,
    Repositorio,
    TransicionInvalida,
)
from buscador.filtros import limpiar_html
from buscador.modelos import normalizar_pegado, sin_controles, url_segura
from buscador.preparacion import alertas_borrador, construir_prompt

PLANTILLAS = Path(__file__).parent / "plantillas"
CONFIRMACIONES = {"si", "sí"}
# Avisos tras cada acción: el texto sale de esta lista cerrada, nunca de la URL (nada se "refleja").
AVISOS = {
    "descartada": "Oferta descartada",
    "restaurada": "Oferta restaurada",
    "preparada": "Borrador guardado",
    "aprobada": "Carta aprobada",
    "enviada": "¡Postulación registrada! Mucha suerte",
}


def _csp(nonce: str) -> str:
    """Content-Security-Policy: solo se ejecutan los scripts que llevan el nonce de ESTA respuesta.

    Aunque se colara HTML ajeno en la página, su <script> o un onclick="..." no correrían.
    """
    return ("default-src 'self'; "
            f"script-src 'nonce-{nonce}'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def _tipo_motivo(motivo: str) -> str:
    """Para colorear los chips: 'pos' si suma, 'neg' si resta, 'info' si solo explica."""
    if re.search(r"\+\d+$", motivo):
        return "pos"
    if re.search(r"-\d+$", motivo) or motivo.startswith(("rol excluido", "remoto restringido", "presencial fuera de")):
        return "neg"
    return "info"


def crear_app(ruta_db: Path, ruta_cv: Path, ruta_plantilla: Path,
              hosts: tuple[str, ...] = ("127.0.0.1", "localhost")) -> FastAPI:
    # Sin /docs ni /openapi.json: es una app personal, no una API pública.
    app = FastAPI(title="Buscador de empleo", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(hosts))
    app.state.token = secrets.token_urlsafe(32)
    vistas = Jinja2Templates(directory=PLANTILLAS)
    vistas.env.filters["url_segura"] = url_segura
    vistas.env.filters["s"] = sin_controles
    vistas.env.filters["tipo_motivo"] = _tipo_motivo
    vistas.env.filters["parrafos"] = normalizar_pegado  # solo para MOSTRAR cartas antiguas con saltos de más
    # Test de Jinja para `select('match', patrón)`: Jinja no trae uno de regex de serie.
    vistas.env.tests["match"] = lambda valor, patron: re.search(patron, str(valor)) is not None

    @app.middleware("http")
    async def cabeceras_de_seguridad(request: Request, call_next):
        request.state.nonce = secrets.token_urlsafe(16)  # uno nuevo en cada respuesta
        respuesta = await call_next(request)
        respuesta.headers["Content-Security-Policy"] = _csp(request.state.nonce)
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["Referrer-Policy"] = "no-referrer"  # no filtrar URLs locales a las webs de ofertas
        return respuesta

    def leer(ruta: Path) -> str:
        return ruta.read_text(encoding="utf-8") if ruta.exists() else ""

    def verificar_token(token: str) -> None:
        # compare_digest: comparación en tiempo constante (no filtra el token por cuánto tarda).
        if not secrets.compare_digest(token, app.state.token):
            raise HTTPException(403, "Token inválido: recarga la página y vuelve a intentarlo")

    def confirmado(texto: str) -> bool:
        return texto.strip().lower() in CONFIRMACIONES

    def render(request: Request, plantilla: str, status_code: int = 200, **contexto) -> HTMLResponse:
        with Repositorio(ruta_db) as repo:
            conteo = repo.contar_por_estado()  # el embudo de la cabecera aparece en todas las páginas
        aviso = request.query_params.get("aviso", "")
        base = {"token": app.state.token, "nonce": request.state.nonce, "conteo": conteo,
                "aviso": aviso if aviso in AVISOS else "", "aviso_texto": AVISOS.get(aviso, ""),
                "aviso_id": request.query_params.get("id", "")}
        return vistas.TemplateResponse(request, plantilla, {**base, **contexto}, status_code=status_code)

    def obtener(repo: Repositorio, oferta_id: int):
        try:
            return repo.obtener(oferta_id)
        except OfertaNoEncontrada:
            raise HTTPException(404, f"No existe la oferta #{oferta_id}") from None

    def volver(ruta: str) -> RedirectResponse:
        return RedirectResponse(ruta, status_code=303)  # 303: tras un POST, el navegador hace GET

    @app.get("/", response_class=HTMLResponse)
    def lista(request: Request, estado: str = "nueva"):
        estado = estado if estado in ESTADOS or estado == "todas" else "nueva"
        with Repositorio(ruta_db) as repo:
            ofertas = repo.listar(estado=None if estado == "todas" else estado, limite=200)
        return render(request, "lista.html", ofertas=ofertas, estado=estado, estados=sorted(ESTADOS))

    @app.get("/ofertas/{oferta_id}", response_class=HTMLResponse)
    def detalle(request: Request, oferta_id: int):
        with Repositorio(ruta_db) as repo:
            g = obtener(repo, oferta_id)
            prep, historial, integra = (repo.ultima_preparacion(oferta_id), repo.historial(oferta_id),
                                        repo.verificar_aprobacion(oferta_id))
        return render(request, "detalle.html", g=g, prep=prep, historial=historial, integra=integra,
                      descripcion=sin_controles(limpiar_html(g.oferta.descripcion)))

    @app.post("/ofertas/{oferta_id}/descartar")
    def descartar(oferta_id: int, token: str = Form(""), nota: str = Form("")):
        _cambiar(oferta_id, "descartada", token, nota)
        # El id viaja para el botón "Deshacer" del aviso.
        return volver(f"/?aviso=descartada&id={oferta_id}")

    @app.post("/ofertas/{oferta_id}/restaurar")
    def restaurar(oferta_id: int, token: str = Form("")):
        _cambiar(oferta_id, "nueva", token, "restaurada desde la web")
        return volver("/?aviso=restaurada")

    def _cambiar(oferta_id: int, nuevo: str, token: str, nota: str) -> None:
        verificar_token(token)
        with Repositorio(ruta_db) as repo:
            obtener(repo, oferta_id)
            try:
                repo.cambiar_estado(oferta_id, nuevo, nota=nota)
            except TransicionInvalida as e:
                raise HTTPException(409, str(e)) from e

    @app.get("/ofertas/{oferta_id}/preparar", response_class=HTMLResponse)
    def preparar_form(request: Request, oferta_id: int):
        with Repositorio(ruta_db) as repo:
            g = obtener(repo, oferta_id)
        prompt = construir_prompt(g.oferta, leer(ruta_cv), leer(ruta_plantilla))
        # La web de una IA no tiene "system prompt": instrucciones y datos van juntos (como el modo manual).
        texto = f"<instrucciones>\n{prompt.instrucciones}\n</instrucciones>\n\n{prompt.mensaje}"
        return render(request, "preparar.html", g=g, prompt=texto)

    @app.post("/ofertas/{oferta_id}/preparar")
    def preparar(request: Request, oferta_id: int, token: str = Form(""), carta: str = Form("")):
        verificar_token(token)
        texto = sin_controles(normalizar_pegado(carta))
        with Repositorio(ruta_db) as repo:
            g = obtener(repo, oferta_id)
            if not texto:
                return render(request, "mensaje.html", 400, titulo="Carta vacía",
                              mensaje="Pega la carta que te devolvió la IA antes de guardar.")
            huella = construir_prompt(g.oferta, leer(ruta_cv), leer(ruta_plantilla)).huella
            try:
                repo.guardar_preparacion(oferta_id, texto, "manual", "web", huella)
            except TransicionInvalida as e:
                raise HTTPException(409, str(e)) from e
        return volver(f"/ofertas/{oferta_id}?aviso=preparada")

    @app.get("/ofertas/{oferta_id}/aprobar", response_class=HTMLResponse)
    def aprobar_form(request: Request, oferta_id: int):
        with Repositorio(ruta_db) as repo:
            g = obtener(repo, oferta_id)
            prep = repo.ultima_preparacion(oferta_id)
        if prep is None or g.estado != "preparada":
            raise HTTPException(409, f"La oferta está '{g.estado}': solo se aprueban ofertas con borrador")
        return render(request, "aprobar.html", g=g, prep=prep, alertas=alertas_borrador(prep.texto, leer(ruta_cv)))

    @app.post("/ofertas/{oferta_id}/aprobar")
    def aprobar(request: Request, oferta_id: int, token: str = Form(""), confirmacion: str = Form(""),
                version: int = Form(0)):
        verificar_token(token)
        if not confirmado(confirmacion):
            return render(request, "mensaje.html", 400, titulo="No se aprobó nada",
                          mensaje="Para aprobar tienes que escribir «si» en la casilla de confirmación.")
        with Repositorio(ruta_db) as repo:
            obtener(repo, oferta_id)
            try:
                # La versión es la que viste: si entretanto hay otra, no se aprueba un texto que no leíste.
                repo.aprobar(oferta_id, confirmado_por_persona=True, version=version)
            except TransicionInvalida as e:
                raise HTTPException(409, str(e)) from e
        return volver(f"/ofertas/{oferta_id}?aviso=aprobada")

    @app.post("/ofertas/{oferta_id}/enviada")
    def enviada(request: Request, oferta_id: int, token: str = Form(""), confirmacion: str = Form(""),
                nota: str = Form("")):
        verificar_token(token)
        if not confirmado(confirmacion):
            return render(request, "mensaje.html", 400, titulo="No se registró nada",
                          mensaje="Escribe «si» para confirmar que TÚ ya enviaste la postulación.")
        with Repositorio(ruta_db) as repo:
            obtener(repo, oferta_id)
            if repo.verificar_aprobacion(oferta_id) is False:
                raise HTTPException(409, "El texto aprobado no coincide con su huella: la base de datos fue manipulada")
            try:
                repo.marcar_enviada(oferta_id, nota=nota, confirmado_por_persona=True)
            except AlmacenamientoError as e:
                raise HTTPException(409, str(e)) from e
        return volver(f"/ofertas/{oferta_id}?aviso=enviada")

    return app
