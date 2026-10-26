"""Adaptador de Remotive (https://remotive.com).

API pública y gratuita, sin API key. Sus términos piden:
  - enlazar a Remotive como fuente de las ofertas,
  - no llamarla más de ~4 veces al día (los datos se actualizan con retraso).
"""

import urllib.parse

from buscador.fuentes.base import Fuente, FuenteError, lista_de_textos, texto
from buscador.modelos import Oferta

URL_API = "https://remotive.com/api/remote-jobs"


class Remotive(Fuente):
    nombre = "remotive"
    titulo = "Remotive"
    sitio = "https://remotive.com"
    limite_diario = 4

    def descargar(self, search: str | None = None) -> tuple[dict, list[Oferta]]:
        crudo = self._pedir(self._construir_url(search))
        jobs = crudo.get("jobs")
        if not isinstance(jobs, list):
            raise FuenteError(f"Remotive devolvió 'jobs' como {type(jobs).__name__}, se esperaba una lista")
        return crudo, self._normalizar_todos(jobs, self._normalizar)

    def _construir_url(self, search: str | None) -> str:
        # urlencode escapa los caracteres especiales (espacios, tildes, &…).
        parametros = {"category": "software-dev"}
        if search:
            parametros["search"] = search
        return f"{URL_API}?{urllib.parse.urlencode(parametros)}"

    def _normalizar(self, job: dict) -> Oferta:
        # texto()/lista_de_textos() en vez de .get(k, ""): si la clave existe pero es null, .get devuelve None.
        return Oferta(
            id_fuente=texto(job.get("id")),
            fuente=self.nombre,
            titulo=texto(job.get("title")),
            empresa=texto(job.get("company_name")),
            url=texto(job.get("url")),
            ubicacion=texto(job.get("candidate_required_location")),
            tipo=texto(job.get("job_type")),
            publicada=texto(job.get("publication_date")),
            descripcion=texto(job.get("description")),
            etiquetas=lista_de_textos(job.get("tags")),
            remoto=True,  # Remotive solo publica ofertas remotas
        )
