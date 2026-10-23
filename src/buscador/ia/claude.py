"""Estrategia: Claude mediante la API de Anthropic.

El SDK `anthropic` se importa SOLO al crear este proveedor: el resto del programa
(descargar, filtrar, listar…) funciona aunque no esté instalado.
"""

import importlib

from buscador.ia.base import IAError, ProveedorIA, RespuestaIA

MODELO_POR_DEFECTO = "claude-opus-5-5"
ESFUERZO_POR_DEFECTO = "medium"
MAX_TOKENS = 16000  # holgado: una carta son ~800 tokens, pero cortarla a medias obliga a repetir (y pagar)
# Si el modelo rechaza la petición por un falso positivo de seguridad, el servidor la reintenta
# con otro modelo dentro de la misma llamada. Guardamos qué modelo respondió de verdad.
BETA_FALLBACK = "server-side-fallback-2026-07-01"


class ClaudeAPI(ProveedorIA):
    nombre = "claude"

    def __init__(self, modelo: str = MODELO_POR_DEFECTO, esfuerzo: str = ESFUERZO_POR_DEFECTO, sdk=None):
        # `sdk` se puede inyectar (los tests pasan uno falso); si no, se importa el real.
        if sdk is None:
            try:
                sdk = importlib.import_module("anthropic")
            except ImportError as e:
                raise IAError("Falta el SDK de Anthropic. Instálalo con: "
                              ".venv/Scripts/python -m pip install -r requirements.txt") from e
        self._sdk = sdk
        try:
            # Sin argumentos: la API key se lee de ANTHROPIC_API_KEY. Nunca se escribe en el código.
            self._cliente = sdk.Anthropic()
        except getattr(sdk, "AnthropicError", Exception) as e:
            raise IAError(f"No se pudo crear el cliente de Anthropic (¿falta ANTHROPIC_API_KEY?): {e}") from e
        self.modelo = modelo
        self.esfuerzo = esfuerzo

    def generar(self, instrucciones: str, mensaje: str) -> RespuestaIA:
        sdk = self._sdk
        try:
            respuesta = self._cliente.beta.messages.create(
                model=self.modelo,
                max_tokens=MAX_TOKENS,
                system=instrucciones,
                messages=[{"role": "user", "content": mensaje}],
                output_config={"effort": self.esfuerzo},
                betas=[BETA_FALLBACK],
                fallbacks="default",
            )
        # De lo más concreto a lo más general: AuthenticationError es un tipo de APIStatusError.
        except sdk.AuthenticationError as e:
            raise IAError("API key inválida o ausente: crea una en console.anthropic.com "
                          "y defínela en la variable de entorno ANTHROPIC_API_KEY") from e
        except sdk.PermissionDeniedError as e:
            raise IAError(f"Tu API key no tiene permiso para usar el modelo {self.modelo}") from e
        except sdk.RateLimitError as e:
            raise IAError("Se alcanzó el límite de peticiones de la API; espera un momento y reintenta") from e
        except sdk.APIStatusError as e:
            raise IAError(f"La API respondió con error {getattr(e, 'status_code', '?')}: {e}") from e
        except sdk.APIConnectionError as e:
            raise IAError(f"No se pudo conectar con la API de Anthropic: {e}") from e
        except TypeError as e:
            # SDK 2.x sin credenciales: TypeError("Could not resolve authentication method…").
            # Solo traducimos ESE caso; cualquier otro TypeError es un bug y debe verse tal cual.
            if "authentication" not in str(e).lower():
                raise
            raise IAError("No hay credenciales de Anthropic: crea una API key en console.anthropic.com "
                          "y defínela en la variable de entorno ANTHROPIC_API_KEY") from e

        # Antes de leer el contenido, por qué terminó.
        if respuesta.stop_reason == "refusal":
            raise IAError("El modelo rechazó generar este borrador (stop_reason=refusal)")
        if respuesta.stop_reason == "max_tokens":
            raise IAError("La respuesta salió cortada (max_tokens); no se guarda un borrador incompleto")
        # La respuesta trae varios bloques (razonamiento, texto…): solo queremos los de texto.
        texto = "".join(b.text for b in respuesta.content if b.type == "text").strip()
        if not texto:
            raise IAError("La IA devolvió una respuesta sin texto")
        return RespuestaIA(texto, respuesta.model, respuesta.usage.input_tokens, respuesta.usage.output_tokens)
