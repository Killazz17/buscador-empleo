"""Adaptador de Arbeitnow (https://www.arbeitnow.com).

API pública y gratuita, sin API key. Sus términos piden no abusar y enlazar al sitio.
Diferencias con Remotive que este adaptador resuelve:
  - Paginación: 300 ofertas por página; la siguiente viene en links.next.
  - No tiene búsqueda en el servidor: filtramos `search` en local.
  - created_at es un timestamp Unix y remote es un booleano → los normalizamos.
"""

import time
from datetime import datetime, timezone

from buscador.fuentes.base import Fuente, FuenteError, lista_de_textos, texto
from buscador.modelos import Oferta

URL_API = "https://www.arbeitnow.com/api/job-board-api"
# Tope de páginas por ejecución (3 × 300 = 900 ofertas): suficiente y respetuoso con la API.
MAX_PAGINAS = 3
# Pausa entre páginas: cortesía con un servicio gratuito.
PAUSA_SEGUNDOS = 1


class Arbeitnow(Fuente):
    nombre = "arbeitnow"
    titulo = "Arbeitnow"
    sitio = "https://www.arbeitnow.com"

    def descargar(self, search: str | None = None) -> tuple[dict, list[Oferta]]:
        paginas = []
        url = URL_API
        # Seguimos links.next hasta que no haya más o lleguemos al tope.
        while url and len(paginas) < MAX_PAGINAS:
            if paginas:
                time.sleep(PAUSA_SEGUNDOS)
            try:
                pagina = self._pedir(url)
            except FuenteError as e:
                if not paginas:
                    raise  # sin la primera página no hay nada que guardar
                # Lo ya descargado vale: lo conservamos y avisamos.
                self.avisos.append(f"Descarga parcial: falló la página {len(paginas) + 1} ({e})")
                break
            paginas.append(pagina)
            url = self._siguiente(pagina)

        ofertas = []
        for numero, pagina in enumerate(paginas, start=1):
            jobs = pagina.get("data")
            if not isinstance(jobs, list):
                self.avisos.append(f"Página {numero} sin lista 'data' ({type(jobs).__name__}); se omite")
                continue
            ofertas += self._normalizar_todos(jobs, self._normalizar)

        if search:
            ofertas = [o for o in ofertas if self._coincide(o, search)]
        # Guardamos todas las páginas tal cual llegaron: es la respuesta "cruda".
        return {"paginas": paginas}, ofertas

    def _siguiente(self, pagina: dict) -> str | None:
        # `links` puede venir null, "x", una lista…: solo un dict tiene .get().
        links = pagina.get("links")
        siguiente = links.get("next") if isinstance(links, dict) else None
        if not siguiente:
            return None
        # Solo seguimos enlaces de la propia API: urlopen también abriría file:// u otros hosts.
        if not isinstance(siguiente, str) or not siguiente.startswith(URL_API):
            self.avisos.append(f"Se ignoró un links.next sospechoso: {siguiente!r}")
            return None
        return siguiente

    @staticmethod
    def _coincide(oferta: Oferta, search: str) -> bool:
        # Búsqueda simple sin distinguir mayúsculas en título y etiquetas.
        # (La descripción da demasiados falsos positivos; el filtrado serio llega en el paso 2.)
        texto_oferta = " ".join([oferta.titulo, *oferta.etiquetas]).lower()
        return search.lower() in texto_oferta

    @staticmethod
    def _a_iso(valor) -> str:
        """Timestamp Unix (int, float o string numérico) → ISO 8601. Si no se puede: el texto tal cual o ""."""
        # bool es subclase de int en Python: True sería el timestamp 1. Lo excluimos.
        if valor is None or isinstance(valor, bool):
            return ""
        if isinstance(valor, str):
            # try/int en vez de isdigit(): "²".isdigit() es True pero int("²") falla.
            try:
                valor = int(valor)
            except ValueError:
                return valor  # quizá ya viene como fecha ISO; no la inventamos
        if not isinstance(valor, (int, float)):
            return ""
        try:
            return datetime.fromtimestamp(valor, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""

    def _normalizar(self, job: dict) -> Oferta:
        ubicacion = texto(job.get("location"))
        remoto = job.get("remote") is True
        if remoto:
            ubicacion = f"Remote ({ubicacion})" if ubicacion else "Remote"

        return Oferta(
            id_fuente=texto(job.get("slug")),
            fuente=self.nombre,
            titulo=texto(job.get("title")),
            empresa=texto(job.get("company_name")),
            url=texto(job.get("url")),
            ubicacion=ubicacion,
            tipo=", ".join(lista_de_textos(job.get("job_types"))),
            publicada=self._a_iso(job.get("created_at")),
            descripcion=texto(job.get("description")),
            etiquetas=lista_de_textos(job.get("tags")),
            remoto=remoto,
        )
