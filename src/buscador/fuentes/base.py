"""Interfaz (el "puerto") que toda fuente de empleo debe cumplir, y utilidades comunes.

Todo lo que viene de una API externa es NO CONFIABLE: puede llegar cortado, con otra forma
o con tipos inesperados. Aquí se centraliza esa defensa para que cada fuente la herede.
"""

import http.client
import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timezone

# texto y lista_de_textos viven en modelos (también los usa Oferta.desde_dict); se reexportan
# aquí para que las fuentes los importen del mismo sitio que Fuente.
from buscador.modelos import Oferta, lista_de_textos, texto  # noqa: F401

# Un User-Agent identificable es buena educación: el dueño de la API sabe quién llama.
USER_AGENT = "buscador-empleo/0.1 (proyecto personal de aprendizaje)"
TIMEOUT_SEGUNDOS = 30


def unix_a_iso(valor) -> str:
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


def coincide_busqueda(oferta: Oferta, search: str) -> bool:
    """Filtro local de --search (para fuentes sin búsqueda en el servidor): título y etiquetas."""
    texto_oferta = " ".join([oferta.titulo, *oferta.etiquetas]).lower()
    return search.lower() in texto_oferta


class FuenteError(Exception):
    """Error al descargar de una fuente (red caída, HTTP 4xx/5xx, JSON inválido o con forma inesperada)."""


class Fuente(ABC):
    # Nombre corto, se usa en los nombres de archivo y en el campo Oferta.fuente.
    nombre: str
    # Máximo de descargas al día que permiten los términos de la API (None = sin límite declarado).
    limite_diario: int | None = None

    def __init__(self):
        # Problemas no fatales (ofertas descartadas, descarga parcial) para mostrárselos al usuario.
        self.avisos: list[str] = []

    @abstractmethod
    def descargar(self, search: str | None = None) -> tuple[dict, list[Oferta]]:
        """Devuelve (respuesta cruda, ofertas normalizadas).

        La respuesta cruda se guarda tal cual para poder reprocesarla
        sin volver a llamar a la API.
        """

    def _descargar(self, url: str) -> bytes:
        """GET a `url` y devuelve el cuerpo en bytes (JSON, RSS…). Cualquier fallo de red → FuenteError."""
        peticion = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            # El timeout evita que el script se quede colgado si la red falla.
            with urllib.request.urlopen(peticion, timeout=TIMEOUT_SEGUNDOS) as respuesta:
                return respuesta.read()
        # El orden importa: HTTPError es un tipo de URLError, y URLError un tipo de OSError.
        except urllib.error.HTTPError as e:
            raise FuenteError(f"{self.nombre} respondió HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise FuenteError(f"No se pudo conectar con {self.nombre}: {e.reason}") from e
        # Timeouts y cortes mientras se LEE el cuerpo no son URLError: hay que capturarlos aparte.
        except (OSError, http.client.HTTPException) as e:
            raise FuenteError(f"Fallo de red con {self.nombre}: {e!r}") from e

    def _pedir(self, url: str) -> dict:
        """GET a `url` y devuelve el JSON como dict. Cualquier fallo se convierte en FuenteError."""
        cuerpo = self._descargar(url)
        try:
            datos = json.loads(cuerpo)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise FuenteError(f"{self.nombre} devolvió algo que no es JSON válido") from e

        # JSON válido no significa forma correcta: [] o null también son JSON.
        if not isinstance(datos, dict):
            raise FuenteError(f"{self.nombre} devolvió {type(datos).__name__} en vez de un objeto JSON")
        return datos

    def _normalizar_todos(self, jobs: list, normalizar: Callable[[dict], Oferta]) -> list[Oferta]:
        """Normaliza cada job por separado: uno malo se descarta con aviso, sin tumbar a los demás."""
        ofertas = []
        for i, job in enumerate(jobs):
            if not isinstance(job, dict):
                self.avisos.append(f"Oferta #{i} descartada: no es un objeto ({type(job).__name__})")
                continue
            try:
                ofertas.append(normalizar(job))
            except (TypeError, ValueError, AttributeError) as e:
                self.avisos.append(f"Oferta #{i} descartada al normalizar: {e!r}")
        return ofertas
