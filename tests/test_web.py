"""Tests de la interfaz web local (FastAPI + HTML). Sin servidor real: TestClient llama a la app en memoria.

Foco en seguridad, porque la página muestra texto de TERCEROS y tiene un botón de aprobar:
XSS (autoescape), CSRF (token por arranque), DNS rebinding (solo localhost) y confirmación escrita.
"""

import argparse
import io
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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

    def test_token_con_caracteres_no_ascii_da_403_y_no_500(self):
        # compare_digest con str lanza TypeError si hay no-ASCII: un token con "ñ" no debe tumbar el servidor.
        r = self.web.post(f"/ofertas/{self.ids['a']}/descartar", data={"token": "ñandú"}, follow_redirects=False)
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


class TestRediseno(BaseWeb):
    """Interfaz amigable: embudo con contadores, avisos tras cada acción y CSP con nonce."""

    def test_embudo_con_contadores_por_etapa(self):
        self.repo.cambiar_estado(self.ids["b"], "descartada", ahora=HOY)
        html = self.web.get("/").text
        for estado, esperado in (("nueva", "1"), ("descartada", "1"), ("enviada", "0")):
            with self.subTest(estado=estado):
                m = re.search(rf'id="etapa-{estado}".*?data-countup="(\d+)"', html, re.S)
                self.assertIsNotNone(m, f"falta la etapa {estado}")
                self.assertEqual(m.group(1), esperado)

    def test_acciones_redirigen_con_aviso(self):
        r = self.post(f"/ofertas/{self.ids['a']}/descartar")
        self.assertEqual(r.headers["location"], f"/?aviso=descartada&id={self.ids['a']}")
        self.assertIn("Oferta descartada", self.web.get(r.headers["location"]).text)

    def test_aviso_desconocido_no_se_refleja(self):
        r = self.web.get("/?aviso=<script>alert(1)</script>")
        self.assertNotIn("alert(1)", r.text)

    def test_csp_con_nonce_en_cada_script(self):
        r = self.web.get("/")
        csp = r.headers["content-security-policy"]
        nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
        self.assertIn("frame-ancestors 'none'", csp)
        scripts = re.findall(r"<script([^>]*)>", r.text)
        self.assertTrue(scripts)
        for atributos in scripts:
            self.assertIn(f'nonce="{nonce}"', atributos)
        self.assertNotEqual(nonce, re.search(r"'nonce-([^']+)'", self.web.get("/").headers["content-security-policy"]).group(1))


class TestCierreDeRama(BaseWeb):
    """Hallazgos de las revisiones (python-reviewer y security-reviewer) antes del PR."""

    def test_sin_recursos_externos_ni_fuentes_de_terceros(self):
        # Privacidad: cada carga no debe pedir nada a un tercero (IP expuesta, CSP más débil).
        r = self.web.get("/")
        self.assertNotIn("fonts.g", r.text)
        self.assertNotIn("https://", r.headers["content-security-policy"])

    def test_host_invalido_responde_400_con_cabeceras_de_seguridad(self):
        r = TestClient(self.app, base_url="http://evil.com").get("/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")

    def test_post_con_origin_ajeno_se_rechaza(self):
        # Defensa en profundidad: aunque el token se filtrara, una página ajena no puede enviar el POST.
        r = self.web.post(f"/ofertas/{self.ids['a']}/descartar", data={"token": self.token},
                          headers={"Origin": "http://evil.com"}, follow_redirects=False)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.estado(), "nueva")

    def test_post_con_origin_local_se_acepta(self):
        r = self.web.post(f"/ofertas/{self.ids['a']}/descartar", data={"token": self.token},
                          headers={"Origin": "http://127.0.0.1:8765"}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_errores_http_se_muestran_como_pagina_html(self):
        casos = (self.web.get("/ofertas/99999"),                                   # 404
                 self.web.post(f"/ofertas/{self.ids['a']}/descartar", data={"token": "x"}),  # 403
                 self.post(f"/ofertas/{self.ids['a']}/restaurar"))                 # 409: nueva → nueva
        for r, codigo, texto in zip(casos, (404, 403, 409), ("No existe la oferta #99999", "Token inválido", "")):
            with self.subTest(codigo=codigo):
                self.assertEqual(r.status_code, codigo)
                self.assertIn("text/html", r.headers["content-type"])
                self.assertIn(texto, r.text)
                self.assertIn("frame-ancestors", r.headers["content-security-policy"])

    def test_nota_larga_se_recorta(self):
        self.post(f"/ofertas/{self.ids['a']}/descartar", nota="x" * 5000)
        nota = self.repo.historial(self.ids["a"])[-1].nota
        self.assertLessEqual(len(nota), 500)

    def test_carta_enorme_se_rechaza_sin_guardar(self):
        r = self.preparar(carta="a" * 50_000)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.estado(), "nueva")


class TestComandoWeb(unittest.TestCase):
    def ejecutar(self, **args):
        """Ejecuta `comando_web` con uvicorn y el navegador simulados; devuelve (código, run, abrir)."""
        with patch("uvicorn.run") as run, patch("webbrowser.open") as abrir, redirect_stdout(io.StringIO()):
            codigo = cli.comando_web(argparse.Namespace(**{"puerto": 8765, "sin_navegador": False, **args}))
        return codigo, run, abrir

    def test_arranca_solo_en_localhost_y_abre_el_navegador(self):
        _, run, abrir = self.ejecutar()
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(run.call_args.kwargs["port"], 8765)
        abrir.assert_called_once()

    def test_sin_navegador_no_lo_abre(self):
        _, run, abrir = self.ejecutar(sin_navegador=True)
        abrir.assert_not_called()
        run.assert_called_once()

    def test_sin_dependencias_web_sale_con_codigo_1(self):
        # Simula que fastapi/uvicorn no están instalados: debe avisar, no romper con un traceback.
        with patch.dict(sys.modules, {"uvicorn": None}), redirect_stderr(io.StringIO()) as err:
            codigo = cli.comando_web(argparse.Namespace(puerto=8765, sin_navegador=True))
        self.assertEqual(codigo, 1)
        self.assertIn('".[web]"', err.getvalue())


if __name__ == "__main__":
    unittest.main()
