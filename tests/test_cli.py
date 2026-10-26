"""Tests de la CLI: nombres de archivo seguros, escritura atómica y límite diario (hallazgos 1, 6, 7).

Cada test usa una carpeta temporal como data/, así no ensucia los datos reales.
"""

import argparse
import importlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from buscador import cli
from buscador.almacenamiento import Repositorio
from buscador.fuentes.base import Fuente
from buscador.ia.simulado import Simulado
from buscador.modelos import Oferta


class FuenteFalsa(Fuente):
    """Fuente de mentira: cuenta cuántas veces la llaman y no toca la red."""
    nombre = "falsa"
    limite_diario = 2
    llamadas = 0

    def descargar(self, search=None):
        type(self).llamadas += 1
        return {"ok": True}, [Oferta("1", self.nombre, "Dev", "ACME", "u", "Remote", "", "", "")]


class FuenteRota(Fuente):
    nombre = "rota"

    def descargar(self, search=None):
        raise TimeoutError("timed out")  # un error que NO es FuenteError


class BaseCli(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "data"
        FuenteFalsa.llamadas = 0
        parches = [
            patch.object(cli, "DIR_DATA", self.data),
            patch.object(cli, "DIR_RAW", self.data / "raw"),
            patch.object(cli, "RAIZ", Path(tmp.name)),
            patch.dict(cli.FUENTES, {"rota": FuenteRota, "falsa": FuenteFalsa}, clear=True),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)

    def ejecutar(self, search=None, fuente="falsa", force=False):
        args = argparse.Namespace(search=search, fuente=fuente, force=force)
        with patch("sys.stdout"), patch("sys.stderr"):
            return cli.comando_descargar(args)

    def archivos(self):
        if not self.data.exists():
            return []
        return sorted(p.relative_to(self.data).as_posix() for p in self.data.rglob("*") if p.is_file())


class TestAtribucionDeFuentes(unittest.TestCase):
    """Los términos de las fuentes piden citarlas: el texto sale de FUENTES, no de una línea fija."""

    def test_las_fuentes_reales_declaran_titulo_y_sitio(self):
        from buscador.fuentes import FUENTES
        self.assertGreaterEqual(len(FUENTES), 4)
        for nombre, clase in FUENTES.items():
            with self.subTest(fuente=nombre):
                self.assertTrue(clase.titulo)
                self.assertTrue(clase.sitio.startswith("https://"))

    def test_la_atribucion_cita_cada_fuente_real_con_su_sitio(self):
        from buscador.fuentes import FUENTES
        texto = cli.atribucion(list(FUENTES))
        for clase in FUENTES.values():
            with self.subTest(fuente=clase.titulo):
                self.assertIn(f"{clase.titulo} ({clase.sitio})", texto)


class TestAtribucionEnLaSalida(BaseCli):
    def linea_de_fuentes(self, fuente):
        with patch("sys.stdout", new_callable=io.StringIO) as salida, patch("sys.stderr"):
            cli.comando_descargar(argparse.Namespace(search=None, fuente=fuente, force=False))
        lineas = [x for x in salida.getvalue().splitlines() if x.startswith("Fuentes:")]
        self.assertEqual(len(lineas), 1)
        return lineas[0]

    def test_cita_todas_las_fuentes_consultadas(self):
        linea = self.linea_de_fuentes(None)
        # Las fuentes de prueba no declaran título: se usa su nombre.
        self.assertIn("falsa", linea)
        self.assertIn("rota", linea)

    def test_con_una_sola_fuente_solo_cita_esa(self):
        linea = self.linea_de_fuentes("falsa")
        self.assertIn("falsa", linea)
        self.assertNotIn("rota", linea)


class TestNombreDeArchivo(BaseCli):
    """Hallazgo 1: --search no confiable en el nombre del archivo."""

    def test_search_no_escapa_de_data(self):
        for search in ("../../x", "..\\..\\x", "c++/qa", 'a:b*?<>|"', "con", "x" * 500):
            with self.subTest(search=search):
                self.assertEqual(self.ejecutar(search=search), 0)
        raiz = self.data.resolve()
        for p in self.data.parent.rglob("*.json"):
            self.assertTrue(p.resolve().is_relative_to(raiz), p)
        for nombre in self.archivos():
            self.assertLess(len(nombre), 120)

    def test_busquedas_distintas_no_colisionan(self):
        self.ejecutar(search="c++")
        self.ejecutar(search="c#")
        self.assertEqual(FuenteFalsa.llamadas, 2)  # c# no reutilizó el archivo de c++

    def test_search_vacio_equivale_a_sin_busqueda(self):
        self.ejecutar(search="")
        self.ejecutar(search=None)
        self.assertEqual(FuenteFalsa.llamadas, 1)


class TestRobustez(BaseCli):
    def test_error_inesperado_de_una_fuente_no_corta_las_demas(self):
        # Hallazgo 2: un TimeoutError suelto no debe dar traceback ni frenar el bucle.
        # "rota" va antes que "falsa" en el registro, así que "falsa" solo se ejecuta si el bucle sigue.
        args = argparse.Namespace(search=None, fuente=None, force=False)
        with patch("sys.stdout"), patch("sys.stderr"):
            codigo = cli.comando_descargar(args)
        self.assertEqual(codigo, 1)
        self.assertEqual(FuenteFalsa.llamadas, 1)

    def test_si_falla_el_normalizado_no_queda_crudo(self):
        # Hallazgo 6: el crudo es el marcador de "completado"; no debe existir si algo falló.
        original = cli.guardar_json

        def falla_en_normalizado(datos, ruta):
            if ruta.name.startswith("ofertas_"):
                raise OSError("disco lleno")
            original(datos, ruta)

        with patch.object(cli, "guardar_json", side_effect=falla_en_normalizado):
            self.assertEqual(self.ejecutar(), 1)
        self.assertEqual([a for a in self.archivos() if a.startswith("raw/")], [])

    def test_no_quedan_temporales(self):
        self.ejecutar()
        self.assertFalse([a for a in self.archivos() if a.endswith(".tmp")])
        self.assertEqual(len([a for a in self.archivos() if a.startswith("raw/")]), 1)


class TestLimiteDiario(BaseCli):
    """Hallazgo 6: Remotive pide máx. ~4 llamadas/día. --force o búsquedas distintas no lo saltan."""

    def test_respeta_limite_diario_aunque_se_use_force(self):
        for i in range(4):
            self.ejecutar(search=f"q{i}", force=True)
        self.assertEqual(FuenteFalsa.llamadas, 2)  # limite_diario = 2

    def test_sin_limite_si_la_fuente_no_lo_define(self):
        with patch.object(FuenteFalsa, "limite_diario", None):
            for i in range(3):
                self.ejecutar(search=f"q{i}")
        self.assertEqual(FuenteFalsa.llamadas, 3)


class TestContadorRobusto(BaseCli):
    """Segunda revisión, hallazgo 1: el archivo del contador también es dato no confiable."""

    def escribir_contador(self, contenido):
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / f"peticiones_{cli.date.today().isoformat()}.json").write_text(contenido, encoding="utf-8")

    def test_contador_con_valores_raros_no_rompe(self):
        for contenido in ('{"falsa": "3"}', '{"falsa": null}', '{"falsa": true}', "[1, 2]", "null"):
            with self.subTest(contenido=contenido):
                self.escribir_contador(contenido)
                self.assertEqual(self.ejecutar(search=contenido), 0)

    def test_fallo_al_escribir_contador_no_da_traceback_y_no_llama_a_la_api(self):
        original = cli.guardar_json

        def falla_en_contador(datos, ruta):
            if ruta.name.startswith("peticiones_"):
                raise PermissionError("archivo bloqueado")
            original(datos, ruta)

        with patch.object(cli, "guardar_json", side_effect=falla_en_contador):
            self.assertEqual(self.ejecutar(), 1)
        # Si no podemos registrar la petición, no la hacemos: no podríamos garantizar el límite.
        self.assertEqual(FuenteFalsa.llamadas, 0)

    def test_permiso_denegado_al_leer_contador(self):
        with patch.object(Path, "read_text", side_effect=PermissionError("bloqueado")):
            self.assertEqual(cli.leer_contador(self.data / "x.json"), {})


class TestMayusculasEnBusqueda(BaseCli):
    """Segunda revisión, hallazgo 4: "Python" y "python" son la misma búsqueda."""

    def test_mayusculas_reutilizan_el_mismo_archivo(self):
        self.ejecutar(search="Python")
        self.ejecutar(search="python")
        self.assertEqual(FuenteFalsa.llamadas, 1)


PERFIL_TEST = """
puntuacion_minima = 3
nivel_bonus = ["junior"]
nivel_penaliza = ["senior"]
roles_excluidos = ["sales"]
idiomas_excluyentes = ["german"]

[puntos]
nivel_bonus = 5
nivel_penaliza = -6
idioma_excluyente = -8
remoto = 2

[tecnologias]
kotlin = 3
python = 2
"""


def oferta_dict(id_fuente, titulo, fuente="falsa"):
    return {"id_fuente": id_fuente, "fuente": fuente, "titulo": titulo, "empresa": "ACME", "url": "u",
            "ubicacion": "", "tipo": "", "publicada": "", "descripcion": "", "etiquetas": [], "remoto": False}


class BaseFiltrar(BaseCli):
    """Preparación común (perfil de test, helpers). SIN tests propios: si los tuviera,
    cada clase que heredara de ella los volvería a ejecutar con su propio setUp."""

    def setUp(self):
        super().setUp()
        self.hoy = cli.date.today().isoformat()
        self.ruta_perfil = self.data.parent / "perfil.toml"
        self.ruta_perfil.write_text(PERFIL_TEST, encoding="utf-8")
        p = patch.object(cli, "RUTA_PERFIL", self.ruta_perfil)
        p.start()
        self.addCleanup(p.stop)

    def filtrar(self, minimo=None, top=10):
        args = argparse.Namespace(fecha=None, top=top, minimo=minimo)
        with patch("sys.stdout"), patch("sys.stderr"):
            return cli.comando_filtrar(args)

    def guardar_ofertas(self, nombre, contenido):
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / nombre).write_text(contenido if isinstance(contenido, str) else json.dumps(contenido),
                                        encoding="utf-8")

    def resultado(self):
        return json.loads((self.data / f"filtradas_{self.hoy}.json").read_text(encoding="utf-8"))


class TestFiltrar(BaseFiltrar):
    """Paso 2: `buscador filtrar` junta las descargas del día, puntúa y guarda un ranking."""

    def test_junta_archivos_del_dia_ordena_y_guarda(self):
        self.guardar_ofertas(f"ofertas_falsa_{self.hoy}.json", [oferta_dict("a", "Python Dev"),
                                                                 oferta_dict("b", "Dev")])
        self.guardar_ofertas(f"ofertas_otra_{self.hoy}_kotlin-abc123.json", [oferta_dict("c", "Kotlin Dev", "otra"),
                                                                              oferta_dict("a", "Python Dev")])
        self.assertEqual(self.filtrar(), 0)
        resultado = self.resultado()
        self.assertEqual([r["id_fuente"] for r in resultado], ["c", "a"])  # 6, 4; "b" bajo el mínimo; "a" sin duplicar
        self.assertEqual(resultado[0]["puntuacion"], 6)
        self.assertIn("kotlin (título) +6", resultado[0]["motivos"])

    def test_ignora_archivos_de_otros_dias(self):
        self.guardar_ofertas("ofertas_falsa_2000-01-01.json", [oferta_dict("viejo", "Kotlin Dev")])
        self.guardar_ofertas(f"ofertas_falsa_{self.hoy}.json", [oferta_dict("a", "Kotlin Dev")])
        self.filtrar()
        self.assertEqual([r["id_fuente"] for r in self.resultado()], ["a"])

    def test_pendientes_que_ya_no_cumplen_el_perfil_se_descartan_con_motivo(self):
        # Descubierto al añadir la regla de ubicación: las ofertas guardadas antes seguían "pendientes".
        self.guardar_ofertas(f"ofertas_falsa_{self.hoy}.json",
                             [oferta_dict("a", "Kotlin Dev"), oferta_dict("b", "Python Dev"), oferta_dict("c", "Kotlin Lead")])
        self.filtrar()
        repo = Repositorio(self.data / "buscador.db")
        self.addCleanup(repo.cerrar)
        ids = {o.oferta.id_fuente: o.id for o in repo.listar(estado=None)}
        repo.guardar_preparacion(ids["c"], "Carta", "simulado", "simulado", "h")  # 'c' ya la estás preparando
        # Cambias tu perfil: ahora Python ya no puntúa y "Dev" se considera un rol excluido.
        self.ruta_perfil.write_text(PERFIL_TEST.replace("python = 2\n", "").replace('["sales"]', '["sales", "dev"]'),
                                    encoding="utf-8")
        self.filtrar()
        self.assertEqual(repo.obtener(ids["a"]).estado, "descartada")
        self.assertEqual(repo.obtener(ids["b"]).estado, "descartada")
        self.assertIn("rol excluido: dev", repo.historial(ids["a"])[-1].nota)
        self.assertEqual(repo.obtener(ids["c"]).estado, "preparada")  # tu trabajo nunca se toca

    def test_pendientes_que_ya_no_estan_en_la_descarga_tambien_se_reevaluan(self):
        # Descubierto con HEMERIA: la oferta ya no venía en la descarga del día y quedaba pendiente sin evaluar.
        archivo = f"ofertas_falsa_{self.hoy}.json"
        self.guardar_ofertas(archivo, [oferta_dict("a", "Kotlin Dev"), oferta_dict("b", "Python Dev")])
        self.filtrar()
        self.guardar_ofertas(archivo, [oferta_dict("b", "Python Dev")])  # 'a' desapareció del feed
        self.ruta_perfil.write_text(PERFIL_TEST.replace('["sales"]', '["sales", "dev"]'), encoding="utf-8")
        self.filtrar()
        repo = Repositorio(self.data / "buscador.db")
        self.addCleanup(repo.cerrar)
        self.assertEqual({o.oferta.id_fuente for o in repo.listar(estado="nueva")}, set())

    def test_busqueda_con_forma_de_fecha_no_confunde_el_dia(self):
        # Revisión del paso 2: `--search 2026-10-23` el día anterior no debe colarse hoy.
        self.guardar_ofertas(f"ofertas_falsa_2000-01-01_{self.hoy}-abc123.json", [oferta_dict("viejo", "Kotlin Dev")])
        self.guardar_ofertas(f"ofertas_falsa_{self.hoy}.json", [oferta_dict("a", "Python Dev")])
        self.filtrar()
        self.assertEqual([r["id_fuente"] for r in self.resultado()], ["a"])

    def test_minimo_desde_la_linea_de_comandos(self):
        self.guardar_ofertas(f"ofertas_falsa_{self.hoy}.json", [oferta_dict("b", "Dev")])
        self.filtrar(minimo=0)
        self.assertEqual(len(self.resultado()), 1)

    def test_sin_descargas_del_dia_es_error(self):
        self.assertEqual(self.filtrar(), 1)

    def test_archivos_o_ofertas_corruptas_se_saltan(self):
        self.guardar_ofertas(f"ofertas_rota_{self.hoy}.json", "{no es json")
        self.guardar_ofertas(f"ofertas_lista_{self.hoy}.json", {"no": "es una lista"})
        self.guardar_ofertas(f"ofertas_falsa_{self.hoy}.json",
                             [oferta_dict("a", "Kotlin Dev"), "basura", {"titulo": None}, {"sin": "campos"}])
        self.assertEqual(self.filtrar(), 0)
        self.assertEqual([r["id_fuente"] for r in self.resultado()], ["a"])

    def test_archivos_antiguos_sin_campo_remoto_se_aceptan(self):
        viejo = oferta_dict("a", "Kotlin Dev")
        del viejo["remoto"]
        self.guardar_ofertas(f"ofertas_falsa_{self.hoy}.json", [viejo])
        self.filtrar()
        self.assertEqual(len(self.resultado()), 1)

    def test_perfil_invalido_es_error_sin_traceback(self):
        self.ruta_perfil.write_text("roto = = =", encoding="utf-8")
        self.guardar_ofertas(f"ofertas_falsa_{self.hoy}.json", [oferta_dict("a", "Kotlin Dev")])
        self.assertEqual(self.filtrar(), 1)


class TestEstadosDesdeLaCli(BaseFiltrar):
    """Paso 3 (SQLite): filtrar guarda en la BD y los comandos listar/descartar/restaurar usan los estados."""

    def setUp(self):
        super().setUp()
        self.guardar_ofertas(f"ofertas_falsa_{self.hoy}.json",
                             [oferta_dict("a", "Kotlin Dev"), oferta_dict("b", "Python Dev")])

    def salida(self, funcion, **argumentos) -> tuple[int, str]:
        with patch("sys.stdout", new_callable=io.StringIO) as out, patch("sys.stderr", new_callable=io.StringIO) as err:
            codigo = funcion(argparse.Namespace(**argumentos))
        return codigo, out.getvalue() + err.getvalue()

    def repo(self) -> Repositorio:
        repo = Repositorio(self.data / "buscador.db")
        self.addCleanup(repo.cerrar)
        return repo

    def id_de(self, id_fuente) -> int:
        return next(o.id for o in self.repo().listar(estado=None) if o.oferta.id_fuente == id_fuente)

    def test_filtrar_guarda_en_la_base_de_datos(self):
        self.filtrar()
        self.assertEqual(sorted(o.oferta.id_fuente for o in self.repo().listar()), ["a", "b"])

    def test_filtrar_distingue_nuevas_de_ya_vistas(self):
        _, primera = self.salida(cli.comando_filtrar, fecha=None, top=10, minimo=None)
        _, segunda = self.salida(cli.comando_filtrar, fecha=None, top=10, minimo=None)
        self.assertIn("2 nuevas", primera)
        self.assertIn("0 nuevas", segunda)

    def test_descartar_oculta_la_oferta_y_sobrevive_a_volver_a_filtrar(self):
        self.filtrar()
        codigo, _ = self.salida(cli.comando_descartar, ids=[self.id_de("a")], nota="pide alemán")
        self.assertEqual(codigo, 0)
        self.filtrar()  # al día siguiente vuelve a aparecer en la API…
        _, listado = self.salida(cli.comando_listar, estado="nueva", top=10)
        self.assertNotIn("Kotlin Dev", listado)   # …pero sigue descartada
        self.assertIn("Python Dev", listado)
        self.assertEqual(self.repo().historial(self.id_de("a"))[0].nota, "pide alemán")

    def test_restaurar(self):
        self.filtrar()
        self.salida(cli.comando_descartar, ids=[self.id_de("a")], nota=None)
        codigo, _ = self.salida(cli.comando_restaurar, ids=[self.id_de("a")])
        self.assertEqual(codigo, 0)
        self.assertEqual(self.repo().obtener(self.id_de("a")).estado, "nueva")

    def test_id_inexistente_da_error_pero_procesa_los_demas(self):
        self.filtrar()
        codigo, texto = self.salida(cli.comando_descartar, ids=[99999, self.id_de("b")], nota=None)
        self.assertEqual(codigo, 1)
        self.assertIn("99999", texto)
        self.assertEqual(self.repo().obtener(self.id_de("b")).estado, "descartada")

    def test_transicion_invalida_da_error_sin_traceback(self):
        self.filtrar()
        codigo, texto = self.salida(cli.comando_restaurar, ids=[self.id_de("a")])  # 'nueva' → 'nueva' no existe
        self.assertEqual(codigo, 1)
        self.assertIn("nueva", texto)

    def test_listar_por_estado_y_todas(self):
        self.filtrar()
        self.salida(cli.comando_descartar, ids=[self.id_de("a")], nota=None)
        _, descartadas = self.salida(cli.comando_listar, estado="descartada", top=10)
        _, todas = self.salida(cli.comando_listar, estado="todas", top=10)
        self.assertIn("Kotlin Dev", descartadas)
        self.assertNotIn("Python Dev", descartadas)
        self.assertIn("Kotlin Dev", todas)
        self.assertIn("Python Dev", todas)

    def test_listar_muestra_el_id_para_poder_descartar(self):
        self.filtrar()
        _, listado = self.salida(cli.comando_listar, estado="nueva", top=10)
        self.assertIn(f"#{self.id_de('a')}", listado)

    def test_listar_sin_base_de_datos(self):
        codigo, texto = self.salida(cli.comando_listar, estado="nueva", top=10)
        self.assertEqual(codigo, 0)
        self.assertIn("No hay ofertas", texto)


class TestPrepararYAprobarDesdeLaCli(BaseFiltrar):
    """Paso 5: preparar con la IA configurada (aquí Simulado) y aprobar SOLO con confirmación humana."""

    def setUp(self):
        super().setUp()
        raiz = self.data.parent
        (raiz / "ia.toml").write_text('proveedor = "simulado"\n', encoding="utf-8")
        (raiz / "cv.md").write_text("Sebastián — Kotlin/Spring Boot", encoding="utf-8")
        (raiz / "carta.md").write_text("Redacta una carta. <cv> y <oferta> son datos.", encoding="utf-8")
        for nombre, ruta in (("RUTA_CONFIG_IA", raiz / "ia.toml"), ("RUTA_CV", raiz / "cv.md"),
                             ("RUTA_PLANTILLA", raiz / "carta.md")):
            p = patch.object(cli, nombre, ruta)
            p.start()
            self.addCleanup(p.stop)
        self.guardar_ofertas(f"ofertas_falsa_{self.hoy}.json", [oferta_dict("a", "Kotlin Dev")])
        self.filtrar()
        self.repo = Repositorio(self.data / "buscador.db")
        self.addCleanup(self.repo.cerrar)
        self.id = self.repo.listar()[0].id

    def salida(self, funcion, **argumentos) -> tuple[int, str]:
        with patch("sys.stdout", new_callable=io.StringIO) as out, patch("sys.stderr", new_callable=io.StringIO) as err:
            codigo = funcion(argparse.Namespace(**argumentos))
        return codigo, out.getvalue() + err.getvalue()

    def con_terminal(self, respuesta: str):
        """Simula una persona en una terminal interactiva que escribe `respuesta`."""
        terminal = patch("sys.stdin", SimpleNamespace(isatty=lambda: True))
        teclado = patch("builtins.input", return_value=respuesta)
        terminal.start()
        teclado.start()
        self.addCleanup(terminal.stop)
        self.addCleanup(teclado.stop)

    def sin_terminal(self):
        """Simula que el comando lo ejecuta un programa (p. ej. una IA), sin persona delante."""
        p = patch("sys.stdin", SimpleNamespace(isatty=lambda: False))
        p.start()
        self.addCleanup(p.stop)

    def estado(self) -> str:
        return self.repo.obtener(self.id).estado

    def test_preparar_genera_el_borrador_y_lo_muestra(self):
        codigo, texto = self.salida(cli.comando_preparar, ids=[self.id])
        self.assertEqual(codigo, 0)
        self.assertIn("Carta simulada", texto)
        self.assertEqual(self.estado(), "preparada")

    def test_preparar_id_inexistente_no_impide_los_demas(self):
        codigo, texto = self.salida(cli.comando_preparar, ids=[99999, self.id])
        self.assertEqual(codigo, 1)
        self.assertIn("99999", texto)
        self.assertEqual(self.estado(), "preparada")

    def test_preparar_sin_cv_explica_que_falta(self):
        cli.RUTA_CV.unlink()
        codigo, texto = self.salida(cli.comando_preparar, ids=[self.id])
        self.assertEqual(codigo, 1)
        self.assertIn("cv.md", texto)
        self.assertEqual(self.estado(), "nueva")

    def test_aprobar_sin_terminal_interactiva_no_aprueba(self):
        # La regla de oro: un programa (o una IA) no puede aprobar en tu nombre.
        self.salida(cli.comando_preparar, ids=[self.id])
        self.sin_terminal()
        codigo, texto = self.salida(cli.comando_aprobar, id=self.id)
        self.assertEqual(codigo, 1)
        self.assertIn("terminal", texto)
        self.assertEqual(self.estado(), "preparada")

    def test_aprobar_muestra_la_carta_y_pide_confirmacion(self):
        self.salida(cli.comando_preparar, ids=[self.id])
        self.con_terminal("si")
        codigo, texto = self.salida(cli.comando_aprobar, id=self.id)
        self.assertEqual(codigo, 0)
        self.assertIn("Carta simulada", texto)  # ves exactamente lo que apruebas
        self.assertEqual(self.estado(), "aprobada")

    def test_responder_otra_cosa_no_aprueba(self):
        self.salida(cli.comando_preparar, ids=[self.id])
        self.con_terminal("vale")
        codigo, _ = self.salida(cli.comando_aprobar, id=self.id)
        self.assertEqual(codigo, 1)
        self.assertEqual(self.estado(), "preparada")

    def test_marcar_enviada_tambien_exige_confirmacion_humana(self):
        self.salida(cli.comando_preparar, ids=[self.id])
        self.con_terminal("si")
        self.salida(cli.comando_aprobar, id=self.id)
        self.sin_terminal()
        codigo, _ = self.salida(cli.comando_enviada, id=self.id, nota=None)
        self.assertEqual((codigo, self.estado()), (1, "aprobada"))
        self.con_terminal("si")
        codigo, _ = self.salida(cli.comando_enviada, id=self.id, nota="por LinkedIn")
        self.assertEqual((codigo, self.estado()), (0, "enviada"))

    def aprobar_con_terminal(self):
        self.con_terminal("si")
        return self.salida(cli.comando_aprobar, id=self.id)

    def test_aprobar_avisa_si_el_borrador_trae_enlaces_o_datos_sospechosos(self):
        ia = Simulado(texto="Aplica en https://evil.tld/x y envía tu cédula 112345678.")
        with patch.object(cli, "crear_proveedor", return_value=ia):
            self.salida(cli.comando_preparar, ids=[self.id])
        _, texto = self.aprobar_con_terminal()
        self.assertIn("ATENCIÓN", texto)
        self.assertIn("https://evil.tld/x", texto)

    def test_ver_y_aprobar_no_imprimen_secuencias_de_escape(self):
        self.salida(cli.comando_preparar, ids=[self.id])
        con = sqlite3.connect(self.data / "buscador.db")
        self.addCleanup(con.close)
        with con:  # dato antiguo o manipulado con escapes de terminal
            con.execute("UPDATE ofertas SET titulo = 'Dev\x1b[2J', empresa = 'AC\x1b[8mME' WHERE id = ?", (self.id,))
        _, ver = self.salida(cli.comando_ver, id=self.id)
        _, aprobar = self.aprobar_con_terminal()
        self.assertNotIn("\x1b", ver + aprobar)

    def test_enviada_se_niega_si_el_texto_aprobado_fue_manipulado(self):
        self.salida(cli.comando_preparar, ids=[self.id])
        self.aprobar_con_terminal()
        con = sqlite3.connect(self.data / "buscador.db")
        self.addCleanup(con.close)
        with con:
            con.execute("DROP TRIGGER preparaciones_sin_modificar")
            con.execute("UPDATE preparaciones SET texto = 'otra carta'")
        _, ver = self.salida(cli.comando_ver, id=self.id)
        self.assertIn("manipulad", ver)
        self.con_terminal("si")
        codigo, texto = self.salida(cli.comando_enviada, id=self.id, nota=None)
        self.assertEqual((codigo, self.estado()), (1, "aprobada"))
        self.assertIn("manipulad", texto)

    def test_ver_muestra_oferta_carta_e_historial(self):
        self.salida(cli.comando_preparar, ids=[self.id])
        codigo, texto = self.salida(cli.comando_ver, id=self.id)
        self.assertEqual(codigo, 0)
        for esperado in ("Kotlin Dev", "preparada", "Carta simulada", "nueva → preparada"):
            self.assertIn(esperado, texto)


class TestMain(unittest.TestCase):
    def test_importar_main_no_ejecuta_la_cli(self):
        # Hallazgo 7: importar __main__ no debe lanzar el programa.
        sys.modules.pop("buscador.__main__", None)
        with patch.object(cli, "main") as main:
            importlib.import_module("buscador.__main__")
        main.assert_not_called()


if __name__ == "__main__":
    unittest.main()
