"""Tests de la interfaz web local (FastAPI + HTML). Sin servidor real: TestClient llama a la app en memoria.

Foco en seguridad, porque la página muestra texto de TERCEROS y tiene un botón de aprobar:
XSS (autoescape), CSRF (token por arranque), DNS rebinding (solo localhost) y confirmación escrita.
"""

import argparse
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from buscador import cli
from buscador.almacenamiento import Repositorio
from buscador.filtros import Evaluacion
from buscador.modelos import Oferta
from buscador.web import crear_app

HOY = "2026-10-23T08:00:00+00:00"


class BaseWeb(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        raiz = Path(tmp.name)
        self.ruta_db = raiz / "buscador.db"
        (raiz / "cv.md").write_text("CV de prueba: Kotlin y Spring", encoding="utf-8")
        (raiz / "carta.md").write_text("Instrucciones de la carta", encoding="utf-8")
        self.repo = Repositorio(self.ruta_db)
        self.addCleanup(self.repo.cerrar)
        ofertas = [Oferta("a", "test", "Kotlin Dev", "ACME", "https://x/a", "Remote", "", "", "<p>Kotlin</p>", [], True),
                   Oferta("b", "test", "<script>alert('xss')</script>", "Evil", "https://x/b", "", "", "", "", [], True)]
        self.repo.guardar_evaluaciones([Evaluacion(o, 10, ["kotlin +6"]) for o in ofertas], ahora=HOY)
        self.ids = {o.oferta.id_fuente: o.id for o in self.repo.listar(estado=None)}
        self.app = crear_app(self.ruta_db, raiz / "cv.md", raiz / "carta.md")
        self.token = self.app.state.token
        self.web = TestClient(self.app, base_url="http://127.0.0.1")

    def estado(self, clave="a") -> str:
        return self.repo.obtener(self.ids[clave]).estado

    def post(self, ruta, token=True, **datos):
        if token:
            datos["token"] = self.token
        return self.web.post(ruta, data=datos, follow_redirects=False)

    def preparar(self, carta="Dear team,\r\n\r\n\r\n\r\nI apply.\r\nSeb"):
        return self.post(f"/ofertas/{self.ids['a']}/preparar", carta=carta)


class TestLista(BaseWeb):
    def test_lista_pendientes(self):
        r = self.web.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Kotlin Dev", r.text)
        self.assertIn(f"/ofertas/{self.ids['a']}", r.text)

    def test_texto_de_terceros_se_escapa(self):
        r = self.web.get("/")
        self.assertNotIn("<script>alert", r.text)
        self.assertIn("&lt;script&gt;", r.text)

    def test_enlaces_que_no_son_http_no_se_vuelven_clicables(self):
        # El autoescape NO protege un href="javascript:...": hay que filtrar el esquema de la URL.
        malicioso = Oferta("c", "test", "Trampa", "X", "javascript:alert(1)", "", "", "", "", [], True)
        self.repo.guardar_evaluaciones([Evaluacion(malicioso, 10, [])], ahora=HOY)
        oferta_id = self.repo.buscar_id("test", "c")
        for ruta in ("/", f"/ofertas/{oferta_id}"):
            with self.subTest(ruta=ruta):
                self.assertNotIn('href="javascript:', self.web.get(ruta).text)

    def test_filtra_por_estado(self):
        self.repo.cambiar_estado(self.ids["a"], "descartada", ahora=HOY)
        self.assertNotIn("Kotlin Dev", self.web.get("/").text)
        self.assertIn("Kotlin Dev", self.web.get("/?estado=descartada").text)

    def test_solo_acepta_peticiones_a_localhost(self):
        # DNS rebinding: una web maliciosa que apunte su dominio a 127.0.0.1 no debe poder usar la app.
        self.assertEqual(self.web.get("/", headers={"host": "evil.example"}).status_code, 400)


class TestAcciones(BaseWeb):
    def test_descartar_y_restaurar(self):
        r = self.post(f"/ofertas/{self.ids['a']}/descartar", nota="no me interesa")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.estado(), "descartada")
        self.post(f"/ofertas/{self.ids['a']}/restaurar")
        self.assertEqual(self.estado(), "nueva")

    def test_sin_token_o_con_token_falso_no_hace_nada(self):
        # CSRF: otra web abierta en tu navegador no conoce el token de esta sesión.
        self.assertEqual(self.post(f"/ofertas/{self.ids['a']}/descartar", token=False).status_code, 403)
        r = self.web.post(f"/ofertas/{self.ids['a']}/descartar", data={"token": "falso"}, follow_redirects=False)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.estado(), "nueva")

    def test_oferta_inexistente(self):
        self.assertEqual(self.web.get("/ofertas/99999").status_code, 404)


class TestPreparar(BaseWeb):
    def test_pagina_de_preparar_muestra_el_prompt_para_copiar(self):
        r = self.web.get(f"/ofertas/{self.ids['a']}/preparar")
        self.assertEqual(r.status_code, 200)
        for parte in ("Instrucciones de la carta", "CV de prueba", "Kotlin Dev"):
            self.assertIn(parte, r.text)

    def test_guardar_la_carta_pegada_normaliza_y_pasa_a_preparada(self):
        self.assertEqual(self.preparar().status_code, 303)
        prep = self.repo.ultima_preparacion(self.ids["a"])
        self.assertEqual(prep.texto, "Dear team,\n\nI apply.\nSeb")
        self.assertEqual((prep.proveedor, self.estado()), ("manual", "preparada"))

    def test_carta_vacia_no_se_guarda(self):
        self.assertEqual(self.preparar(carta="   ").status_code, 400)
        self.assertIsNone(self.repo.ultima_preparacion(self.ids["a"]))


class TestAprobarYEnviar(BaseWeb):
    def aprobar(self, confirmacion="si", version=1, token=True):
        return self.post(f"/ofertas/{self.ids['a']}/aprobar", token=token, confirmacion=confirmacion, version=version)

    def test_pagina_de_aprobar_muestra_carta_y_avisos(self):
        self.preparar(carta="Visit https://evil.example/apply")
        r = self.web.get(f"/ofertas/{self.ids['a']}/aprobar")
        self.assertIn("https://evil.example/apply", r.text)
        self.assertIn("ATENCIÓN", r.text)

    def test_aprobar_exige_escribir_si_y_token(self):
        self.preparar()
        self.assertEqual(self.aprobar(confirmacion="vale").status_code, 400)
        self.assertEqual(self.aprobar(token=False).status_code, 403)
        self.assertEqual(self.estado(), "preparada")
        self.assertEqual(self.aprobar().status_code, 303)
        self.assertEqual(self.estado(), "aprobada")

    def test_no_aprueba_una_version_que_no_viste(self):
        self.preparar()
        self.preparar(carta="Versión 2")
        self.assertEqual(self.aprobar(version=1).status_code, 409)
        self.assertEqual(self.estado(), "preparada")

    def test_enviada_exige_confirmacion_y_detecta_manipulacion(self):
        self.preparar()
        self.aprobar()
        ruta = f"/ofertas/{self.ids['a']}/enviada"
        self.assertEqual(self.post(ruta, confirmacion="no").status_code, 400)
        con = sqlite3.connect(self.ruta_db)
        self.addCleanup(con.close)
        with con:
            con.execute("DROP TRIGGER preparaciones_sin_modificar")
            con.execute("UPDATE preparaciones SET texto = 'otra'")
        self.assertEqual(self.post(ruta, confirmacion="si").status_code, 409)
        self.assertEqual(self.estado(), "aprobada")

    def test_enviada(self):
        self.preparar()
        self.aprobar()
        self.assertEqual(self.post(f"/ofertas/{self.ids['a']}/enviada", confirmacion="si", nota="web").status_code, 303)
        self.assertEqual(self.estado(), "enviada")


class TestComandoWeb(unittest.TestCase):
    def test_arranca_solo_en_localhost_y_abre_el_navegador(self):
        with patch("uvicorn.run") as run, patch("webbrowser.open") as abrir:
            cli.comando_web(argparse.Namespace(puerto=8765, sin_navegador=False))
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(run.call_args.kwargs["port"], 8765)
        abrir.assert_called_once()


if __name__ == "__main__":
    unittest.main()
