"""Adaptador de We Work Remotely (https://weworkremotely.com): feeds RSS públicos por categoría.

Diferencias que resuelve:
  - Es XML (RSS), no JSON: se lee con xml.etree de la librería estándar (no resuelve entidades
    externas, así que un feed malicioso no puede leer archivos locales: sin riesgo de XXE).
  - El título viene como "Empresa: Puesto".
  - <region> dice desde dónde aceptan candidatos ("Anywhere in the World", "USA Only"…): es justo
    lo que necesita la regla de ubicación del perfil.
  - Un mismo empleo puede estar en dos categorías: se quitan duplicados por <guid>.
"""

import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from buscador.fuentes.base import Fuente, FuenteError, coincide_busqueda, lista_de_textos, texto
from buscador.modelos import Oferta

# Solo las categorías que encajan con el perfil (backend y full-stack). Una petición por feed.
FEEDS = [
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
]
PAUSA_SEGUNDOS = 1


class WeWorkRemotely(Fuente):
    nombre = "weworkremotely"

    def descargar(self, search: str | None = None) -> tuple[dict, list[Oferta]]:
        feeds: dict[str, str] = {}
        items: list[ET.Element] = []
        for i, url in enumerate(FEEDS):
            if i:
                time.sleep(PAUSA_SEGUNDOS)
            cuerpo = self._descargar(url)
            try:
                raiz = ET.fromstring(cuerpo)
            except ET.ParseError as e:
                raise FuenteError(f"{self.nombre} devolvió un RSS que no es XML válido: {e}") from e
            feeds[url] = cuerpo.decode("utf-8", errors="replace")  # crudo, para reprocesar sin pedir
            items += raiz.iter("item")

        vistos: set[str] = set()
        ofertas = []
        for oferta in self._normalizar_todos([self._a_dict(item) for item in items], self._normalizar):
            if oferta.id_fuente in vistos:
                continue
            vistos.add(oferta.id_fuente)
            ofertas.append(oferta)
        if search:
            ofertas = [o for o in ofertas if coincide_busqueda(o, search)]
        return {"feeds": feeds}, ofertas

    @staticmethod
    def _a_dict(item: ET.Element) -> dict:
        return {hijo.tag: hijo.text for hijo in item}

    def _normalizar(self, item: dict) -> Oferta:
        empresa, _, puesto = texto(item.get("title")).partition(": ")
        if not puesto:  # título sin "Empresa: " delante
            empresa, puesto = "", empresa
        region = ", ".join(p for p in (texto(item.get("region")), texto(item.get("country"))) if p.strip())
        etiquetas = [s.strip() for s in texto(item.get("skills")).split(",") if s.strip()]
        etiquetas += lista_de_textos(item.get("category"))
        return Oferta(
            id_fuente=texto(item.get("guid") or item.get("link")),
            fuente=self.nombre,
            titulo=puesto.strip(),
            empresa=empresa.strip(),
            url=texto(item.get("link")),
            ubicacion=region,
            tipo=texto(item.get("type")),
            publicada=self._fecha(item.get("pubDate")),
            descripcion=texto(item.get("description")),  # el XML ya desescapó &lt;p&gt; → <p>
            etiquetas=etiquetas,
            remoto=True,  # We Work Remotely solo publica empleos remotos
        )

    @staticmethod
    def _fecha(valor) -> str:
        """RFC 822 ("Thu, 22 Oct 2026 10:00:00 +0000") → ISO 8601."""
        try:
            return parsedate_to_datetime(texto(valor)).isoformat()
        except (TypeError, ValueError):
            return ""
