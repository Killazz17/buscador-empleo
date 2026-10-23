"""Almacenamiento en SQLite tras un repositorio (patrón Repository).

Todo el SQL del proyecto vive AQUÍ. El resto del código habla con métodos como
`guardar_evaluaciones()` o `cambiar_estado()`, sin saber que debajo hay SQLite.
Si un día se migra a PostgreSQL, solo cambia este archivo.

Ciclo de vida de una oferta (máquina de estados):

    nueva ──► preparada ──► aprobada ──► enviada (final)
      │  ▲        │             │
      ▼  │        ▼             ▼
    descartada ◄──┴─────────────┘

La regla de oro del proyecto queda garantizada POR DISEÑO: a 'enviada' solo se llega
desde 'aprobada', y 'aprobada' es una decisión del usuario.
"""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from buscador.filtros import Evaluacion
from buscador.modelos import Oferta

ESTADOS = frozenset({"nueva", "descartada", "preparada", "aprobada", "enviada"})
TRANSICIONES: dict[str, frozenset[str]] = {
    "nueva": frozenset({"descartada", "preparada"}),
    "preparada": frozenset({"aprobada", "descartada"}),
    "aprobada": frozenset({"enviada", "descartada"}),  # el usuario puede cambiar de idea antes de enviar
    "descartada": frozenset({"nueva"}),                 # restaurar
    "enviada": frozenset(),                             # final: lo enviado no se "desenvía"
}
# Solo se puede (re)preparar un borrador si la oferta está en uno de estos estados.
ESTADOS_PREPARABLES = frozenset({"nueva", "preparada"})
# Estos estados exigen datos (qué se preparó, qué se aprobó): no se alcanzan con cambiar_estado().
METODO_PARA = {
    "preparada": "guardar_preparacion() (comando `preparar`)",
    "aprobada": "aprobar() (comando `aprobar`)",
    "enviada": "marcar_enviada() (comando `enviada`)",
}

# Migraciones: la posición en la lista es la versión. NUNCA se edita una migración ya publicada;
# los cambios de esquema se añaden como una migración nueva al final.
MIGRACIONES = [
    # v1: ofertas + historial de cambios de estado.
    """
    CREATE TABLE ofertas (
        id           INTEGER PRIMARY KEY,
        fuente       TEXT    NOT NULL,
        id_fuente    TEXT    NOT NULL,
        titulo       TEXT    NOT NULL,
        empresa      TEXT    NOT NULL DEFAULT '',
        url          TEXT    NOT NULL DEFAULT '',
        ubicacion    TEXT    NOT NULL DEFAULT '',
        tipo         TEXT    NOT NULL DEFAULT '',
        publicada    TEXT    NOT NULL DEFAULT '',
        descripcion  TEXT    NOT NULL DEFAULT '',
        etiquetas    TEXT    NOT NULL DEFAULT '[]',          -- lista JSON
        remoto       INTEGER NOT NULL DEFAULT 0 CHECK (remoto IN (0, 1)),
        puntuacion   INTEGER NOT NULL,
        motivos      TEXT    NOT NULL DEFAULT '[]',          -- lista JSON
        -- Segunda línea de defensa: aunque alguien salte el repositorio, la BD rechaza estados inventados.
        estado       TEXT    NOT NULL DEFAULT 'nueva'
                     CHECK (estado IN ('nueva', 'descartada', 'preparada', 'aprobada', 'enviada')),
        primera_vez  TEXT    NOT NULL,                       -- ISO 8601
        ultima_vez   TEXT    NOT NULL,
        UNIQUE (fuente, id_fuente)
    );
    -- La consulta más habitual es "las nuevas, de mejor a peor": este índice la sirve directamente.
    CREATE INDEX idx_ofertas_estado_puntuacion ON ofertas (estado, puntuacion DESC);

    CREATE TABLE historial (
        id         INTEGER PRIMARY KEY,
        oferta_id  INTEGER NOT NULL REFERENCES ofertas (id) ON DELETE CASCADE,
        de         TEXT    NOT NULL,
        a          TEXT    NOT NULL,
        fecha      TEXT    NOT NULL,
        nota       TEXT    NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_historial_oferta ON historial (oferta_id);
    """,
    # v2 (revisión de ecc:database-reviewer): la BD hace cumplir la máquina de estados y protege
    # el historial, aunque alguien escriba SQL directo saltándose el repositorio.
    # Ojo: este SQL repite a mano TRANSICIONES. Un test recorre TODOS los pares de estados para
    # garantizar que ambos coinciden; si cambias TRANSICIONES, añade una migración nueva.
    """
    CREATE TRIGGER ofertas_insertar_solo_nueva
    BEFORE INSERT ON ofertas
    WHEN NEW.estado <> 'nueva'
    BEGIN
        SELECT RAISE(ABORT, 'una oferta debe empezar en estado nueva');
    END;

    -- "UPDATE OF estado": solo se activa si el UPDATE toca la columna estado.
    CREATE TRIGGER ofertas_transicion_valida
    BEFORE UPDATE OF estado ON ofertas
    WHEN NEW.estado <> OLD.estado AND NOT (
           (OLD.estado = 'nueva'      AND NEW.estado IN ('descartada', 'preparada'))
        OR (OLD.estado = 'preparada'  AND NEW.estado IN ('aprobada', 'descartada'))
        OR (OLD.estado = 'aprobada'   AND NEW.estado IN ('enviada', 'descartada'))
        OR (OLD.estado = 'descartada' AND NEW.estado = 'nueva')
    )
    BEGIN
        SELECT RAISE(ABORT, 'transición de estado no permitida');
    END;

    -- El historial es una auditoría: solo se añade. Tampoco se puede borrar una oferta con historial
    -- (no dependemos de PRAGMA foreign_keys, que una conexión externa podría tener apagado).
    CREATE TRIGGER historial_sin_modificar BEFORE UPDATE ON historial
    BEGIN
        SELECT RAISE(ABORT, 'el historial es solo de añadir');
    END;

    CREATE TRIGGER historial_sin_borrar BEFORE DELETE ON historial
    BEGIN
        SELECT RAISE(ABORT, 'el historial es solo de añadir');
    END;

    CREATE TRIGGER ofertas_con_historial_no_se_borran
    BEFORE DELETE ON ofertas
    WHEN EXISTS (SELECT 1 FROM historial WHERE oferta_id = OLD.id)
    BEGIN
        SELECT RAISE(ABORT, 'una oferta con historial no se puede borrar');
    END;
    """,
    # v3 (paso 5): qué borrador escribió la IA y qué versión aprobó el usuario.
    # 'preparada' exige un borrador, 'aprobada' exige aprobar el ÚLTIMO, 'enviada' exige una aprobación.
    """
    CREATE TABLE preparaciones (
        id              INTEGER PRIMARY KEY,
        oferta_id       INTEGER NOT NULL REFERENCES ofertas (id),
        version         INTEGER NOT NULL CHECK (version >= 1),
        texto           TEXT    NOT NULL CHECK (length(trim(texto)) > 0),
        proveedor       TEXT    NOT NULL,                  -- "claude", "simulado"…
        modelo          TEXT    NOT NULL,                  -- el que respondió de verdad
        prompt_huella   TEXT    NOT NULL,                  -- versión de la plantilla usada
        tokens_entrada  INTEGER NOT NULL DEFAULT 0,
        tokens_salida   INTEGER NOT NULL DEFAULT 0,
        generada_en     TEXT    NOT NULL,
        UNIQUE (oferta_id, version)
    );
    CREATE TABLE aprobaciones (
        id                INTEGER PRIMARY KEY,
        oferta_id         INTEGER NOT NULL REFERENCES ofertas (id),
        preparacion_id    INTEGER NOT NULL REFERENCES preparaciones (id),
        contenido_huella  TEXT    NOT NULL,                -- sha256 del texto aprobado: lo aprobado no cambia
        aprobada_en       TEXT    NOT NULL
    );
    CREATE INDEX idx_aprobaciones_oferta ON aprobaciones (oferta_id);

    CREATE TRIGGER preparaciones_solo_si_preparable
    BEFORE INSERT ON preparaciones
    WHEN COALESCE((SELECT estado FROM ofertas WHERE id = NEW.oferta_id), '') NOT IN ('nueva', 'preparada')
    BEGIN
        SELECT RAISE(ABORT, 'solo se preparan ofertas nuevas o ya preparadas');
    END;

    CREATE TRIGGER aprobaciones_solo_la_ultima_de_una_preparada
    BEFORE INSERT ON aprobaciones
    WHEN COALESCE((SELECT estado FROM ofertas WHERE id = NEW.oferta_id), '') <> 'preparada'
      OR NEW.preparacion_id <> COALESCE((SELECT max(id) FROM preparaciones WHERE oferta_id = NEW.oferta_id), -1)
    BEGIN
        SELECT RAISE(ABORT, 'solo se aprueba la última versión de una oferta preparada');
    END;

    CREATE TRIGGER ofertas_preparada_requiere_borrador
    BEFORE UPDATE OF estado ON ofertas
    WHEN NEW.estado = 'preparada' AND OLD.estado <> 'preparada'
     AND NOT EXISTS (SELECT 1 FROM preparaciones WHERE oferta_id = NEW.id)
    BEGIN
        SELECT RAISE(ABORT, 'no hay borrador: una oferta no puede estar preparada sin preparación');
    END;

    CREATE TRIGGER ofertas_aprobada_requiere_aprobacion
    BEFORE UPDATE OF estado ON ofertas
    WHEN NEW.estado = 'aprobada' AND OLD.estado <> 'aprobada'
     AND NOT EXISTS (SELECT 1 FROM aprobaciones
                     WHERE oferta_id = NEW.id
                       AND preparacion_id = (SELECT max(id) FROM preparaciones WHERE oferta_id = NEW.id))
    BEGIN
        SELECT RAISE(ABORT, 'no hay aprobación de la última versión del borrador');
    END;

    CREATE TRIGGER ofertas_enviada_requiere_aprobacion
    BEFORE UPDATE OF estado ON ofertas
    WHEN NEW.estado = 'enviada' AND OLD.estado <> 'enviada'
     AND NOT EXISTS (SELECT 1 FROM aprobaciones WHERE oferta_id = NEW.id)
    BEGIN
        SELECT RAISE(ABORT, 'no se puede marcar como enviada sin una aprobación');
    END;

    -- Borradores y aprobaciones son registro histórico: solo se añaden.
    CREATE TRIGGER preparaciones_sin_modificar BEFORE UPDATE ON preparaciones
    BEGIN SELECT RAISE(ABORT, 'las preparaciones son solo de añadir'); END;
    CREATE TRIGGER preparaciones_sin_borrar BEFORE DELETE ON preparaciones
    BEGIN SELECT RAISE(ABORT, 'las preparaciones son solo de añadir'); END;
    CREATE TRIGGER aprobaciones_sin_modificar BEFORE UPDATE ON aprobaciones
    BEGIN SELECT RAISE(ABORT, 'las aprobaciones son solo de añadir'); END;
    CREATE TRIGGER aprobaciones_sin_borrar BEFORE DELETE ON aprobaciones
    BEGIN SELECT RAISE(ABORT, 'las aprobaciones son solo de añadir'); END;
    """,
]


class AlmacenamientoError(Exception):
    """La base de datos no se puede abrir, es de otra versión o la consulta no es válida."""


class OfertaNoEncontrada(AlmacenamientoError):
    pass


class TransicionInvalida(AlmacenamientoError):
    pass


@dataclass
class OfertaGuardada:
    id: int                 # id interno de la BD: el que usa el usuario en `descartar 12`
    oferta: Oferta
    puntuacion: int
    motivos: list[str]
    estado: str
    primera_vez: str
    ultima_vez: str


@dataclass
class Preparacion:
    id: int
    oferta_id: int
    version: int
    texto: str
    proveedor: str
    modelo: str
    prompt_huella: str
    tokens_entrada: int
    tokens_salida: int
    generada_en: str


@dataclass
class CambioEstado:
    de: str
    a: str
    fecha: str
    nota: str


@dataclass
class ResumenGuardado:
    ids_nuevos: set[int]    # ofertas vistas por primera vez en este guardado
    actualizadas: int       # ya conocidas: se refrescan sus datos, NO su estado


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repositorio:
    VERSION_ESQUEMA = len(MIGRACIONES)

    def __init__(self, ruta: Path, timeout: float = 5.0):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        # timeout: cuántos segundos esperar si otro proceso tiene la BD bloqueada antes de rendirse.
        self._con = sqlite3.connect(self.ruta, timeout=timeout)
        # Las filas se leen por nombre de columna (fila["titulo"]) en vez de por posición.
        self._con.row_factory = sqlite3.Row
        try:
            # SQLite trae las claves foráneas DESACTIVADAS por defecto; hay que pedirlas en cada conexión.
            self._con.execute("PRAGMA foreign_keys = ON")
            self._migrar()
        # OperationalError es un tipo de DatabaseError: va primero para no confundir "ocupada" con "corrupta".
        except sqlite3.OperationalError as e:
            self._con.close()
            raise AlmacenamientoError(f"no se pudo abrir {self.ruta} (¿está ocupada por otro proceso?): {e}") from e
        except sqlite3.DatabaseError as e:
            self._con.close()
            raise AlmacenamientoError(f"{self.ruta} no es una base de datos válida: {e}") from e
        except AlmacenamientoError:
            self._con.close()
            raise

    # `with Repositorio(ruta) as repo:` cierra la conexión aunque haya errores.
    def __enter__(self) -> "Repositorio":
        return self

    def __exit__(self, *_) -> None:
        self.cerrar()

    def cerrar(self) -> None:
        self._con.close()

    @contextmanager
    def _escritura(self) -> Iterator[None]:
        """Transacción de escritura que bloquea DESDE EL PRINCIPIO.

        Con el modo por defecto de sqlite3, el BEGIN implícito llega con el primer INSERT/UPDATE,
        así que un SELECT previo (p. ej. leer el estado actual) quedaría FUERA de la transacción:
        otro proceso podría cambiar el dato entre la lectura y la escritura. BEGIN IMMEDIATE toma
        el bloqueo de escritura antes de leer, y `with self._con` hace COMMIT o ROLLBACK al salir.
        """
        try:
            with self._con:
                self._con.execute("BEGIN IMMEDIATE")
                yield
        except sqlite3.OperationalError as e:
            raise AlmacenamientoError(
                f"no se pudo escribir en la base de datos (¿está ocupada por otro proceso?): {e}") from e

    # --- Esquema ---------------------------------------------------------------------------

    def version_esquema(self) -> int:
        # user_version es un entero libre en la cabecera del archivo: lo usamos como versión del esquema.
        return self._con.execute("PRAGMA user_version").fetchone()[0]

    def _migrar(self) -> None:
        version = self.version_esquema()
        if version > self.VERSION_ESQUEMA:
            raise AlmacenamientoError(
                f"{self.ruta} tiene el esquema v{version}, pero este código solo conoce hasta "
                f"v{self.VERSION_ESQUEMA}. ¿Es de una versión más nueva del programa?")
        for numero in range(version, self.VERSION_ESQUEMA):
            # Cada migración y su cambio de versión van en UNA transacción: o se aplica entera o nada.
            try:
                self._con.executescript(
                    f"BEGIN;\n{MIGRACIONES[numero]}\nPRAGMA user_version = {numero + 1};\nCOMMIT;")
            except sqlite3.Error:
                if self._con.in_transaction:
                    self._con.rollback()
                raise

    # --- Ofertas ---------------------------------------------------------------------------

    def guardar_evaluaciones(self, evaluaciones: list[Evaluacion], ahora: str | None = None) -> ResumenGuardado:
        """Inserta las ofertas nuevas y refresca las conocidas, SIN tocar su estado ni su primera_vez.

        Todo va en una transacción: si una falla, no se guarda ninguna.
        Las ofertas 'aprobada' o 'enviada' no se refrescan: lo que aprobaste no cambia bajo tus pies.
        """
        ahora = ahora or _ahora()
        # El repositorio no confía en que quien llama haya quitado duplicados: si la misma oferta
        # viene dos veces en el lote, se guarda una vez y con su mejor puntuación.
        mejores: dict[tuple[str, str], Evaluacion] = {}
        for ev in evaluaciones:
            clave = (ev.oferta.fuente, ev.oferta.id_fuente)
            if clave not in mejores or ev.puntuacion > mejores[clave].puntuacion:
                mejores[clave] = ev

        ids_nuevos: set[int] = set()
        actualizadas = 0
        with self._escritura():
            for ev in mejores.values():
                o = ev.oferta
                existente = self._con.execute(
                    "SELECT id FROM ofertas WHERE fuente = ? AND id_fuente = ?", (o.fuente, o.id_fuente)).fetchone()
                # SIEMPRE parámetros (? o :nombre), nunca f-strings con datos: así es imposible la inyección SQL.
                cursor = self._con.execute(
                    """
                    INSERT INTO ofertas (fuente, id_fuente, titulo, empresa, url, ubicacion, tipo, publicada,
                                         descripcion, etiquetas, remoto, puntuacion, motivos, primera_vez, ultima_vez)
                    VALUES (:fuente, :id_fuente, :titulo, :empresa, :url, :ubicacion, :tipo, :publicada,
                            :descripcion, :etiquetas, :remoto, :puntuacion, :motivos, :ahora, :ahora)
                    -- UPSERT: si ya existe, se refrescan los datos pero NO el estado ni primera_vez.
                    ON CONFLICT (fuente, id_fuente) DO UPDATE SET
                        titulo = excluded.titulo, empresa = excluded.empresa, url = excluded.url,
                        ubicacion = excluded.ubicacion, tipo = excluded.tipo, publicada = excluded.publicada,
                        descripcion = excluded.descripcion, etiquetas = excluded.etiquetas,
                        remoto = excluded.remoto, puntuacion = excluded.puntuacion,
                        motivos = excluded.motivos, ultima_vez = excluded.ultima_vez
                    WHERE ofertas.estado NOT IN ('aprobada', 'enviada')
                    """,
                    {
                        "fuente": o.fuente, "id_fuente": o.id_fuente, "titulo": o.titulo, "empresa": o.empresa,
                        "url": o.url, "ubicacion": o.ubicacion, "tipo": o.tipo, "publicada": o.publicada,
                        "descripcion": o.descripcion,
                        "etiquetas": json.dumps(o.etiquetas, ensure_ascii=False),
                        "remoto": int(o.remoto),
                        "puntuacion": ev.puntuacion,
                        "motivos": json.dumps(ev.motivos, ensure_ascii=False),
                        "ahora": ahora,
                    },
                )
                if existente:
                    actualizadas += 1
                else:
                    ids_nuevos.add(cursor.lastrowid)
        return ResumenGuardado(ids_nuevos, actualizadas)

    def listar(self, estado: str | None = "nueva", limite: int | None = None) -> list[OfertaGuardada]:
        """Ofertas de mejor a peor puntuación. estado=None → todas."""
        if estado is not None and estado not in ESTADOS:
            raise AlmacenamientoError(f"estado desconocido '{estado}'. Válidos: {', '.join(sorted(ESTADOS))}")
        sql = "SELECT * FROM ofertas"
        parametros: list = []
        if estado is not None:
            sql += " WHERE estado = ?"
            parametros.append(estado)
        sql += " ORDER BY puntuacion DESC, id"
        if limite is not None:
            sql += " LIMIT ?"
            parametros.append(limite)
        return [self._a_guardada(fila) for fila in self._con.execute(sql, parametros)]

    def buscar_id(self, fuente: str, id_fuente: str) -> int | None:
        fila = self._con.execute("SELECT id FROM ofertas WHERE fuente = ? AND id_fuente = ?",
                                 (fuente, id_fuente)).fetchone()
        return fila["id"] if fila else None

    def obtener(self, oferta_id: int) -> OfertaGuardada:
        fila = self._con.execute("SELECT * FROM ofertas WHERE id = ?", (oferta_id,)).fetchone()
        if fila is None:
            raise OfertaNoEncontrada(f"no existe la oferta #{oferta_id}")
        return self._a_guardada(fila)

    # --- Estados ---------------------------------------------------------------------------

    def cambiar_estado(self, oferta_id: int, nuevo: str, nota: str = "", ahora: str | None = None) -> None:
        """Transiciones "simples" (descartar, restaurar). Preparar/aprobar/enviar tienen su propio método."""
        if nuevo not in ESTADOS:
            raise TransicionInvalida(f"estado desconocido '{nuevo}'. Válidos: {', '.join(sorted(ESTADOS))}")
        if nuevo in METODO_PARA:
            raise TransicionInvalida(f"para pasar a '{nuevo}' usa {METODO_PARA[nuevo]}")
        with self._escritura():
            self._transicion(oferta_id, self._estado(oferta_id), nuevo, nota, ahora)

    def _estado(self, oferta_id: int) -> str:
        fila = self._con.execute("SELECT estado FROM ofertas WHERE id = ?", (oferta_id,)).fetchone()
        if fila is None:
            raise OfertaNoEncontrada(f"no existe la oferta #{oferta_id}")
        return fila["estado"]

    def _transicion(self, oferta_id: int, actual: str, nuevo: str, nota: str, ahora: str | None) -> None:
        """Valida, aplica y registra un cambio de estado. Debe llamarse DENTRO de _escritura()."""
        if nuevo not in TRANSICIONES[actual]:
            permitidos = ", ".join(sorted(TRANSICIONES[actual])) or "ninguno (estado final)"
            raise TransicionInvalida(
                f"la oferta #{oferta_id} está '{actual}' y no puede pasar a '{nuevo}'. Permitidos: {permitidos}")
        # Compare-and-swap: solo actualiza si el estado sigue siendo el que validamos.
        # Con BEGIN IMMEDIATE ya no debería cambiar, pero así el historial nunca miente.
        cursor = self._con.execute(
            "UPDATE ofertas SET estado = ? WHERE id = ? AND estado = ?", (nuevo, oferta_id, actual))
        if cursor.rowcount != 1:
            raise TransicionInvalida(f"el estado de la oferta #{oferta_id} cambió mientras se procesaba")
        self._con.execute(
            "INSERT INTO historial (oferta_id, de, a, fecha, nota) VALUES (?, ?, ?, ?, ?)",
            (oferta_id, actual, nuevo, ahora or _ahora(), nota),
        )

    # --- Preparación, aprobación y envío (paso 5) -----------------------------------------

    def guardar_preparacion(self, oferta_id: int, texto: str, proveedor: str, modelo: str, prompt_huella: str,
                            tokens_entrada: int = 0, tokens_salida: int = 0,
                            ahora: str | None = None) -> Preparacion:
        """Guarda un borrador como nueva versión. Si la oferta era 'nueva', pasa a 'preparada'."""
        if not texto.strip():
            raise AlmacenamientoError("el borrador está vacío: no se guarda")
        ahora = ahora or _ahora()
        with self._escritura():
            actual = self._estado(oferta_id)
            if actual not in ESTADOS_PREPARABLES:
                raise TransicionInvalida(f"la oferta #{oferta_id} está '{actual}': "
                                         "solo se preparan ofertas nuevas o ya preparadas")
            version = self._con.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM preparaciones WHERE oferta_id = ?",
                (oferta_id,)).fetchone()[0]
            cursor = self._con.execute(
                "INSERT INTO preparaciones (oferta_id, version, texto, proveedor, modelo, prompt_huella,"
                " tokens_entrada, tokens_salida, generada_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (oferta_id, version, texto, proveedor, modelo, prompt_huella, tokens_entrada, tokens_salida, ahora),
            )
            if actual == "nueva":
                self._transicion(oferta_id, "nueva", "preparada", f"borrador v{version} ({proveedor}/{modelo})", ahora)
        return self._preparacion(cursor.lastrowid)

    def ultima_preparacion(self, oferta_id: int) -> Preparacion | None:
        fila = self._con.execute(
            "SELECT id FROM preparaciones WHERE oferta_id = ? ORDER BY version DESC LIMIT 1", (oferta_id,)).fetchone()
        return self._preparacion(fila["id"]) if fila else None

    def aprobar(self, oferta_id: int, *, confirmado_por_persona: bool, version: int | None = None,
                ahora: str | None = None) -> None:
        """Aprueba la ÚLTIMA versión del borrador. Lo llama la CLI tras la confirmación de una persona.

        `confirmado_por_persona` es obligatorio y nombrado (el `*`): un import o una llamada distraída
        no aprueba nada. OJO: no es una barrera de seguridad contra quien lo pase a propósito.
        `version`: la que el usuario vio en pantalla. Si entretanto se generó otra, no se aprueba
        nada: nunca se aprueba un texto distinto del que se leyó.
        """
        if confirmado_por_persona is not True:
            raise AlmacenamientoError("aprobar exige la confirmación explícita de una persona")
        ahora = ahora or _ahora()
        with self._escritura():
            actual = self._estado(oferta_id)
            if actual != "preparada":
                raise TransicionInvalida(f"la oferta #{oferta_id} está '{actual}': solo se aprueban ofertas preparadas")
            ultima = self.ultima_preparacion(oferta_id)
            if version is not None and ultima.version != version:
                raise TransicionInvalida(f"la última versión es la {ultima.version}, no la {version} que revisaste")
            self._con.execute(
                "INSERT INTO aprobaciones (oferta_id, preparacion_id, contenido_huella, aprobada_en)"
                " VALUES (?, ?, ?, ?)",
                (oferta_id, ultima.id, hashlib.sha256(ultima.texto.encode("utf-8")).hexdigest(), ahora),
            )
            self._transicion(oferta_id, "preparada", "aprobada", f"aprobada la versión {ultima.version}", ahora)

    def marcar_enviada(self, oferta_id: int, nota: str = "", *, confirmado_por_persona: bool,
                       ahora: str | None = None) -> None:
        """Registra que el USUARIO envió la postulación. La herramienta nunca envía nada."""
        if confirmado_por_persona is not True:
            raise AlmacenamientoError("marcar como enviada exige la confirmación explícita de una persona")
        with self._escritura():
            actual = self._estado(oferta_id)
            if actual != "aprobada":
                raise TransicionInvalida(f"la oferta #{oferta_id} está '{actual}': "
                                         "solo se marca como enviada una oferta aprobada")
            if self.verificar_aprobacion(oferta_id) is False:
                raise AlmacenamientoError(f"el texto aprobado de la oferta #{oferta_id} no coincide con su huella: "
                                          "la base de datos fue manipulada")
            self._transicion(oferta_id, "aprobada", "enviada", nota or "enviada por el usuario", ahora)

    def verificar_aprobacion(self, oferta_id: int) -> bool | None:
        """¿El texto de la última aprobación sigue siendo el que se aprobó? None si no hay aprobación.

        Los triggers frenan BUGS, no a alguien con acceso al archivo (puede borrarlos). Esto es
        DETECCIÓN: recalcula el SHA-256 del texto y lo compara con la huella guardada al aprobar.
        """
        fila = self._con.execute(
            "SELECT a.contenido_huella, p.texto FROM aprobaciones a JOIN preparaciones p ON p.id = a.preparacion_id"
            " WHERE a.oferta_id = ? ORDER BY a.id DESC LIMIT 1", (oferta_id,)).fetchone()
        if fila is None:
            return None
        return hashlib.sha256(fila["texto"].encode("utf-8")).hexdigest() == fila["contenido_huella"]

    def _preparacion(self, preparacion_id: int) -> Preparacion:
        f = self._con.execute("SELECT * FROM preparaciones WHERE id = ?", (preparacion_id,)).fetchone()
        return Preparacion(f["id"], f["oferta_id"], f["version"], f["texto"], f["proveedor"], f["modelo"],
                           f["prompt_huella"], f["tokens_entrada"], f["tokens_salida"], f["generada_en"])

    def historial(self, oferta_id: int) -> list[CambioEstado]:
        filas = self._con.execute(
            "SELECT de, a, fecha, nota FROM historial WHERE oferta_id = ? ORDER BY id", (oferta_id,))
        return [CambioEstado(f["de"], f["a"], f["fecha"], f["nota"]) for f in filas]

    # --- Conversión fila → objeto ----------------------------------------------------------

    @staticmethod
    def _lista_json(fila: sqlite3.Row, columna: str) -> list:
        # La BD pudo editarse a mano: un JSON roto debe dar un error del dominio, no un traceback.
        try:
            valor = json.loads(fila[columna])
        except (ValueError, TypeError) as e:
            raise AlmacenamientoError(f"la oferta #{fila['id']} tiene '{columna}' corrupto: {e}") from e
        if not isinstance(valor, list):
            raise AlmacenamientoError(f"la oferta #{fila['id']} tiene '{columna}' que no es una lista")
        return valor

    @classmethod
    def _a_guardada(cls, fila: sqlite3.Row) -> OfertaGuardada:
        oferta = Oferta(
            id_fuente=fila["id_fuente"], fuente=fila["fuente"], titulo=fila["titulo"], empresa=fila["empresa"],
            url=fila["url"], ubicacion=fila["ubicacion"], tipo=fila["tipo"], publicada=fila["publicada"],
            descripcion=fila["descripcion"], etiquetas=cls._lista_json(fila, "etiquetas"),
            remoto=bool(fila["remoto"]),
        )
        return OfertaGuardada(fila["id"], oferta, fila["puntuacion"], cls._lista_json(fila, "motivos"),
                              fila["estado"], fila["primera_vez"], fila["ultima_vez"])
