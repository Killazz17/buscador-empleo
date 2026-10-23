"""Tests de los hallazgos de ecc:python-reviewer: datos externos no confiables.

Escritos ANTES de la corrección (TDD): primero fallan (RED), luego se arregla el código (GREEN).
Mockeamos urllib.request.urlopen para simular la red sin hacer peticiones reales.
"""

import http.client
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from buscador.fuentes.arbeitnow import Arbeitnow
from buscador.fuentes.base import FuenteError
from buscador.fuentes.remotive import Remotive

URLOPEN = "urllib.request.urlopen"


def respuesta(cuerpo):
    """Imita la respuesta de urlopen: un archivo de bytes usable con `with`."""
    datos = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode()
    return io.BytesIO(datos)


def pagina(jobs, next_url=None):
    return {"data": jobs, "links": {"next": next_url}}


def job(slug="a", **extra):
    base = {"slug": slug, "title": "Dev", "company_name": "ACME", "url": "u",
            "location": "Berlin", "remote": False, "tags": [], "job_types": [], "created_at": 1700000000}
    base.update(extra)
    return base


# Arbeitnow espera entre páginas por cortesía; en los tests no queremos esperar de verdad.
@patch("time.sleep", lambda s: None)
class TestErroresDeRed(unittest.TestCase):
    """Hallazgo 2: cualquier error de red/lectura debe convertirse en FuenteError."""

    def assert_fuente_error(self, error):
        for clase in (Remotive, Arbeitnow):
            with self.subTest(fuente=clase.nombre), patch(URLOPEN, side_effect=error):
                with self.assertRaises(FuenteError):
                    clase().descargar()

    def test_timeout(self):
        self.assert_fuente_error(TimeoutError("timed out"))

    def test_conexion_reseteada(self):
        self.assert_fuente_error(ConnectionResetError("reset"))

    def test_respuesta_incompleta(self):
        self.assert_fuente_error(http.client.IncompleteRead(b""))

    def test_http_500(self):
        self.assert_fuente_error(urllib.error.HTTPError("u", 500, "Server Error", {}, None))

    def test_bytes_invalidos(self):
        for clase in (Remotive, Arbeitnow):
            with self.subTest(fuente=clase.nombre), patch(URLOPEN, return_value=respuesta(b"\xff\xfe\x00basura")):
                with self.assertRaises(FuenteError):
                    clase().descargar()


@patch("time.sleep", lambda s: None)
class TestFormaDeLaRespuesta(unittest.TestCase):
    """Hallazgo 3: JSON válido pero con forma inesperada."""

    def test_respuesta_que_no_es_objeto(self):
        for cuerpo in ([], None, "texto"):
            for clase in (Remotive, Arbeitnow):
                with self.subTest(fuente=clase.nombre, cuerpo=cuerpo), patch(URLOPEN, return_value=respuesta(cuerpo)):
                    with self.assertRaises(FuenteError):
                        clase().descargar()

    def test_remotive_jobs_null(self):
        with patch(URLOPEN, return_value=respuesta({"jobs": None})):
            with self.assertRaises(FuenteError):
                Remotive().descargar()

    def test_arbeitnow_links_null(self):
        with patch(URLOPEN, return_value=respuesta({"data": [job()], "links": None})):
            _, ofertas = Arbeitnow().descargar()
        self.assertEqual(len(ofertas), 1)

    def test_arbeitnow_no_sigue_next_a_otro_host(self):
        malas = ["https://evil.example/api", "file:///etc/passwd", ""]
        for next_url in malas:
            with self.subTest(next_url=next_url), patch(URLOPEN, return_value=respuesta(pagina([job()], next_url))) as u:
                Arbeitnow().descargar()
            self.assertEqual(u.call_count, 1)

    def test_job_que_no_es_dict_se_salta(self):
        with patch(URLOPEN, return_value=respuesta(pagina([job("a"), "basura", None, job("b")]))):
            fuente = Arbeitnow()
            _, ofertas = fuente.descargar()
        self.assertEqual([o.id_fuente for o in ofertas], ["a", "b"])
        self.assertTrue(fuente.avisos)  # se informa de lo descartado


@patch("time.sleep", lambda s: None)
class TestPaginacionParcial(unittest.TestCase):
    """Hallazgo 2b: si falla una página posterior, se conserva lo ya descargado."""

    def test_fallo_en_pagina_2_conserva_pagina_1(self):
        efectos = [respuesta(pagina([job("a")], "https://www.arbeitnow.com/api/job-board-api?page=2")),
                   TimeoutError("timed out")]
        with patch(URLOPEN, side_effect=efectos):
            fuente = Arbeitnow()
            crudo, ofertas = fuente.descargar()
        self.assertEqual([o.id_fuente for o in ofertas], ["a"])
        self.assertEqual(len(crudo["paginas"]), 1)
        self.assertTrue(any("parcial" in a.lower() for a in fuente.avisos))

    def test_fallo_en_pagina_1_es_error(self):
        with patch(URLOPEN, side_effect=TimeoutError("timed out")):
            with self.assertRaises(FuenteError):
                Arbeitnow().descargar()


@patch("time.sleep", lambda s: None)
class TestCamposRaros(unittest.TestCase):
    """Hallazgos 4 y 5: tipos inesperados en campos individuales."""

    def descargar_uno(self, **campos):
        with patch(URLOPEN, return_value=respuesta(pagina([job(**campos)]))):
            _, ofertas = Arbeitnow().descargar(search="dev")
        return ofertas

    def test_created_at_raro_no_rompe(self):
        for valor in ("1700000000", "2026-10-22", None, 10**20, True, {"x": 1}):
            with self.subTest(created_at=valor):
                ofertas = self.descargar_uno(created_at=valor)
                self.assertEqual(len(ofertas), 1)
        self.assertEqual(self.descargar_uno(created_at="1700000000")[0].publicada, "2023-11-14T22:13:20+00:00")

    def test_campos_null(self):
        o = self.descargar_uno(title="Dev", tags=None, job_types=None, slug=None,
                               company_name=None, location=None)[0]
        self.assertEqual((o.etiquetas, o.tipo, o.id_fuente, o.empresa, o.ubicacion), ([], "", "", "", ""))

    def test_tipos_se_normalizan_a_texto(self):
        o = self.descargar_uno(slug=42, tags=["Java", 7], job_types=["full time", None])[0]
        self.assertEqual(o.id_fuente, "42")
        self.assertEqual(o.etiquetas, ["Java", "7"])
        self.assertEqual(o.tipo, "full time")

    def test_titulo_null_con_search_no_rompe(self):
        with patch(URLOPEN, return_value=respuesta(pagina([job(title=None, tags=None)]))):
            _, ofertas = Arbeitnow().descargar(search="dev")
        self.assertEqual(ofertas, [])

    def test_created_at_con_digitos_unicode(self):
        # Segunda revisión, hallazgo 3: "²".isdigit() es True pero int("²") lanza ValueError.
        ofertas = self.descargar_uno(created_at="²")
        self.assertEqual(len(ofertas), 1)

    def test_links_de_tipo_raro_conserva_lo_descargado(self):
        # Segunda revisión, hallazgo 2: links truthy pero no dict ("x", [1], 5) no debe perder la página.
        for links in ("x", [1], 5):
            with self.subTest(links=links), patch(URLOPEN, return_value=respuesta({"data": [job()], "links": links})):
                _, ofertas = Arbeitnow().descargar()
            self.assertEqual(len(ofertas), 1)

    def test_remotive_campos_null(self):
        crudo = {"jobs": [{"id": None, "title": None, "tags": None, "company_name": None}]}
        with patch(URLOPEN, return_value=respuesta(crudo)):
            _, ofertas = Remotive().descargar()
        self.assertEqual((ofertas[0].id_fuente, ofertas[0].titulo, ofertas[0].etiquetas), ("", "", []))


if __name__ == "__main__":
    unittest.main()
