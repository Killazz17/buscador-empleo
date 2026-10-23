"""Tests de los adaptadores de fuentes, SIN red.

Sustituimos `_pedir` por respuestas falsas (fixtures). Así los tests son rápidos,
no gastan el límite de peticiones de las APIs y no fallan si una API está caída.
"""

import unittest
from unittest.mock import patch

from buscador.fuentes import FUENTES
from buscador.fuentes.arbeitnow import URL_API, Arbeitnow
from buscador.fuentes.base import Fuente, FuenteError
from buscador.fuentes.getonboard import MAX_PAGINAS as MAX_PAGINAS_GOB
from buscador.fuentes.getonboard import GetOnBoard
from buscador.fuentes.remotive import Remotive
from buscador.fuentes.weworkremotely import WeWorkRemotely


def job_arbeitnow(slug, titulo, tags=(), remote=False, location="Berlin"):
    return {
        "slug": slug, "company_name": "ACME", "title": titulo, "description": "<p>x</p>",
        "remote": remote, "url": f"https://www.arbeitnow.com/jobs/{slug}", "tags": list(tags),
        "job_types": ["full time"], "location": location, "created_at": 1700000000,
    }


class TestRegistro(unittest.TestCase):
    def test_todas_las_fuentes_cumplen_la_interfaz(self):
        for nombre, clase in FUENTES.items():
            self.assertTrue(issubclass(clase, Fuente))
            self.assertEqual(clase.nombre, nombre)


class TestRemotive(unittest.TestCase):
    def test_normaliza_un_job(self):
        crudo = {"jobs": [{
            "id": 123, "title": "Backend Dev", "company_name": "ACME",
            "url": "https://remotive.com/x", "candidate_required_location": "Worldwide",
            "job_type": "full_time", "publication_date": "2026-10-22T10:00:00",
            "description": "<p>x</p>", "tags": ["java"],
        }]}
        with patch.object(Remotive, "_pedir", return_value=crudo):
            _, ofertas = Remotive().descargar()
        o = ofertas[0]
        self.assertEqual((o.id_fuente, o.fuente, o.titulo, o.ubicacion), ("123", "remotive", "Backend Dev", "Worldwide"))
        self.assertTrue(o.remoto)  # Remotive solo publica ofertas remotas

    def test_campos_faltantes_no_rompen(self):
        with patch.object(Remotive, "_pedir", return_value={"jobs": [{}]}):
            _, ofertas = Remotive().descargar()
        self.assertEqual(ofertas[0].titulo, "")


# Arbeitnow espera entre páginas por cortesía; en los tests no queremos esperar de verdad.
@patch("time.sleep", lambda s: None)
class TestArbeitnow(unittest.TestCase):
    def test_sigue_la_paginacion(self):
        paginas = [
            # links.next debe apuntar a la propia API: otros hosts se ignoran (ver test_errores.py).
            {"data": [job_arbeitnow("a", "Java Dev")], "links": {"next": f"{URL_API}?page=2"}},
            {"data": [job_arbeitnow("b", "Kotlin Dev")], "links": {"next": None}},
        ]
        with patch.object(Arbeitnow, "_pedir", side_effect=paginas) as pedir:
            crudo, ofertas = Arbeitnow().descargar()
        self.assertEqual(pedir.call_count, 2)
        self.assertEqual(len(crudo["paginas"]), 2)
        self.assertEqual([o.id_fuente for o in ofertas], ["a", "b"])

    def test_respeta_el_tope_de_paginas(self):
        pagina_infinita = {"data": [], "links": {"next": f"{URL_API}?page=n"}}
        with patch.object(Arbeitnow, "_pedir", return_value=pagina_infinita) as pedir, \
             patch("buscador.fuentes.arbeitnow.MAX_PAGINAS", 2):
            Arbeitnow().descargar()
        self.assertEqual(pedir.call_count, 2)

    def test_filtra_search_en_titulo_y_etiquetas(self):
        pagina = {"data": [
            job_arbeitnow("a", "Java Developer"),
            job_arbeitnow("b", "Backend Engineer", tags=["Kotlin"]),
            job_arbeitnow("c", "Sales Manager"),
        ], "links": {"next": None}}
        with patch.object(Arbeitnow, "_pedir", return_value=pagina):
            _, ofertas = Arbeitnow().descargar(search="kotlin")
        self.assertEqual([o.id_fuente for o in ofertas], ["b"])

    def test_normaliza_remote_y_fecha(self):
        pagina = {"data": [job_arbeitnow("a", "Dev", remote=True)], "links": {"next": None}}
        with patch.object(Arbeitnow, "_pedir", return_value=pagina):
            _, ofertas = Arbeitnow().descargar()
        self.assertEqual(ofertas[0].ubicacion, "Remote (Berlin)")
        self.assertTrue(ofertas[0].remoto)
        self.assertEqual(ofertas[0].publicada, "2023-11-14T22:13:20+00:00")
        self.assertEqual(ofertas[0].tipo, "full time")


WWR_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
  <item>
    <title>DuckDuckGo: Senior Backend Engineer</title>
    <region>Anywhere in the World</region><country></country>
    <skills>Kotlin, PostgreSQL</skills><category>Back-End Programming</category><type>Full-Time</type>
    <description>&lt;p&gt;We use &lt;strong&gt;Kotlin&lt;/strong&gt;&lt;/p&gt;</description>
    <pubDate>Thu, 22 Oct 2026 10:00:00 +0000</pubDate>
    <guid>https://weworkremotely.com/remote-jobs/duckduckgo-senior-backend-engineer</guid>
    <link>https://weworkremotely.com/remote-jobs/duckduckgo-senior-backend-engineer</link>
  </item>
  <item>
    <title>Sin dos puntos en el titulo</title>
    <region>USA Only</region><country>United States</country>
    <guid>wwr-2</guid><link>https://weworkremotely.com/remote-jobs/x</link>
  </item>
</channel></rss>"""


@patch("time.sleep", lambda s: None)
class TestWeWorkRemotely(unittest.TestCase):
    def descargar(self, cuerpo=WWR_RSS, search=None):
        with patch.object(WeWorkRemotely, "_descargar", return_value=cuerpo) as descargar:
            crudo, ofertas = WeWorkRemotely().descargar(search)
        return descargar, crudo, ofertas

    def test_normaliza_un_item(self):
        _, _, ofertas = self.descargar()
        o = ofertas[0]
        self.assertEqual((o.fuente, o.empresa, o.titulo, o.ubicacion, o.remoto, o.tipo),
                         ("weworkremotely", "DuckDuckGo", "Senior Backend Engineer", "Anywhere in the World", True, "Full-Time"))
        self.assertEqual(o.id_fuente, "https://weworkremotely.com/remote-jobs/duckduckgo-senior-backend-engineer")
        self.assertEqual(o.etiquetas, ["Kotlin", "PostgreSQL", "Back-End Programming"])
        self.assertEqual(o.publicada, "2026-10-22T10:00:00+00:00")
        self.assertIn("<strong>Kotlin</strong>", o.descripcion)  # HTML real, ya desescapado por el XML

    def test_titulo_sin_empresa_y_region_con_pais(self):
        _, _, ofertas = self.descargar()
        o = ofertas[1]
        self.assertEqual((o.empresa, o.titulo, o.ubicacion), ("", "Sin dos puntos en el titulo", "USA Only, United States"))

    def test_lee_varias_categorias_y_quita_duplicados(self):
        descargar, crudo, ofertas = self.descargar()
        self.assertGreaterEqual(descargar.call_count, 2)  # back-end y full-stack
        self.assertEqual(len(ofertas), 2)  # el mismo RSS dos veces → sin duplicados
        self.assertEqual(len(crudo["feeds"]), descargar.call_count)

    def test_xml_roto_es_error_de_fuente(self):
        with self.assertRaises(FuenteError):
            self.descargar(cuerpo=b"<rss><channel><item>")

    def test_search_local(self):
        _, _, ofertas = self.descargar(search="kotlin")
        self.assertEqual([o.empresa for o in ofertas], ["DuckDuckGo"])


def job_gob(slug, titulo="Backend Developer", modalidad="fully_remote", paises=("Remote",), remoto=True):
    return {"id": slug, "type": "job", "links": {"public_url": f"https://www.getonbrd.com/jobs/{slug}"},
            "attributes": {"title": titulo, "description": "<ul><li>Kotlin y Spring</li></ul>",
                           "functions": "<p>Construir APIs</p>", "desirable": "<p>Docker</p>", "benefits": "",
                           "remote": remoto, "remote_modality": modalidad, "countries": list(paises),
                           "category_name": "Programming", "published_at": 1700000000,
                           "company": {"data": {"attributes": {"name": "ACME LATAM"}}}}}


@patch("time.sleep", lambda s: None)
class TestGetOnBoard(unittest.TestCase):
    def descargar(self, paginas, search=None):
        with patch.object(GetOnBoard, "_pedir", side_effect=paginas) as pedir:
            crudo, ofertas = GetOnBoard().descargar(search)
        return pedir, crudo, ofertas

    def test_normaliza_y_pide_la_empresa_expandida(self):
        pedir, _, [o] = self.descargar([{"data": [job_gob("a")], "meta": {"page": 1, "total_pages": 1}}])
        self.assertIn("expand", pedir.call_args.args[0])
        self.assertEqual((o.fuente, o.id_fuente, o.titulo, o.empresa, o.url, o.tipo, o.publicada),
                         ("getonboard", "a", "Backend Developer", "ACME LATAM", "https://www.getonbrd.com/jobs/a",
                          "fully_remote", "2023-11-14T22:13:20+00:00"))
        for parte in ("Kotlin y Spring", "Construir APIs", "Docker"):
            self.assertIn(parte, o.descripcion)

    def test_ubicacion_segun_modalidad(self):
        # Pensado para la regla de ubicación: remote_local = remoto SOLO para residentes de esos países.
        casos = {
            ("fully_remote", ("Remote",), True): ("Remote", True),
            ("remote_local", ("Chile",), True): ("Chile", True),
            ("hybrid", ("Costa Rica",), False): ("Costa Rica", False),
            ("no_remote", ("Colombia", "Perú"), False): ("Colombia, Perú", False),
        }
        for (modalidad, paises, remoto), esperado in casos.items():
            with self.subTest(modalidad=modalidad):
                _, _, [o] = self.descargar([{"data": [job_gob("x", modalidad=modalidad, paises=paises, remoto=remoto)],
                                             "meta": {"total_pages": 1}}])
                self.assertEqual((o.ubicacion, o.remoto), esperado)

    def test_pagina_hasta_el_total_o_el_tope(self):
        pagina = {"data": [job_gob("a")], "meta": {"total_pages": 99}}
        pedir, crudo, _ = self.descargar([pagina] * 10)
        self.assertEqual(pedir.call_count, MAX_PAGINAS_GOB)
        pedir, _, _ = self.descargar([{"data": [job_gob("a")], "meta": {"total_pages": 1}}])
        self.assertEqual(pedir.call_count, 1)

    def test_data_que_no_es_lista_es_error(self):
        with self.assertRaises(FuenteError):
            self.descargar([{"data": None}])

    def test_search_local(self):
        _, _, ofertas = self.descargar([{"data": [job_gob("a", "Kotlin Dev"), job_gob("b", "PHP Dev")],
                                         "meta": {"total_pages": 1}}], search="kotlin")
        self.assertEqual([o.id_fuente for o in ofertas], ["a"])

    def test_registradas(self):
        self.assertIs(FUENTES["getonboard"], GetOnBoard)
        self.assertIs(FUENTES["weworkremotely"], WeWorkRemotely)


if __name__ == "__main__":
    unittest.main()
