"""Adaptador de Get on Board (https://www.getonbrd.com): bolsa de empleo tech de Latinoamérica.

API pública (JSON:API), sin API key. Diferencias que resuelve:
  - La empresa viene como referencia: se pide expandida con expand=["company"].
  - Paginación por número de página con meta.total_pages.
  - La descripción está repartida en secciones (requisitos, funciones, deseables, beneficios).
  - remote_modality define la ubicación que verá la regla del perfil:
      fully_remote → remoto; "Remote" si no limita países
      remote_local → remoto SOLO para residentes de `countries` (p. ej. "Chile" → restringido)
      hybrid / no_remote → presencial en `countries`
"""

import time
import urllib.parse

from buscador.fuentes.base import Fuente, FuenteError, coincide_busqueda, lista_de_textos, texto, unix_a_iso
from buscador.modelos import Oferta

URL_API = "https://www.getonbrd.com/api/v0/categories/programming/jobs"
POR_PAGINA = 50
MAX_PAGINAS = 3  # 150 ofertas más recientes por ejecución: suficiente y respetuoso con la API
PAUSA_SEGUNDOS = 1
SECCIONES = ("description", "projects", "functions", "desirable", "benefits")


class GetOnBoard(Fuente):
    nombre = "getonboard"

    def descargar(self, search: str | None = None) -> tuple[dict, list[Oferta]]:
        paginas = []
        total = 1
        while len(paginas) < min(total, MAX_PAGINAS):
            if paginas:
                time.sleep(PAUSA_SEGUNDOS)
            pagina = self._pedir(self._url(len(paginas) + 1))
            if not isinstance(pagina.get("data"), list):
                raise FuenteError(f"{self.nombre} devolvió 'data' como {type(pagina.get('data')).__name__}")
            paginas.append(pagina)
            meta = pagina.get("meta") if isinstance(pagina.get("meta"), dict) else {}
            total = meta.get("total_pages") if isinstance(meta.get("total_pages"), int) else 1

        ofertas = [o for p in paginas for o in self._normalizar_todos(p["data"], self._normalizar)]
        if search:
            ofertas = [o for o in ofertas if coincide_busqueda(o, search)]
        return {"paginas": paginas}, ofertas

    @staticmethod
    def _url(pagina: int) -> str:
        parametros = {"per_page": POR_PAGINA, "page": pagina, "expand": '["company"]'}
        return f"{URL_API}?{urllib.parse.urlencode(parametros)}"

    def _normalizar(self, job: dict) -> Oferta:
        a = job.get("attributes") if isinstance(job.get("attributes"), dict) else {}
        empresa = ((a.get("company") or {}).get("data") or {}).get("attributes") or {}
        modalidad = texto(a.get("remote_modality"))
        paises = [p for p in lista_de_textos(a.get("countries")) if p.strip()]
        concretos = [p for p in paises if p.lower() != "remote"]
        remoto = modalidad in ("fully_remote", "remote_local") or a.get("remote") is True
        ubicacion = ", ".join(concretos) if concretos else ("Remote" if remoto else "")
        return Oferta(
            id_fuente=texto(job.get("id")),
            fuente=self.nombre,
            titulo=texto(a.get("title")),
            empresa=texto(empresa.get("name")),
            url=texto((job.get("links") or {}).get("public_url")),
            ubicacion=ubicacion,
            tipo=modalidad,
            publicada=unix_a_iso(a.get("published_at")),
            descripcion="\n".join(texto(a.get(s)) for s in SECCIONES if a.get(s)),
            etiquetas=lista_de_textos(a.get("category_name")),
            remoto=remoto,
        )
