# TDD: botones «Descargar» y «Filtrar» en la web

- **Fecha:** 2026-10-26
- **Skills:** `ecc:tdd-workflow`
- **Origen:** idea pendiente de la sesión anterior ("añadir botones de Descargar y Filtrar en la web, para no tener que usar la terminal"). El usuario preguntó por ella y pidió hacerla.
- **Rama:** `feat/web-botones-pipeline`, apilada sobre `feat/interfaz-web` (PR #1).

## User journeys
1. Como candidato, quiero pulsar «Descargar ofertas» y ver qué trajo cada fuente, sin abrir PowerShell.
2. Como candidato, quiero pulsar «Filtrar y puntuar» y que las ofertas nuevas aparezcan en mi lista.
3. Como candidato, quiero que estos botones **nunca** preparen, aprueben ni envíen nada.

## Decisiones
- **Reutilizar la CLI, no copiarla:** `ejecutar_en_cli(accion)` llama a `cli.comando_descargar` / `cli.comando_filtrar` y captura su salida (stdout y stderr). Así la web hereda el límite diario de las APIs, el "ya descargaste hoy" y la lectura del perfil. Alternativa descartada: extraer la lógica a un módulo `pipeline.py` que devuelva datos (mejor arquitectura, pero toca la CLI y sus tests; queda como mejora futura).
- **Una acción a la vez (`threading.Lock`, no bloqueante):** redirigir stdout es global al proceso; dos clics simultáneos mezclarían salidas. Si hay una en curso responde 409 con un mensaje claro.
- **Inyección de dependencias:** `crear_app(..., ejecutar=...)`. Los tests usan un falso, sin red ni disco real; el valor por defecto es `ejecutar_en_cli`.
- **Lista cerrada `ACCIONES`:** solo `descargar` y `filtrar`. Cualquier otra ruta (`/pipeline/preparar`, `/pipeline/aprobar`) da 404, con test.
- **Seguridad:** POST con token CSRF y comprobación de `Origin` (ya globales). La salida incluye texto de terceros (títulos de ofertas): `sin_controles` + autoescape de Jinja, con test de `<script>`.
- **Sin `--force`:** el botón respeta "ya descargaste hoy"; repetir la descarga sigue siendo cosa de la terminal.
- **Estado "ocupado":** un script con nonce desactiva el botón al enviar (diferido con `setTimeout`, porque un botón desactivado durante el evento `submit` no envía el formulario).

## Ciclos

| Fase | Resultado |
|---|---|
| RED | `Ran 41 tests — FAILED (errors=10)`: `crear_app()` no aceptaba `ejecutar` y no existía `ejecutar_en_cli`. |
| GREEN | `Ran 267 tests — OK` |

## Pendiente
- No se probó con descargas reales desde la web (consumiría peticiones del límite diario y escribe en `data/`). Primera prueba real: pulsar los botones en tu navegador.
- Posible mejora: `pipeline.py` que devuelva datos estructurados en lugar de texto capturado.
