"""Tests del paso 2: puntuar ofertas contra el perfil del candidato.

`evaluar` es una función pura (sin red ni archivos): recibe una Oferta y un Perfil
y devuelve una Evaluacion con puntuación y motivos. Por eso es tan fácil de testear.
"""

import tempfile
import unittest
from pathlib import Path

from buscador.filtros import evaluar, evaluar_todas, limpiar_html, rechazadas
from buscador.modelos import Oferta
from buscador.perfil import Perfil, PerfilError, cargar_perfil

RAIZ = Path(__file__).resolve().parents[1]


def perfil(**cambios) -> Perfil:
    base = dict(
        tecnologias={"kotlin": 3, "java": 3, "python": 2, "c++": 1, "spring": 3},
        nivel_bonus=["junior", "intern"],
        nivel_penaliza=["senior", "lead"],
        roles_excluidos=["sales", "copywriter"],
        idiomas_excluyentes=["german", "deutsch"],
        puntos_nivel_bonus=5,
        puntos_nivel_penaliza=-6,
        puntos_idioma=-8,
        puntos_remoto=2,
        puntuacion_minima=3,
    )
    base.update(cambios)
    return Perfil(**base)


def oferta(titulo="Developer", descripcion="", etiquetas=(), remoto=False, id_fuente="1", fuente="test",
           ubicacion="Berlin") -> Oferta:
    return Oferta(id_fuente, fuente, titulo, "ACME", "https://x", ubicacion, "", "", descripcion,
                  list(etiquetas), remoto=remoto)


def puntos(o: Oferta, p: Perfil | None = None) -> int:
    return evaluar(o, p or perfil()).puntuacion


class TestTecnologias(unittest.TestCase):
    def test_en_titulo_vale_doble(self):
        self.assertEqual(puntos(oferta("Kotlin Developer")), 6)
        self.assertEqual(puntos(oferta(descripcion="We use kotlin daily")), 3)

    def test_etiquetas_cuentan(self):
        self.assertEqual(puntos(oferta(etiquetas=["Python"])), 2)

    def test_cada_tecnologia_cuenta_una_sola_vez(self):
        o = oferta("Kotlin Kotlin Engineer", descripcion="kotlin, KOTLIN", etiquetas=["Kotlin"])
        self.assertEqual(puntos(o), 6)  # solo la mejor coincidencia: título ×2

    def test_java_no_coincide_con_javascript(self):
        self.assertEqual(puntos(oferta("JavaScript Developer")), 0)

    def test_terminos_con_simbolos(self):
        self.assertEqual(puntos(oferta(descripcion="Experience with C++.")), 1)

    def test_no_distingue_mayusculas_ni_puntuacion(self):
        self.assertEqual(puntos(oferta(descripcion="SPRING, Kotlin.")), 6)

    def test_motivos_explican_cada_punto(self):
        motivos = evaluar(oferta("Kotlin Developer", descripcion="python"), perfil()).motivos
        self.assertIn("kotlin (título) +6", motivos)
        self.assertIn("python +2", motivos)


class TestHtml(unittest.TestCase):
    def test_limpia_etiquetas_y_entidades(self):
        self.assertEqual(limpiar_html("<li>Kotlin</li><li>Spring&nbsp;Boot</li>").split(), ["Kotlin", "Spring", "Boot"])

    def test_atributos_html_no_puntuan(self):
        self.assertEqual(puntos(oferta(descripcion='<a href="java.html">enlace</a>')), 0)

    def test_tecnologias_dentro_de_html_si_puntuan(self):
        self.assertEqual(puntos(oferta(descripcion="<ul><li>Kotlin</li><li>Spring</li></ul>")), 6)


class TestNivelYRol(unittest.TestCase):
    def test_junior_suma(self):
        self.assertEqual(puntos(oferta("Junior Developer")), 5)

    def test_senior_resta(self):
        self.assertEqual(puntos(oferta("Senior Developer")), -6)

    def test_nivel_solo_cuenta_en_titulo(self):
        self.assertEqual(puntos(oferta(descripcion="You will work with senior engineers")), 0)

    def test_rol_excluido_descarta(self):
        ev = evaluar(oferta("Sales Manager (Kotlin)"), perfil())
        self.assertTrue(ev.descartada)
        self.assertTrue(any("sales" in m for m in ev.motivos))


class TestIdiomaYModalidad(unittest.TestCase):
    def test_idioma_excluyente_resta(self):
        self.assertEqual(puntos(oferta(descripcion="Fluent German is required")), -8)

    def test_texto_en_aleman_resta(self):
        texto = ("Wir suchen dich für unser Team. Du arbeitest mit der Kotlin Plattform "
                 "und bist bei uns für die Weiterentwicklung und das Testing verantwortlich.")
        self.assertEqual(puntos(oferta(descripcion=texto)), 3 - 8)

    def test_penalizacion_de_idioma_se_aplica_una_vez(self):
        texto = "Deutsch C2. Wir suchen dich und du bist mit der Firma für die Arbeit und das Team da."
        self.assertEqual(puntos(oferta(descripcion=texto)), -8)

    def test_remoto_suma(self):
        self.assertEqual(puntos(oferta(remoto=True)), 2)


class TestEvaluarTodas(unittest.TestCase):
    def test_ordena_filtra_por_minimo_y_quita_descartadas(self):
        ofertas = [
            oferta("Python Dev", id_fuente="a"),           # 4
            oferta("Kotlin Spring Dev", id_fuente="b"),    # 12
            oferta("Developer", id_fuente="c"),            # 0 → bajo el mínimo
            oferta("Sales Kotlin", id_fuente="d"),         # descartada
        ]
        resultado = evaluar_todas(ofertas, perfil())
        self.assertEqual([e.oferta.id_fuente for e in resultado], ["b", "a"])

    def test_rechazadas_incluye_descartadas_y_bajo_el_minimo(self):
        ofertas = [oferta("Kotlin Spring Dev", id_fuente="ok"), oferta("Developer", id_fuente="bajo"),
                   oferta("Sales Kotlin", id_fuente="rol")]
        resultado = {e.oferta.id_fuente: e for e in rechazadas(ofertas, perfil())}
        self.assertEqual(set(resultado), {"bajo", "rol"})
        self.assertTrue(resultado["rol"].descartada)

    def test_minimo_personalizado(self):
        ofertas = [oferta("Python Dev", id_fuente="a"), oferta("Developer", id_fuente="c")]
        self.assertEqual(len(evaluar_todas(ofertas, perfil(), minimo=0)), 2)

    def test_quita_duplicados_por_fuente_e_id(self):
        # Títulos distintos para probar solo la clave (fuente, id); el duplicado por contenido tiene su propio test.
        ofertas = [oferta("Kotlin Dev", id_fuente="a"), oferta("Kotlin Dev", id_fuente="a"),
                   oferta("Kotlin Engineer", id_fuente="a", fuente="otra")]
        self.assertEqual(len(evaluar_todas(ofertas, perfil())), 2)


class TestAjustesConDatosReales(unittest.TestCase):
    """Problemas que aparecieron al ejecutar `filtrar` con las 918 ofertas reales."""

    def test_sinonimos_cuentan_una_sola_vez(self):
        p = perfil(tecnologias={"fullstack": 2}, alias={"fullstack": ["full stack", "full-stack"]})
        self.assertEqual(puntos(oferta(descripcion="full-stack and fullstack and full stack"), p), 2)
        ev = evaluar(oferta("Full Stack Developer"), p)
        self.assertEqual(ev.puntuacion, 4)
        self.assertIn("fullstack (título) +4", ev.motivos)

    def test_tope_de_puntos_por_tecnologias_en_la_descripcion(self):
        # Descripciones que listan 15 tecnologías no deben ganar a un título que encaja.
        p = perfil(puntos_tope_descripcion=4)
        self.assertEqual(puntos(oferta(descripcion="kotlin, java, python"), p), 4)  # 8 → tope 4
        self.assertEqual(puntos(oferta("Kotlin Dev", descripcion="java python"), p), 6 + 4)  # el título no tiene tope

    def test_tope_explica_el_recorte(self):
        ev = evaluar(oferta(descripcion="kotlin, java, python"), perfil(puntos_tope_descripcion=4))
        self.assertIn("tope tecnologías en descripción -4", ev.motivos)

    def test_misma_oferta_en_otro_dominio_es_duplicado(self):
        # Arbeitnow publica la misma oferta en .nl y .co.uk con distinto id.
        a = oferta("Kotlin Dev", id_fuente="x-nl")
        b = oferta("kotlin dev", id_fuente="x-uk")
        self.assertEqual(len(evaluar_todas([a, b], perfil())), 1)


class TestRevisionPaso2(unittest.TestCase):
    """Hallazgos de ecc:python-reviewer sobre el paso 2."""

    # Hallazgo 2: el idioma solo penaliza si se EXIGE, no si se niega o es opcional.
    def test_idioma_negado_u_opcional_no_resta(self):
        for frase in ("No German required.", "German is a plus.", "German is nice to have.",
                      "Working language is English, German not needed.", "We are a German company."):
            with self.subTest(frase=frase):
                self.assertEqual(puntos(oferta(descripcion=frase)), 0)

    def test_idioma_exigido_resta(self):
        for frase in ("Fluent German is required.", "Native German speaker.", "Deutsch C1.",
                      "Sehr gute Deutschkenntnisse."):
            with self.subTest(frase=frase):
                self.assertEqual(puntos(oferta(descripcion=frase), perfil(idiomas_excluyentes=["german", "deutsch", "deutschkenntnisse"])), -8)

    # Hallazgo 3: deduplicación más precisa y quedándose con la mejor copia.
    def test_sin_empresa_no_se_fusionan_titulos_genericos(self):
        a = Oferta("1", "test", "Kotlin Developer", "", "u1", "Berlin", "", "", "")
        b = Oferta("2", "test", "Kotlin Developer", "", "u2", "Paris", "", "", "")
        self.assertEqual(len(evaluar_todas([a, b], perfil())), 2)

    def test_misma_empresa_en_otra_ciudad_no_es_duplicado(self):
        a = Oferta("1", "test", "Kotlin Developer", "ACME", "u1", "Berlin", "", "", "")
        b = Oferta("2", "test", "Kotlin Developer", "ACME", "u2", "Paris", "", "", "")
        self.assertEqual(len(evaluar_todas([a, b], perfil())), 2)

    def test_entre_duplicados_se_queda_la_mejor(self):
        peor = oferta("Kotlin Dev", id_fuente="nl")
        mejor = oferta("Kotlin Dev", id_fuente="uk", remoto=True)
        resultado = evaluar_todas([peor, mejor], perfil())
        self.assertEqual([e.oferta.id_fuente for e in resultado], ["uk"])

    # Hallazgo 4: un "<" suelto no es una etiqueta HTML.
    def test_menor_que_no_borra_texto(self):
        texto = "<li>Requirements: <2 years of Java experience</li><li>Python</li>"
        self.assertIn("Java", limpiar_html(texto))
        self.assertEqual(puntos(oferta(descripcion=texto)), 3 + 2)

    # Hallazgo 5: variantes habituales que no se encontraban.
    def test_terminos_con_espacio_aceptan_saltos_de_linea(self):
        p = perfil(tecnologias={"rest api": 1})
        for texto in ("REST\nAPI", "REST  API", "rest\tapi"):
            with self.subTest(texto=texto):
                self.assertEqual(puntos(oferta(descripcion=texto), p), 1)

    def test_sufijo_numerico_de_version(self):
        self.assertEqual(puntos(oferta(descripcion="Python3 and C++17")), 2 + 1)

    def test_sufijo_de_letras_sigue_sin_coincidir(self):
        self.assertEqual(puntos(oferta(descripcion="JavaScript, Pythonic")), 0)


def perfil_cr(**cambios) -> Perfil:
    """Perfil con la regla de ubicación del usuario: vive en Costa Rica."""
    base = dict(pais=["costa rica", "heredia", "san josé"],
                remoto_aceptado=["worldwide", "anywhere", "latam", "americas"],
                reubicacion=["relocation", "visa sponsorship"],
                puntos_remoto_dudoso=-2)
    base.update(cambios)
    return perfil(**base)


class TestUbicacion(unittest.TestCase):
    """Fuera de Costa Rica: solo remoto abierto a CR, o presencial CON ayuda para mudarse."""

    def ev(self, **datos):
        return evaluar(oferta(**datos), perfil_cr())

    def assertDescartada(self, ev, motivo):
        self.assertTrue(ev.descartada, ev.motivos)
        self.assertTrue(any(motivo in m for m in ev.motivos), ev.motivos)

    def test_en_tu_pais_vale_cualquier_modalidad(self):
        for remoto in (False, True):
            with self.subTest(remoto=remoto):
                self.assertFalse(self.ev(ubicacion="Heredia, Costa Rica", remoto=remoto).descartada)

    def test_remoto_abierto_a_tu_region(self):
        for region in ("Worldwide", "LATAM", "Americas", "Anywhere in the world"):
            with self.subTest(region=region):
                ev = self.ev(ubicacion=region, remoto=True)
                self.assertFalse(ev.descartada, ev.motivos)

    def test_remoto_restringido_se_descarta(self):
        for region in ("USA", "Europe, USA, Canada, APAC", "North America", "UK"):
            with self.subTest(region=region):
                self.assertDescartada(self.ev(ubicacion=region, remoto=True), "restringido")

    def test_remoto_sin_region_clara_se_mantiene_con_aviso(self):
        for ubicacion in ("Remote (Zug)", "Remote", ""):
            with self.subTest(ubicacion=ubicacion):
                ev = self.ev(ubicacion=ubicacion, remoto=True)
                self.assertFalse(ev.descartada)
                self.assertIn("verifica si aceptan Costa Rica -2", ev.motivos)

    def test_presencial_fuera_sin_reubicacion_se_descarta(self):
        self.assertDescartada(self.ev(ubicacion="Berlin", descripcion="Great office in Berlin."), "sin reubicación")

    def test_presencial_fuera_con_reubicacion_se_mantiene(self):
        ev = self.ev(ubicacion="Paris", descripcion="<li>Relocation support for international mobilities</li>")
        self.assertFalse(ev.descartada)
        self.assertTrue(any("reubicación" in m for m in ev.motivos))

    def test_reubicacion_negada_no_cuenta(self):
        # Descubierto con Accurids: "work permit" aparecía como requisito, no como ayuda.
        for frase in ("We do not offer visa sponsorship.", "Unfortunately we cannot provide relocation."):
            with self.subTest(frase=frase):
                self.assertDescartada(self.ev(ubicacion="Berlin", descripcion=frase), "sin reubicación")

    def test_remoto_restringido_en_titulo_o_descripcion(self):
        # Descubierto con "Backend Developer - Remote from EU" y "remotely (within EU)".
        p = perfil_cr(restricciones_remoto=["remote from eu", "within eu", "us only"])
        casos = [oferta("Backend Developer - Remote from EU", remoto=True, ubicacion="Remote"),
                 oferta("Dev", descripcion="Work remotely (within EU).", remoto=True, ubicacion="Remote (Strasbourg)")]
        for o in casos:
            with self.subTest(titulo=o.titulo):
                ev = evaluar(o, p)
                self.assertTrue(ev.descartada, ev.motivos)
                self.assertTrue(any("restringido" in m for m in ev.motivos))

    def test_sin_pais_configurado_no_se_aplica(self):
        self.assertFalse(evaluar(oferta(ubicacion="Berlin"), perfil()).descartada)


class TestHtmlEscapado(unittest.TestCase):
    def test_html_que_viene_escapado_tambien_se_limpia(self):
        # Descubierto con la oferta real de Doctolib: Arbeitnow a veces manda "&lt;h2&gt;" en vez de "<h2>".
        texto = limpiar_html("&lt;h2&gt;&lt;strong&gt;What we do&lt;/strong&gt;&lt;/h2&gt;&lt;li&gt;Kotlin&lt;/li&gt;")
        self.assertEqual(texto.split(), ["What", "we", "do", "Kotlin"])

    def test_menor_que_literal_sigue_intacto(self):
        self.assertIn("<2 years", limpiar_html("&lt;2 years of Java"))


class TestCargarPerfil(unittest.TestCase):
    def escribir(self, contenido: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ruta = Path(tmp.name) / "perfil.toml"
        ruta.write_text(contenido, encoding="utf-8")
        return ruta

    TOML_VALIDO = """
puntuacion_minima = 4
nivel_bonus = ["Junior"]
nivel_penaliza = ["Senior"]
roles_excluidos = ["Sales"]
idiomas_excluyentes = ["German"]

[puntos]
nivel_bonus = 5
nivel_penaliza = -6
idioma_excluyente = -8
remoto = 2

[tecnologias]
Kotlin = 3
"c++" = 1
"""

    def test_carga_un_toml_valido_y_normaliza_a_minusculas(self):
        p = cargar_perfil(self.escribir(self.TOML_VALIDO))
        self.assertEqual(p.tecnologias, {"kotlin": 3, "c++": 1})
        self.assertEqual(p.nivel_bonus, ["junior"])
        self.assertEqual((p.puntos_remoto, p.puntuacion_minima), (2, 4))

    def test_tecnologia_con_alias_como_tabla_en_linea(self):
        toml = self.TOML_VALIDO.replace("Kotlin = 3", 'FullStack = { peso = 2, alias = ["Full Stack", "full-stack"] }')
        p = cargar_perfil(self.escribir(toml + "\n"))
        self.assertEqual(p.tecnologias["fullstack"], 2)
        self.assertEqual(p.alias["fullstack"], ["full stack", "full-stack"])

    def test_seccion_ubicacion_opcional_y_validada(self):
        self.assertEqual(cargar_perfil(self.escribir(self.TOML_VALIDO)).pais, [])
        con_ubicacion = self.TOML_VALIDO + '\n[ubicacion]\npais = ["Costa Rica"]\nremoto_aceptado = ["LATAM"]\nreubicacion = ["relocation"]\n'
        p = cargar_perfil(self.escribir(con_ubicacion))
        self.assertEqual((p.pais, p.remoto_aceptado, p.reubicacion), (["costa rica"], ["latam"], ["relocation"]))
        for malo in (con_ubicacion.replace("reubicacion", "reubicasion"),
                     con_ubicacion.replace("remoto = 2", "remoto = 2\nremoto_dudoso = 3")):
            with self.subTest(malo=malo[-60:]), self.assertRaises(PerfilError):
                cargar_perfil(self.escribir(malo))

    def test_tope_descripcion_es_opcional(self):
        self.assertIsNone(cargar_perfil(self.escribir(self.TOML_VALIDO)).puntos_tope_descripcion)
        toml = self.TOML_VALIDO.replace("remoto = 2", "remoto = 2\ntope_descripcion = 8")
        self.assertEqual(cargar_perfil(self.escribir(toml)).puntos_tope_descripcion, 8)

    def test_errores_de_escritura_en_el_perfil(self):
        # Revisión del paso 2, hallazgos 1 y 6: typos que antes se aceptaban en silencio.
        casos = {
            "término vacío en tecnologías": self.TOML_VALIDO.replace('"c++" = 1', '"c++" = 1\n"" = 1'),
            "término en blanco en lista": self.TOML_VALIDO.replace('["Sales"]', '["Sales", " "]'),
            "alias vacío": self.TOML_VALIDO.replace("Kotlin = 3", 'Kotlin = { peso = 3, alias = [""] }'),
            "clave desconocida arriba": "desconocida = 1\n" + self.TOML_VALIDO,
            "clave desconocida en puntos": self.TOML_VALIDO.replace("remoto = 2", "remoto = 2\nremot = 1"),
            "clave desconocida en tecnología": self.TOML_VALIDO.replace("Kotlin = 3", 'Kotlin = { peso = 3, aliases = ["kt"] }'),
            "penalización positiva": self.TOML_VALIDO.replace("nivel_penaliza = -6", "nivel_penaliza = 6"),
            "idioma positivo": self.TOML_VALIDO.replace("idioma_excluyente = -8", "idioma_excluyente = 8"),
            "bonus negativo": self.TOML_VALIDO.replace("nivel_bonus = 5", "nivel_bonus = -5"),
            "tope negativo": self.TOML_VALIDO.replace("remoto = 2", "remoto = 2\ntope_descripcion = -1"),
            "duplicado por mayúsculas": self.TOML_VALIDO.replace("Kotlin = 3", "Kotlin = 3\nkotlin = 2"),
            "sin tecnologías": self.TOML_VALIDO.replace('Kotlin = 3\n"c++" = 1', ""),
        }
        for nombre, contenido in casos.items():
            with self.subTest(caso=nombre), self.assertRaises(PerfilError):
                cargar_perfil(self.escribir(contenido))

    def test_el_perfil_real_del_proyecto_es_valido(self):
        p = cargar_perfil(RAIZ / "config" / "perfil.toml")
        for tecnologia in ("kotlin", "java", "spring", "python", "react"):
            self.assertIn(tecnologia, p.tecnologias)

    def test_archivo_inexistente(self):
        with self.assertRaises(PerfilError):
            cargar_perfil(Path("no-existe.toml"))

    def test_toml_mal_escrito(self):
        with self.assertRaises(PerfilError):
            cargar_perfil(self.escribir("esto no es = = toml"))

    def test_falta_una_clave(self):
        with self.assertRaisesRegex(PerfilError, "puntuacion_minima"):
            cargar_perfil(self.escribir(self.TOML_VALIDO.replace("puntuacion_minima = 4", "")))

    def test_tipo_incorrecto(self):
        with self.assertRaisesRegex(PerfilError, "kotlin"):
            cargar_perfil(self.escribir(self.TOML_VALIDO.replace("Kotlin = 3", 'Kotlin = "tres"')))


if __name__ == "__main__":
    unittest.main()
