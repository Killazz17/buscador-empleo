"""Tests de los proveedores de IA (patrón Strategy), SIN red ni coste.

El proveedor de Claude recibe un SDK y un cliente FALSOS: así se prueba qué petición
construye y cómo interpreta cada respuesta o error, sin gastar un céntimo.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from buscador.ia import PROVEEDORES, ConfigIA, cargar_config_ia, crear_proveedor
from buscador.ia.base import IAError, ProveedorIA, RespuestaIA
from buscador.ia.claude import ClaudeAPI
from buscador.ia.manual import Manual
from buscador.ia.simulado import Simulado

RAIZ = Path(__file__).resolve().parents[1]


# --- SDK falso: misma forma que el de Anthropic, sin red -------------------------------------
class FalsoAPIError(Exception):
    pass


class FalsoAPIStatusError(FalsoAPIError):
    def __init__(self, mensaje="error", status_code=500):
        super().__init__(mensaje)
        self.status_code = status_code


class FalsoAuthenticationError(FalsoAPIStatusError):
    pass


class FalsoPermissionDeniedError(FalsoAPIStatusError):
    pass


class FalsoRateLimitError(FalsoAPIStatusError):
    pass


class FalsoAPIConnectionError(FalsoAPIError):
    pass


def respuesta(*textos, stop_reason="end_turn", modelo="claude-opus-5-5", entrada=1000, salida=300):
    bloques = [SimpleNamespace(type="thinking", thinking="")]
    bloques += [SimpleNamespace(type="text", text=t) for t in textos]
    return SimpleNamespace(content=bloques, stop_reason=stop_reason, model=modelo,
                           usage=SimpleNamespace(input_tokens=entrada, output_tokens=salida))


class FalsoMessages:
    def __init__(self, resultado):
        self.resultado = resultado
        self.llamadas = []

    def create(self, **kwargs):
        self.llamadas.append(kwargs)
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


def sdk_falso(resultado=None):
    messages = FalsoMessages(resultado if resultado is not None else respuesta("Hola"))
    cliente = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    sdk = SimpleNamespace(
        Anthropic=lambda: cliente,
        APIError=FalsoAPIError, APIStatusError=FalsoAPIStatusError, APIConnectionError=FalsoAPIConnectionError,
        AuthenticationError=FalsoAuthenticationError, PermissionDeniedError=FalsoPermissionDeniedError,
        RateLimitError=FalsoRateLimitError,
    )
    return sdk, messages


class TestRegistro(unittest.TestCase):
    def test_proveedores_cumplen_la_interfaz(self):
        self.assertIn("claude", PROVEEDORES)
        self.assertIn("simulado", PROVEEDORES)
        for nombre, clase in PROVEEDORES.items():
            self.assertTrue(issubclass(clase, ProveedorIA))
            self.assertEqual(clase.nombre, nombre)


class TestSimulado(unittest.TestCase):
    def test_respuesta_determinista_y_registra_llamadas(self):
        ia = Simulado()
        r1 = ia.generar("instrucciones", "mensaje")
        r2 = ia.generar("instrucciones", "mensaje")
        self.assertIsInstance(r1, RespuestaIA)
        self.assertEqual(r1, r2)
        self.assertIn("Carta simulada", r1.texto)
        self.assertEqual(len(ia.llamadas), 2)

    def test_puede_simular_un_fallo(self):
        with self.assertRaises(IAError):
            Simulado(falla=True).generar("i", "m")


class TestClaudeAPI(unittest.TestCase):
    def test_construye_la_peticion_esperada(self):
        sdk, messages = sdk_falso()
        ClaudeAPI(sdk=sdk).generar("INSTRUCCIONES", "MENSAJE")
        [peticion] = messages.llamadas
        self.assertEqual(peticion["model"], "claude-opus-5-5")
        self.assertEqual(peticion["system"], "INSTRUCCIONES")
        self.assertEqual(peticion["messages"], [{"role": "user", "content": "MENSAJE"}])
        self.assertEqual(peticion["output_config"], {"effort": "medium"})
        self.assertGreaterEqual(peticion["max_tokens"], 4000)
        # Fallback del servidor activado por defecto (rechazos por falso positivo de seguridad).
        self.assertEqual(peticion["fallbacks"], "default")
        self.assertEqual(peticion["betas"], ["server-side-fallback-2026-07-01"])

    def test_modelo_y_esfuerzo_configurables(self):
        sdk, messages = sdk_falso()
        ClaudeAPI(modelo="claude-sonnet-5-5", esfuerzo="high", sdk=sdk).generar("i", "m")
        self.assertEqual((messages.llamadas[0]["model"], messages.llamadas[0]["output_config"]),
                         ("claude-sonnet-5-5", {"effort": "high"}))

    def test_junta_el_texto_e_informa_modelo_real_y_tokens(self):
        sdk, _ = sdk_falso(respuesta("Estimado equipo,", " me postulo.", modelo="claude-opus-5", entrada=4200, salida=650))
        r = ClaudeAPI(sdk=sdk).generar("i", "m")
        # El modelo es el que RESPONDIÓ (puede ser el de fallback), no el que pedimos.
        self.assertEqual(r, RespuestaIA("Estimado equipo, me postulo.", "claude-opus-5", 4200, 650))

    def test_rechazo_respuesta_cortada_o_vacia_son_errores(self):
        for caso in (respuesta("", stop_reason="refusal"), respuesta("a medias", stop_reason="max_tokens"),
                     respuesta()):
            with self.subTest(stop_reason=caso.stop_reason, textos=len(caso.content)):
                sdk, _ = sdk_falso(caso)
                with self.assertRaises(IAError):
                    ClaudeAPI(sdk=sdk).generar("i", "m")

    def test_errores_del_sdk_se_traducen_con_mensajes_utiles(self):
        casos = {
            FalsoAuthenticationError("bad key", 401): "API key",
            FalsoPermissionDeniedError("no", 403): "permiso",
            FalsoRateLimitError("slow down", 429): "límite",
            FalsoAPIStatusError("boom", 500): "500",
            FalsoAPIConnectionError("offline"): "conectar",
        }
        for error, pista in casos.items():
            with self.subTest(error=type(error).__name__):
                sdk, _ = sdk_falso(error)
                with self.assertRaisesRegex(IAError, pista):
                    ClaudeAPI(sdk=sdk).generar("i", "m")

    def test_sin_credenciales_el_sdk_2_lanza_typeerror_y_se_traduce(self):
        # Descubierto probando con el SDK real (2.2.0): sin API key no lanza AuthenticationError,
        # sino TypeError("Could not resolve authentication method…") al hacer la petición.
        sdk, _ = sdk_falso(TypeError("Could not resolve authentication method. Expected one of api_key, ..."))
        with self.assertRaisesRegex(IAError, "ANTHROPIC_API_KEY"):
            ClaudeAPI(sdk=sdk).generar("i", "m")

    def test_otros_typeerror_no_se_ocultan(self):
        # Un TypeError que no es de credenciales es un bug nuestro: debe verse, no disfrazarse.
        sdk, _ = sdk_falso(TypeError("create() got an unexpected keyword argument 'x'"))
        with self.assertRaises(TypeError):
            ClaudeAPI(sdk=sdk).generar("i", "m")

    def test_sin_el_sdk_instalado_da_un_error_que_explica_como_instalarlo(self):
        with patch.dict(sys.modules, {"anthropic": None}):
            with self.assertRaisesRegex(IAError, "pip install"):
                ClaudeAPI()


class TestManual(unittest.TestCase):
    """Proveedor gratuito: la "IA" eres tú copiando el prompt a claude.ai/Gemini y pegando la respuesta."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.carpeta = Path(tmp.name) / "manual"
        self.copiado: list[str] = []
        silencio = patch("sys.stdout")  # las instrucciones para la persona no deben ensuciar la salida de los tests
        silencio.start()
        self.addCleanup(silencio.stop)

    def manual(self, pegar: str | None = "Estimado equipo, me postulo.", teclear: str = "", interactivo=True):
        def entrada(_pregunta):
            # Simula a la persona: pega la respuesta de la web en el archivo y pulsa Enter.
            if pegar is not None:
                [respuesta] = self.carpeta.glob("*-respuesta.md")
                respuesta.write_text(pegar, encoding="utf-8")
            return teclear
        return Manual(modelo="claude.ai", carpeta=self.carpeta, entrada=entrada,
                      es_interactivo=lambda: interactivo, copiar=self.copiado.append)

    def test_flujo_completo_y_prompt_autocontenido(self):
        r = self.manual().generar("INSTRUCCIONES DE LA CARTA", "<cv>CV</cv> <oferta>OFERTA</oferta>")
        self.assertEqual(r, RespuestaIA("Estimado equipo, me postulo.", "claude.ai", 0, 0))
        [prompt] = self.carpeta.glob("*-prompt.md")
        contenido = prompt.read_text(encoding="utf-8")
        # La web no tiene "system prompt": instrucciones y datos van juntos en un solo texto.
        for parte in ("INSTRUCCIONES DE LA CARTA", "<cv>CV</cv>", "<oferta>OFERTA</oferta>"):
            self.assertIn(parte, contenido)
        self.assertEqual(self.copiado, [contenido])  # también al portapapeles, para pegar directo

    def test_normaliza_saltos_de_linea_y_bom_al_pegar(self):
        # Descubierto con la primera carta real: al pegar en el Bloc de notas, cada salto salía doble.
        ia = self.manual(pegar=None)

        def pegar_como_windows(_pregunta):
            [respuesta] = self.carpeta.glob("*-respuesta.md")
            respuesta.write_bytes("﻿Dear X,\r\r\n\r\r\nPárrafo.\r\r\n\r\r\n\r\r\nSincerely,\r\r\nSeb".encode("utf-8"))
            return ""
        ia._entrada = pegar_como_windows
        self.assertEqual(ia.generar("i", "m").texto, "Dear X,\n\nPárrafo.\n\nSincerely,\nSeb")

    def test_respuesta_vacia_es_error(self):
        with self.assertRaisesRegex(IAError, "vacía"):
            self.manual(pegar="   ").generar("i", "m")

    def test_cancelar(self):
        with self.assertRaisesRegex(IAError, "cancel"):
            self.manual(pegar=None, teclear="cancelar").generar("i", "m")

    def test_sin_terminal_interactiva_no_puede_funcionar(self):
        with self.assertRaisesRegex(IAError, "terminal"):
            self.manual(interactivo=False).generar("i", "m")

    def test_si_falla_el_portapapeles_sigue_funcionando(self):
        def portapapeles_roto(_texto):
            raise OSError("sin portapapeles")
        ia = self.manual()
        ia._copiar = portapapeles_roto
        self.assertEqual(ia.generar("i", "m").texto, "Estimado equipo, me postulo.")

    def test_esta_registrado_y_se_crea_desde_la_config(self):
        self.assertIn("manual", PROVEEDORES)
        self.assertIsInstance(crear_proveedor(ConfigIA("manual", {"modelo": "gemini"})), Manual)


class TestConfigIA(unittest.TestCase):
    def escribir(self, contenido: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ruta = Path(tmp.name) / "ia.toml"
        ruta.write_text(contenido, encoding="utf-8")
        return ruta

    def test_config_valida(self):
        config = cargar_config_ia(self.escribir('proveedor = "claude"\nmodelo = "claude-opus-5-5"\nesfuerzo = "medium"\n'))
        self.assertEqual(config, ConfigIA("claude", {"modelo": "claude-opus-5-5", "esfuerzo": "medium"}))

    def test_la_config_real_del_proyecto_es_valida(self):
        self.assertIn(cargar_config_ia(RAIZ / "config" / "ia.toml").proveedor, PROVEEDORES)

    def test_errores_de_configuracion(self):
        casos = {
            "proveedor desconocido": 'proveedor = "skynet"\n',
            "clave desconocida": 'proveedor = "claude"\nmodel = "x"\n',
            "esfuerzo inválido": 'proveedor = "claude"\nesfuerzo = "turbo"\n',
            "falta proveedor": 'modelo = "x"\n',
            "TOML roto": "proveedor = = =",
        }
        for nombre, contenido in casos.items():
            with self.subTest(caso=nombre), self.assertRaises(IAError):
                cargar_config_ia(self.escribir(contenido))
        with self.assertRaises(IAError):
            cargar_config_ia(Path("no-existe.toml"))

    def test_crear_proveedor_segun_config(self):
        self.assertIsInstance(crear_proveedor(ConfigIA("simulado", {})), Simulado)
        sdk, _ = sdk_falso()
        with patch.dict(sys.modules, {"anthropic": sdk}):
            claude = crear_proveedor(ConfigIA("claude", {"modelo": "claude-sonnet-5-5"}))
        self.assertIsInstance(claude, ClaudeAPI)
        self.assertEqual(claude.modelo, "claude-sonnet-5-5")


if __name__ == "__main__":
    unittest.main()
