"""Tests del repositorio SQLite: guardar, listar, estados, historial y migraciones.

Cada test usa una base de datos en una carpeta temporal: aislada y sin tocar data/buscador.db.
"""

import hashlib
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from buscador.almacenamiento import (
    ESTADOS,
    MIGRACIONES,
    TRANSICIONES,
    AlmacenamientoError,
    OfertaNoEncontrada,
    Repositorio,
    TransicionInvalida,
)
from buscador.filtros import Evaluacion
from buscador.modelos import Oferta

AYER = "2026-10-22T08:00:00+00:00"
HOY = "2026-10-23T08:00:00+00:00"


def evaluacion(id_fuente="1", titulo="Kotlin Dev", puntuacion=10, fuente="test", motivos=None) -> Evaluacion:
    oferta = Oferta(id_fuente, fuente, titulo, "ACME", f"https://x/{id_fuente}", "Berlin", "", "", "<p>desc</p>",
                    ["kotlin"], remoto=True)
    return Evaluacion(oferta, puntuacion, motivos or [f"kotlin (título) +{puntuacion}"])


def llevar_a(repo: Repositorio, oferta_id: int, estado: str) -> None:
    """Lleva una oferta 'nueva' a `estado` por el camino válido del repositorio."""
    if estado == "descartada":
        repo.cambiar_estado(oferta_id, "descartada", ahora=HOY)
    if estado in ("preparada", "aprobada", "enviada"):
        repo.guardar_preparacion(oferta_id, "Carta v1", "simulado", "simulado", "huella", ahora=HOY)
    if estado in ("aprobada", "enviada"):
        repo.aprobar(oferta_id, confirmado_por_persona=True, ahora=HOY)
    if estado == "enviada":
        repo.marcar_enviada(oferta_id, confirmado_por_persona=True, ahora=HOY)


class BaseRepo(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ruta = Path(tmp.name) / "buscador.db"
        self.repo = Repositorio(self.ruta)
        self.addCleanup(self.repo.cerrar)

    def un_id(self, id_fuente="1") -> int:
        return next(o.id for o in self.repo.listar(estado=None) if o.oferta.id_fuente == id_fuente)


class TestGuardar(BaseRepo):
    def test_guarda_y_lee_la_oferta_completa(self):
        self.repo.guardar_evaluaciones([evaluacion()], ahora=HOY)
        [guardada] = self.repo.listar()
        self.assertEqual(guardada.oferta, evaluacion().oferta)  # ida y vuelta sin perder nada
        self.assertEqual((guardada.puntuacion, guardada.motivos), (10, ["kotlin (título) +10"]))
        self.assertEqual((guardada.estado, guardada.primera_vez, guardada.ultima_vez), ("nueva", HOY, HOY))

    def test_resumen_distingue_nuevas_de_conocidas(self):
        r1 = self.repo.guardar_evaluaciones([evaluacion("1"), evaluacion("2")], ahora=AYER)
        r2 = self.repo.guardar_evaluaciones([evaluacion("2"), evaluacion("3")], ahora=HOY)
        self.assertEqual((len(r1.ids_nuevos), r1.actualizadas), (2, 0))
        self.assertEqual((len(r2.ids_nuevos), r2.actualizadas), (1, 1))
        self.assertEqual(r2.ids_nuevos, {self.un_id("3")})

    def test_volver_a_guardar_actualiza_datos_pero_conserva_estado_y_primera_vez(self):
        self.repo.guardar_evaluaciones([evaluacion(puntuacion=10)], ahora=AYER)
        self.repo.cambiar_estado(self.un_id(), "descartada", ahora=AYER)
        self.repo.guardar_evaluaciones([evaluacion(titulo="Kotlin Developer", puntuacion=15)], ahora=HOY)
        [guardada] = self.repo.listar(estado=None)
        self.assertEqual(guardada.estado, "descartada")          # tu decisión se respeta
        self.assertEqual(guardada.primera_vez, AYER)
        self.assertEqual(guardada.ultima_vez, HOY)
        self.assertEqual((guardada.puntuacion, guardada.oferta.titulo), (15, "Kotlin Developer"))

    def test_misma_id_en_distinta_fuente_son_ofertas_distintas(self):
        self.repo.guardar_evaluaciones([evaluacion("1", fuente="a"), evaluacion("1", fuente="b")], ahora=HOY)
        self.assertEqual(len(self.repo.listar()), 2)

    def test_inyeccion_sql_se_guarda_como_texto(self):
        malicioso = "x'); DROP TABLE ofertas; --"
        self.repo.guardar_evaluaciones([evaluacion(titulo=malicioso)], ahora=HOY)
        self.assertEqual(self.repo.listar()[0].oferta.titulo, malicioso)

    def test_persiste_al_reabrir(self):
        self.repo.guardar_evaluaciones([evaluacion()], ahora=HOY)
        self.repo.cerrar()
        with Repositorio(self.ruta) as otro:
            self.assertEqual(len(otro.listar()), 1)

    def test_guardar_es_atomico(self):
        # Si una evaluación falla a mitad, no queda guardada ninguna (transacción).
        rota = evaluacion("2")
        rota.motivos = [object()]  # no serializable a JSON
        with self.assertRaises(TypeError):
            self.repo.guardar_evaluaciones([evaluacion("1"), rota], ahora=HOY)
        self.assertEqual(self.repo.listar(estado=None), [])


class TestListar(BaseRepo):
    def setUp(self):
        super().setUp()
        self.repo.guardar_evaluaciones([evaluacion("bajo", puntuacion=5), evaluacion("alto", puntuacion=20),
                                        evaluacion("medio", puntuacion=12)], ahora=HOY)

    def test_ordena_por_puntuacion(self):
        self.assertEqual([o.oferta.id_fuente for o in self.repo.listar()], ["alto", "medio", "bajo"])

    def test_filtra_por_estado_y_limita(self):
        self.repo.cambiar_estado(self.un_id("alto"), "descartada", ahora=HOY)
        self.assertEqual([o.oferta.id_fuente for o in self.repo.listar(estado="nueva")], ["medio", "bajo"])
        self.assertEqual([o.oferta.id_fuente for o in self.repo.listar(estado="descartada")], ["alto"])
        self.assertEqual(len(self.repo.listar(estado=None)), 3)
        self.assertEqual(len(self.repo.listar(limite=1)), 1)

    def test_estado_desconocido_al_listar(self):
        with self.assertRaises(AlmacenamientoError):
            self.repo.listar(estado="inventado")

    def test_contar_por_estado(self):
        self.repo.cambiar_estado(self.un_id("alto"), "descartada", ahora=HOY)
        conteo = self.repo.contar_por_estado()
        self.assertEqual((conteo["nueva"], conteo["descartada"], conteo["enviada"]), (2, 1, 0))
        self.assertEqual(set(conteo), set(ESTADOS))

    def test_buscar_id_por_fuente(self):
        self.assertEqual(self.repo.buscar_id("test", "medio"), self.un_id("medio"))
        self.assertIsNone(self.repo.buscar_id("test", "no-existe"))

    def test_obtener_por_id(self):
        self.assertEqual(self.repo.obtener(self.un_id("medio")).oferta.id_fuente, "medio")
        with self.assertRaises(OfertaNoEncontrada):
            self.repo.obtener(99999)


class TestEstados(BaseRepo):
    """La regla de oro del proyecto, garantizada por diseño: a 'enviada' solo se llega desde 'aprobada'."""

    def setUp(self):
        super().setUp()
        self.repo.guardar_evaluaciones([evaluacion()], ahora=HOY)
        self.id = self.un_id()

    def test_flujo_completo_valido(self):
        llevar_a(self.repo, self.id, "enviada")
        self.assertEqual(self.repo.obtener(self.id).estado, "enviada")

    def test_no_se_puede_enviar_sin_aprobar(self):
        with self.assertRaises(TransicionInvalida):
            self.repo.marcar_enviada(self.id, confirmado_por_persona=True, ahora=HOY)
        llevar_a(self.repo, self.id, "preparada")
        with self.assertRaises(TransicionInvalida):
            self.repo.marcar_enviada(self.id, confirmado_por_persona=True, ahora=HOY)

    def test_enviada_es_final(self):
        llevar_a(self.repo, self.id, "enviada")
        for destino in ("descartada", "nueva"):
            with self.subTest(destino=destino), self.assertRaises(TransicionInvalida):
                self.repo.cambiar_estado(self.id, destino, ahora=HOY)
        for accion in (self.repo.aprobar, self.repo.marcar_enviada):
            with self.subTest(accion=accion.__name__), self.assertRaises(TransicionInvalida):
                accion(self.id, confirmado_por_persona=True, ahora=HOY)
        with self.assertRaises(TransicionInvalida):
            self.repo.guardar_preparacion(self.id, "otra", "simulado", "simulado", "h", ahora=HOY)

    def test_cambiar_estado_no_sirve_para_preparar_aprobar_ni_enviar(self):
        # Esos estados exigen datos (qué se preparó, qué se aprobó): tienen sus propios métodos.
        for estado in ("preparada", "aprobada", "enviada"):
            with self.subTest(estado=estado), self.assertRaisesRegex(TransicionInvalida, "usa"):
                self.repo.cambiar_estado(self.id, estado, ahora=HOY)

    def test_descartar_y_restaurar(self):
        self.repo.cambiar_estado(self.id, "descartada", ahora=HOY)
        self.repo.cambiar_estado(self.id, "nueva", ahora=HOY)
        self.assertEqual(self.repo.obtener(self.id).estado, "nueva")

    def test_transicion_rechazada_no_cambia_nada(self):
        with self.assertRaises(TransicionInvalida):
            self.repo.marcar_enviada(self.id, confirmado_por_persona=True, ahora=HOY)
        self.assertEqual(self.repo.obtener(self.id).estado, "nueva")
        self.assertEqual(self.repo.historial(self.id), [])

    def test_cada_estado_tiene_sus_transiciones_definidas(self):
        self.assertEqual(set(TRANSICIONES), ESTADOS)
        for destinos in TRANSICIONES.values():
            self.assertLessEqual(destinos, ESTADOS)
        # Solo 'aprobada' lleva a 'enviada'.
        self.assertEqual({o for o, d in TRANSICIONES.items() if "enviada" in d}, {"aprobada"})

    def test_estado_inexistente(self):
        with self.assertRaises(TransicionInvalida):
            self.repo.cambiar_estado(self.id, "hackeada", ahora=HOY)

    def test_oferta_inexistente(self):
        with self.assertRaises(OfertaNoEncontrada):
            self.repo.cambiar_estado(99999, "descartada", ahora=HOY)

    def test_historial_registra_cada_cambio(self):
        self.repo.cambiar_estado(self.id, "descartada", nota="pide alemán", ahora=AYER)
        self.repo.cambiar_estado(self.id, "nueva", ahora=HOY)
        historial = self.repo.historial(self.id)
        self.assertEqual([(h.de, h.a, h.fecha, h.nota) for h in historial],
                         [("nueva", "descartada", AYER, "pide alemán"), ("descartada", "nueva", HOY, "")])

    def test_la_base_de_datos_tambien_rechaza_estados_invalidos(self):
        # Defensa en profundidad: aunque alguien salte el repositorio, el CHECK de la tabla protege.
        con = sqlite3.connect(self.ruta)
        self.addCleanup(con.close)
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("UPDATE ofertas SET estado = 'hackeada'")


class TestMigraciones(BaseRepo):
    def test_base_nueva_queda_en_la_ultima_version(self):
        self.assertEqual(self.repo.version_esquema(), Repositorio.VERSION_ESQUEMA)

    def test_reabrir_no_reaplica_migraciones_ni_pierde_datos(self):
        self.repo.guardar_evaluaciones([evaluacion()], ahora=HOY)
        self.repo.cerrar()
        with Repositorio(self.ruta) as otro:
            self.assertEqual(otro.version_esquema(), Repositorio.VERSION_ESQUEMA)
            self.assertEqual(len(otro.listar()), 1)

    def test_base_de_una_version_futura_se_rechaza(self):
        self.repo.cerrar()
        con = sqlite3.connect(self.ruta)
        con.execute(f"PRAGMA user_version = {Repositorio.VERSION_ESQUEMA + 1}")
        con.close()
        with self.assertRaises(AlmacenamientoError):
            Repositorio(self.ruta)

    def test_archivo_que_no_es_sqlite(self):
        self.repo.cerrar()
        self.ruta.write_bytes(b"esto no es una base de datos" * 100)
        with self.assertRaises(AlmacenamientoError):
            Repositorio(self.ruta)


def requisitos_sql(con: sqlite3.Connection, oferta_id: int, destino: str) -> None:
    """Con SQL directo, crea lo que la BD exige para entrar en `destino` (preparación o aprobación)."""
    if destino == "preparada":
        con.execute("INSERT INTO preparaciones (oferta_id, version, texto, proveedor, modelo, prompt_huella,"
                    " generada_en) VALUES (?, 1, 'x', 's', 's', 'h', ?)", (oferta_id, HOY))
    elif destino == "aprobada":
        con.execute("INSERT INTO aprobaciones (oferta_id, preparacion_id, contenido_huella, aprobada_en)"
                    " SELECT ?, max(id), 'h', ? FROM preparaciones WHERE oferta_id = ?", (oferta_id, HOY, oferta_id))


class TestRevisionBaseDeDatos(BaseRepo):
    """Hallazgos de ecc:database-reviewer. La BD debe defender sus reglas aunque se salte el repositorio."""

    def sql_directo(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.ruta)
        self.addCleanup(con.close)
        return con

    def oferta_en(self, estado: str, id_fuente: str) -> int:
        self.repo.guardar_evaluaciones([evaluacion(id_fuente)], ahora=HOY)
        oferta_id = self.un_id(id_fuente)
        llevar_a(self.repo, oferta_id, estado)
        return oferta_id

    # Hallazgo 1 (CRÍTICO): la máquina de estados también vive en la BD (triggers).
    def test_la_bd_aplica_exactamente_la_tabla_de_transiciones(self):
        con = self.sql_directo()
        for origen in sorted(ESTADOS):
            for destino in sorted(ESTADOS - {origen}):
                with self.subTest(origen=origen, destino=destino):
                    oferta_id = self.oferta_en(origen, f"{origen}-{destino}")
                    if destino in TRANSICIONES[origen]:
                        with con:
                            requisitos_sql(con, oferta_id, destino)
                            con.execute("UPDATE ofertas SET estado = ? WHERE id = ?", (destino, oferta_id))
                    else:
                        with self.assertRaises(sqlite3.IntegrityError), con:
                            con.execute("UPDATE ofertas SET estado = ? WHERE id = ?", (destino, oferta_id))

    def test_la_bd_no_permite_insertar_directamente_en_otro_estado_que_nueva(self):
        con = self.sql_directo()
        with self.assertRaises(sqlite3.IntegrityError), con:
            con.execute("INSERT INTO ofertas (fuente, id_fuente, titulo, puntuacion, estado, primera_vez, ultima_vez)"
                        " VALUES ('x', '1', 't', 1, 'enviada', ?, ?)", (HOY, HOY))

    def test_actualizar_otras_columnas_sigue_permitido(self):
        oferta_id = self.oferta_en("enviada", "e")
        con = self.sql_directo()
        with con:
            con.execute("UPDATE ofertas SET puntuacion = 99 WHERE id = ?", (oferta_id,))

    def test_lista_de_estados_coincide_con_el_check_de_la_tabla(self):
        sql = self.sql_directo().execute("SELECT sql FROM sqlite_master WHERE name = 'ofertas'").fetchone()[0]
        check = re.search(r"estado\s+IN\s*\(([^)]*)\)", sql).group(1)
        self.assertEqual(set(re.findall(r"'(\w+)'", check)), ESTADOS)

    # Hallazgo 6: el historial es una auditoría: solo se añade, nunca se modifica ni se borra.
    def test_historial_es_solo_de_anadir(self):
        oferta_id = self.oferta_en("descartada", "h")
        con = self.sql_directo()
        for sql in ("UPDATE historial SET nota = 'manipulada'", "DELETE FROM historial",
                    f"DELETE FROM ofertas WHERE id = {oferta_id}"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError), con:
                con.execute(sql)
        self.assertEqual(len(self.repo.historial(oferta_id)), 1)

    # Hallazgo 2: lectura y escritura del estado bajo el mismo bloqueo de escritura.
    def test_bd_ocupada_por_otro_proceso_da_error_claro(self):
        oferta_id = self.oferta_en("nueva", "lock")
        lento = Repositorio(self.ruta, timeout=0.1)
        self.addCleanup(lento.cerrar)
        otro = self.sql_directo()
        otro.execute("BEGIN IMMEDIATE")  # otro proceso tiene el bloqueo de escritura
        self.addCleanup(otro.rollback)
        with self.assertRaisesRegex(AlmacenamientoError, "ocupada"):
            lento.cambiar_estado(oferta_id, "descartada", ahora=HOY)
        with self.assertRaisesRegex(AlmacenamientoError, "ocupada"):
            lento.guardar_evaluaciones([evaluacion("otra")], ahora=HOY)

    # Hallazgo 3: duplicados dentro del mismo lote.
    def test_duplicados_en_el_lote_cuentan_una_vez_y_gana_la_mejor(self):
        resumen = self.repo.guardar_evaluaciones([evaluacion("1", puntuacion=5), evaluacion("1", puntuacion=9),
                                                  evaluacion("1", puntuacion=7)], ahora=HOY)
        self.assertEqual((len(resumen.ids_nuevos), resumen.actualizadas), (1, 0))
        self.assertEqual(self.repo.listar()[0].puntuacion, 9)

    # Hallazgo 5: lo que aprobaste no cambia bajo tus pies.
    def test_ofertas_aprobadas_o_enviadas_no_se_refrescan(self):
        for estado in ("aprobada", "enviada"):
            with self.subTest(estado=estado):
                oferta_id = self.oferta_en(estado, estado)
                self.repo.guardar_evaluaciones([evaluacion(estado, titulo="Título cambiado", puntuacion=1)], ahora=HOY)
                guardada = self.repo.obtener(oferta_id)
                self.assertEqual((guardada.oferta.titulo, guardada.puntuacion, guardada.estado),
                                 ("Kotlin Dev", 10, estado))

    # Hallazgo 4: una fila corrupta da un error del dominio, no un traceback de JSON.
    def test_json_corrupto_da_error_claro(self):
        self.oferta_en("nueva", "j")
        con = self.sql_directo()
        with con:
            con.execute("UPDATE ofertas SET etiquetas = '{roto'")
        with self.assertRaises(AlmacenamientoError):
            self.repo.listar()

    # Migración v1 → v2 sobre una BD real existente.
    def test_una_bd_v1_existente_se_migra_sin_perder_datos(self):
        self.repo.cerrar()
        self.ruta.unlink()
        con = sqlite3.connect(self.ruta)
        con.executescript(f"BEGIN; {MIGRACIONES[0]} PRAGMA user_version = 1; COMMIT;")
        con.execute("INSERT INTO ofertas (fuente, id_fuente, titulo, puntuacion, primera_vez, ultima_vez)"
                    " VALUES ('x', '1', 'Kotlin Dev', 10, ?, ?)", (HOY, HOY))
        con.commit()
        con.close()
        with Repositorio(self.ruta) as migrado:
            self.assertEqual(migrado.version_esquema(), Repositorio.VERSION_ESQUEMA)
            self.assertGreaterEqual(Repositorio.VERSION_ESQUEMA, 2)
            [oferta] = migrado.listar()
            self.assertEqual(oferta.oferta.titulo, "Kotlin Dev")
        con = self.sql_directo()
        with self.assertRaises(sqlite3.IntegrityError), con:  # los triggers ya protegen la BD migrada
            con.execute("UPDATE ofertas SET estado = 'enviada'")


class TestPreparacionesYAprobaciones(BaseRepo):
    """Paso 5 (esquema v3): qué se preparó, qué se aprobó, y la BD exige ambas cosas."""

    def setUp(self):
        super().setUp()
        self.repo.guardar_evaluaciones([evaluacion()], ahora=HOY)
        self.id = self.un_id()

    def preparar(self, texto="Carta", proveedor="simulado"):
        return self.repo.guardar_preparacion(self.id, texto, proveedor, "modelo-x", "huella-1",
                                             tokens_entrada=4000, tokens_salida=600, ahora=HOY)

    def test_preparar_guarda_todo_y_versiona(self):
        p1 = self.preparar("Carta 1")
        p2 = self.preparar("Carta 2", proveedor="claude")
        self.assertEqual((p1.version, p2.version), (1, 2))
        ultima = self.repo.ultima_preparacion(self.id)
        self.assertEqual((ultima.texto, ultima.proveedor, ultima.modelo, ultima.prompt_huella,
                          ultima.tokens_entrada, ultima.tokens_salida),
                         ("Carta 2", "claude", "modelo-x", "huella-1", 4000, 600))
        self.assertEqual(self.repo.obtener(self.id).estado, "preparada")
        self.assertEqual([(h.de, h.a) for h in self.repo.historial(self.id)], [("nueva", "preparada")])

    def test_sin_preparaciones(self):
        self.assertIsNone(self.repo.ultima_preparacion(self.id))
        with self.assertRaises(TransicionInvalida):
            self.repo.aprobar(self.id, confirmado_por_persona=True, ahora=HOY)

    def test_aprobar_registra_que_version_y_su_huella(self):
        self.preparar("Carta 1")
        self.preparar("Carta 2")
        self.repo.aprobar(self.id, confirmado_por_persona=True, ahora=HOY)
        self.assertEqual(self.repo.obtener(self.id).estado, "aprobada")
        fila = self.sql().execute("SELECT p.version, a.contenido_huella FROM aprobaciones a"
                                  " JOIN preparaciones p ON p.id = a.preparacion_id").fetchone()
        self.assertEqual(fila[0], 2)  # se aprueba la ÚLTIMA versión
        self.assertEqual(fila[1], hashlib.sha256("Carta 2".encode("utf-8")).hexdigest())

    # Revisión de seguridad (ALTO): aprobar/enviar exigen declararlo explícitamente; un import
    # o una llamada distraída no aprueba nada. (No es una barrera contra quien lo haga a propósito.)
    def test_aprobar_y_enviar_exigen_confirmado_por_persona(self):
        self.preparar()
        with self.assertRaises(TypeError):
            self.repo.aprobar(self.id, ahora=HOY)
        with self.assertRaises(AlmacenamientoError):
            self.repo.aprobar(self.id, confirmado_por_persona=False, ahora=HOY)
        self.assertEqual(self.repo.obtener(self.id).estado, "preparada")
        self.repo.aprobar(self.id, confirmado_por_persona=True, ahora=HOY)
        with self.assertRaises(TypeError):
            self.repo.marcar_enviada(self.id, ahora=HOY)
        with self.assertRaises(AlmacenamientoError):
            self.repo.marcar_enviada(self.id, confirmado_por_persona=False, ahora=HOY)
        self.assertEqual(self.repo.obtener(self.id).estado, "aprobada")

    # Revisión de seguridad (MEDIO): la huella guardada se VERIFICA.
    def test_verificar_aprobacion_detecta_texto_manipulado(self):
        self.assertIsNone(self.repo.verificar_aprobacion(self.id))  # sin aprobación: nada que verificar
        self.preparar("Carta original")
        self.repo.aprobar(self.id, confirmado_por_persona=True, ahora=HOY)
        self.assertTrue(self.repo.verificar_aprobacion(self.id))
        con = self.sql()
        with con:  # alguien con acceso al archivo borra el trigger y cambia el texto aprobado
            con.execute("DROP TRIGGER preparaciones_sin_modificar")
            con.execute("UPDATE preparaciones SET texto = 'Carta cambiada'")
        self.assertFalse(self.repo.verificar_aprobacion(self.id))

    def test_texto_vacio_no_se_guarda(self):
        with self.assertRaises(AlmacenamientoError):
            self.preparar("   ")

    def sql(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.ruta)
        self.addCleanup(con.close)
        return con

    def test_la_bd_exige_preparacion_y_aprobacion(self):
        con = self.sql()
        with self.subTest("preparada sin preparación"), self.assertRaises(sqlite3.IntegrityError), con:
            con.execute("UPDATE ofertas SET estado = 'preparada' WHERE id = ?", (self.id,))
        self.preparar()
        with self.subTest("aprobada sin aprobación"), self.assertRaises(sqlite3.IntegrityError), con:
            con.execute("UPDATE ofertas SET estado = 'aprobada' WHERE id = ?", (self.id,))

    def test_solo_se_aprueba_la_ultima_version(self):
        primera = self.preparar("v1")
        self.preparar("v2")
        con = self.sql()
        with self.assertRaises(sqlite3.IntegrityError), con:
            con.execute("INSERT INTO aprobaciones (oferta_id, preparacion_id, contenido_huella, aprobada_en)"
                        " VALUES (?, ?, 'h', ?)", (self.id, primera.id, HOY))

    def test_no_se_prepara_una_oferta_descartada_ni_aprobada(self):
        self.repo.cambiar_estado(self.id, "descartada", ahora=HOY)
        with self.assertRaises(TransicionInvalida):
            self.preparar()
        con = self.sql()
        with self.assertRaises(sqlite3.IntegrityError), con:
            requisitos_sql(con, self.id, "preparada")

    def test_preparaciones_y_aprobaciones_son_solo_de_anadir(self):
        self.preparar()
        self.repo.aprobar(self.id, confirmado_por_persona=True, ahora=HOY)
        con = self.sql()
        for sql in ("UPDATE preparaciones SET texto = 'cambiado'", "DELETE FROM preparaciones",
                    "UPDATE aprobaciones SET contenido_huella = 'x'", "DELETE FROM aprobaciones"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError), con:
                con.execute(sql)

    def test_una_bd_v2_existente_se_migra_a_v3(self):
        self.repo.cerrar()
        self.ruta.unlink()
        con = sqlite3.connect(self.ruta)
        con.executescript(f"BEGIN; {MIGRACIONES[0]} {MIGRACIONES[1]} PRAGMA user_version = 2; COMMIT;")
        con.execute("INSERT INTO ofertas (fuente, id_fuente, titulo, puntuacion, primera_vez, ultima_vez)"
                    " VALUES ('x', '1', 'Kotlin Dev', 10, ?, ?)", (HOY, HOY))
        con.commit()
        con.close()
        with Repositorio(self.ruta) as migrado:
            self.assertEqual(migrado.version_esquema(), Repositorio.VERSION_ESQUEMA)
            [oferta] = migrado.listar()
            migrado.guardar_preparacion(oferta.id, "Carta", "simulado", "simulado", "h", ahora=HOY)
            self.assertEqual(migrado.obtener(oferta.id).estado, "preparada")


if __name__ == "__main__":
    unittest.main()
