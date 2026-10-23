"""Paso 2 del pipeline: puntuar cada oferta contra el perfil.

Funciones PURAS (sin red ni archivos): misma entrada → misma salida. Por eso se testean fácil.
La puntuación es por reglas, no por IA: cada punto lleva su motivo para que el ranking sea
explicable y se pueda ajustar editando config/perfil.toml.
"""

import html
import re
from dataclasses import dataclass, field
from functools import lru_cache

from buscador.modelos import Oferta
from buscador.perfil import Perfil

# Palabras muy frecuentes en alemán y raras en inglés/español. Si aparecen muchas,
# la descripción está en alemán y lo normal es que exijan hablarlo.
PALABRAS_ALEMAN = frozenset("und der die das wir mit für sie ist nicht eine dich dir bei du uns ihr".split())
MIN_PALABRAS_ALEMAN = 5

# Un idioma solo penaliza si la frase lo EXIGE ("Fluent German required", "Deutsch C1")
# y no lo niega ni lo hace opcional ("No German required", "German is a plus").
NIVEL_EXIGIDO = re.compile(r"\b(?:fluen|native|requir|must|proficien|mandatory|essential|speak|c[12]\b|b2\b"
                           r"|level|fließend|fliessend|verhandlungssicher|sehr gut)|kenntnisse", re.IGNORECASE)
NEGADO_U_OPCIONAL = re.compile(r"\b(?:no|not|without|kein|keine|keinen)\b|\bplus\b|nice[ -]to[ -]have"
                               r"|optional|advantage|bonus|von vorteil|wünschenswert", re.IGNORECASE)
SEPARADOR_FRASES = re.compile(r"[.;!?\n•]")
# "We do NOT offer visa sponsorship", "you MUST HAVE a valid work permit": mencionan la reubicación
# para negarla o exigirla. Esas frases no cuentan como ayuda para mudarte.
REUBICACION_NEGADA = re.compile(r"\b(?:no|not|cannot|can't|unable|without|won't|don't|doesn't)\b"
                                r"|must (?:already )?have|required|requirement", re.IGNORECASE)


@dataclass
class Evaluacion:
    oferta: Oferta
    puntuacion: int = 0
    motivos: list[str] = field(default_factory=list)
    descartada: bool = False

    def sumar(self, puntos: int, motivo: str) -> None:
        self.puntuacion += puntos
        self.motivos.append(f"{motivo} {puntos:+d}")  # +d muestra el signo: "+6", "-8"


def limpiar_html(texto: str) -> str:
    """Quita etiquetas (y sus atributos) y decodifica entidades: "<li>A&amp;B</li>" → " A&B ".

    Solo cuenta como etiqueta un "<" seguido de letra o "/": así "<2 years of Java" no se borra.
    Dos pasadas: algunas fuentes mandan el HTML ESCAPADO ("&lt;h2&gt;"); la 1ª lo decodifica a
    etiquetas reales y la 2ª las quita.
    """
    for _ in range(2):
        sin_etiquetas = re.sub(r"</?[A-Za-z][^<>]*>|<!--.*?-->", " ", texto, flags=re.DOTALL)
        texto = html.unescape(sin_etiquetas)
    return texto.replace("\xa0", " ")  # &nbsp; → espacio normal


@lru_cache(maxsize=None)
def _patron(termino: str) -> re.Pattern:
    """Regex de "palabra completa" que también funciona con símbolos (c++, next.js, ci/cd).

    \\b no sirve: en "c++" no hay frontera de palabra después del "+". Por eso se usan
    lookarounds:
      - antes no puede haber letra, dígito, "_", "+", "#" ni "." ("next.js" no coincide en "xnext.js");
      - después no puede haber LETRA, "+" ni "#" → "java" no coincide en "javascript",
        pero sí en "Python3" o "C++17" (un número de versión detrás está permitido).
    Los espacios del término aceptan cualquier espacio en blanco: "rest api" encuentra "REST\\nAPI".
    """
    cuerpo = re.escape(termino).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![\w+#.]){cuerpo}(?![^\W\d_]|[+#])", re.IGNORECASE)


def _contiene(texto: str, termino: str) -> bool:
    return _patron(termino).search(texto) is not None


def _idioma_exigido(texto: str, idiomas: list[str]) -> str | None:
    """Primer idioma excluyente que la oferta EXIGE, mirando frase a frase."""
    for frase in SEPARADOR_FRASES.split(texto):
        for idioma in idiomas:
            if (_contiene(frase, idioma) and not NEGADO_U_OPCIONAL.search(frase)
                    and NIVEL_EXIGIDO.search(frase)):
                return idioma
    return None


def _parece_aleman(texto: str) -> bool:
    palabras = re.findall(r"[a-zäöüß]+", texto.lower())
    return sum(p in PALABRAS_ALEMAN for p in palabras) >= MIN_PALABRAS_ALEMAN


def _reubicacion_ofrecida(texto: str, terminos: list[str]) -> str | None:
    """Primer término de reubicación que aparece en una frase que NO lo niega ni lo exige."""
    for frase in SEPARADOR_FRASES.split(texto):
        for termino in terminos:
            if _contiene(frase, termino) and not REUBICACION_NEGADA.search(frase):
                return termino
    return None


def _descartar_por_ubicacion(ev: Evaluacion, perfil: Perfil, cuerpo: str) -> bool:
    """Aplica la regla de ubicación del perfil. Devuelve True si la oferta queda descartada.

    - En tu país: vale cualquier modalidad.
    - Fuera y remoto: vale si la ubicación nombra una región aceptada (Worldwide, LATAM…).
      Si nombra OTRAS regiones ("USA", "Europe") está restringido → descartada.
      Si no nombra ninguna ("Remote", "Remote (Zug)": solo dónde está la empresa) → se mantiene con aviso.
    - Fuera y presencial/híbrido: vale solo si la descripción ofrece reubicación (visa, relocation).
    """
    if not perfil.pais:
        return False
    oferta = ev.oferta
    ubicacion = oferta.ubicacion.strip()
    if any(_contiene(ubicacion, t) for t in perfil.pais):
        ev.motivos.append("en tu país")
        return False
    if oferta.remoto:
        # Restricciones escritas en el título o la descripción ("Remote from EU", "within EU") mandan.
        restriccion = next((r for r in perfil.restricciones_remoto if _contiene(f"{oferta.titulo}\n{cuerpo}", r)), None)
        if restriccion:
            ev.descartada = True
            ev.motivos.append(f"remoto restringido ({restriccion})")
            return True
        region = next((r for r in perfil.remoto_aceptado if _contiene(ubicacion, r)), None)
        if region:
            ev.motivos.append(f"remoto abierto ({region})")
            return False
        if not ubicacion or ubicacion.lower().startswith("remote"):
            ev.sumar(perfil.puntos_remoto_dudoso, "verifica si aceptan Costa Rica")
            return False
        ev.descartada = True
        ev.motivos.append(f"remoto restringido a: {ubicacion}")
        return True
    senal = _reubicacion_ofrecida(cuerpo, perfil.reubicacion)
    if senal:
        ev.motivos.append(f"presencial fuera, pero ofrece reubicación ({senal})")
        return False
    ev.descartada = True
    ev.motivos.append(f"presencial fuera de tu país sin reubicación ({ubicacion or 'sin ubicación'})")
    return True


def evaluar(oferta: Oferta, perfil: Perfil) -> Evaluacion:
    ev = Evaluacion(oferta)
    titulo = oferta.titulo
    cuerpo = " ".join([*oferta.etiquetas, limpiar_html(oferta.descripcion)])

    # 1. Rol que no es para el candidato → fuera, no hace falta seguir.
    for rol in perfil.roles_excluidos:
        if _contiene(titulo, rol):
            ev.descartada = True
            ev.motivos.append(f"rol excluido: {rol}")
            return ev

    # 1b. Ubicación: fuera de tu país, solo remoto abierto a tu región o presencial con reubicación.
    if _descartar_por_ubicacion(ev, perfil, cuerpo):
        return ev

    # 2. Tecnologías: cada una (con sus alias) cuenta una sola vez, con su mejor coincidencia.
    puntos_cuerpo = 0
    for tecnologia, peso in perfil.tecnologias.items():
        terminos = [tecnologia, *perfil.alias.get(tecnologia, [])]
        if any(_contiene(titulo, t) for t in terminos):
            ev.sumar(peso * 2, f"{tecnologia} (título)")
        elif any(_contiene(cuerpo, t) for t in terminos):
            ev.sumar(peso, tecnologia)
            puntos_cuerpo += peso
    # Algunas descripciones listan 15 tecnologías: sin tope, ganarían a un título que encaja de verdad.
    tope = perfil.puntos_tope_descripcion
    if tope is not None and puntos_cuerpo > tope:
        ev.sumar(tope - puntos_cuerpo, "tope tecnologías en descripción")

    # 3. Nivel: solo el título ("work with senior engineers" en la descripción no dice nada del puesto).
    bonus = next((n for n in perfil.nivel_bonus if _contiene(titulo, n)), None)
    if bonus:
        ev.sumar(perfil.puntos_nivel_bonus, f"nivel {bonus}")
    penaliza = next((n for n in perfil.nivel_penaliza if _contiene(titulo, n)), None)
    if penaliza:
        ev.sumar(perfil.puntos_nivel_penaliza, f"nivel {penaliza}")

    # 4. Idioma: se penaliza una sola vez, aunque haya varias señales.
    # El título va como frase aparte (con "\n") para no mezclarlo con la primera frase de la descripción.
    idioma = _idioma_exigido(f"{titulo}\n{cuerpo}", perfil.idiomas_excluyentes)
    if idioma:
        ev.sumar(perfil.puntos_idioma, f"idioma: {idioma}")
    elif _parece_aleman(cuerpo):
        ev.sumar(perfil.puntos_idioma, "descripción en alemán")

    # 5. Modalidad.
    if oferta.remoto:
        ev.sumar(perfil.puntos_remoto, "remoto")

    return ev


def evaluar_todas(ofertas: list[Oferta], perfil: Perfil, minimo: int | None = None) -> list[Evaluacion]:
    """Puntúa, descarta las que no llegan al mínimo, ordena de mejor a peor y quita duplicados.

    Se ordena ANTES de deduplicar: así, entre dos copias de la misma oferta, sobrevive la mejor
    puntuada (p. ej. la copia marcada como remota), no la que apareció primero.
    """
    return separar(ofertas, perfil, minimo)[0]


def rechazadas(ofertas: list[Oferta], perfil: Perfil, minimo: int | None = None) -> list[Evaluacion]:
    """Las que NO pasan: descartadas por una regla o por debajo del mínimo."""
    return separar(ofertas, perfil, minimo)[1]


def separar(ofertas: list[Oferta], perfil: Perfil,
            minimo: int | None = None) -> tuple[list[Evaluacion], list[Evaluacion]]:
    """Evalúa UNA vez y devuelve (aceptadas ordenadas y sin duplicados, rechazadas)."""
    minimo = perfil.puntuacion_minima if minimo is None else minimo
    evaluaciones = [evaluar(o, perfil) for o in ofertas]
    candidatas = [e for e in evaluaciones if not e.descartada and e.puntuacion >= minimo]
    fuera = [e for e in evaluaciones if e.descartada or e.puntuacion < minimo]
    # sort es estable: a igual puntuación se conserva el orden original.
    candidatas.sort(key=lambda e: e.puntuacion, reverse=True)

    vistas: set[tuple[str, ...]] = set()
    resultado = []
    for ev in candidatas:
        claves = _claves_duplicado(ev.oferta)
        if claves & vistas:
            continue
        vistas |= claves
        resultado.append(ev)
    return resultado, fuera


def _claves_duplicado(oferta: Oferta) -> set[tuple[str, ...]]:
    """Dos formas de duplicado:
      - mismo id en la misma fuente (la oferta vino en varios archivos del día);
      - misma empresa, título y ubicación (Arbeitnow la publica en .nl y .co.uk con ids distintos).
    Sin empresa no se compara el contenido: dos "Backend Developer" anónimos pueden ser ofertas distintas.
    """
    claves = {("id", oferta.fuente, oferta.id_fuente)}
    if oferta.empresa.strip():
        claves.add(("contenido", oferta.empresa.casefold(), oferta.titulo.casefold(), oferta.ubicacion.casefold()))
    return claves
