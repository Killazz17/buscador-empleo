# TDD: paso 5, preparar borradores con IA (patrón Strategy)

- **Fecha:** 2026-10-23
- **Skills:** `ecc:tdd-workflow` y `claude-api` (documentación del SDK leída antes de escribir código; se comprobó el SDK instalado, anthropic 2.2.0)
- **Origen:** el usuario pidió que el diseño fuera escalable para poder cambiar de IA (patrón Strategy) y eligió la "API de Claude" como primer proveedor real. Las historias de usuario se escribieron en esta sesión.
- **Git:** el proyecto no está bajo git, así que no hay commits de checkpoint. Este informe es la evidencia.

## User journeys
1. Como candidato, quiero `preparar 12` y obtener un borrador basado en mi CV real, sin datos inventados.
2. Como candidato, quiero revisar y aprobar yo mismo cada carta, y que nadie (ni la IA) pueda aprobar por mí.
3. Como candidato, quiero cambiar de IA editando una línea de configuración.
4. Como candidato, quiero saber qué IA y qué versión del prompt escribió cada carta, y cuántos tokens costó.

## Decisiones
- **Strategy:** `ProveedorIA.generar(instrucciones, mensaje) → RespuestaIA`. Proveedores `claude` y `simulado` registrados en `PROVEEDORES` y elegidos en `config/ia.toml`. El prompt vive en `prompts/carta.md`, fuera del proveedor.
- **Dependencia opcional:** `anthropic` solo se importa al crear `ClaudeAPI`. El resto de la CLI funciona sin ella.
- **Claude Opus 5.5**, con esfuerzo `medium` configurable, `max_tokens` holgado y **fallback del servidor** (`fallbacks="default"`). Se guarda el modelo que **respondió**.
- **Esquema v3:** tablas `preparaciones` (versionadas) y `aprobaciones` (con la huella SHA-256 del texto aprobado). Los triggers exigen un borrador para `preparada`, la aprobación de la **última** versión para `aprobada` y una aprobación para `enviada`. Ambas tablas son solo de añadir.
- **`cambiar_estado()`** ya no lleva a `preparada`, `aprobada` ni `enviada`: hay métodos dedicados que registran los datos.
- **Confirmación humana:** `aprobar` y `enviada` exigen una terminal interactiva (`isatty`) y un "si" escrito. Un proceso sin terminal (un cron, un script o una IA) no puede aprobar.
- **Inyección de prompt:** la oferta va en `<oferta>` con `<` y `>` neutralizados, y las instrucciones dicen que es un dato, no una orden.
- **Privacidad:** `config/cv.md` no incluye teléfono ni correo y está en `.gitignore`.

## Ciclos

| Fase | Resultado |
|---|---|
| RED | `Ran 154 tests — FAILED (failures=4, errors=39)`: `No module named 'buscador.ia'`, `'Repositorio' object has no attribute 'guardar_preparacion'`, `no such table: preparaciones`, `cli` sin `RUTA_CONFIG_IA`, y `cambiar_estado` todavía aceptaba `preparada`/`aprobada`/`enviada`. |
| GREEN parcial | Tests de repositorio, preparación e IA: `Ran 66 — FAILED (errors=1)`: faltaba `prompts/carta.md`. |
| GREEN | `Ran 173 tests — OK` |
| De extremo a extremo (copia de la BD real en el scratchpad, proveedor `simulado`) | Migración **v2 → v3 con 61 ofertas**. `preparar 2` creó el borrador v1 y el estado pasó a `preparada`. **`aprobar 2` lanzado desde Claude Code (sin terminal) → "No se aprobó nada", código 1.** `ver 2` mostró el borrador y el historial. |
| **Bug encontrado con el SDK real** | Sin credenciales, anthropic 2.2.0 lanza `TypeError("Could not resolve authentication method…")`, no `AuthenticationError`, y se escapaba sin capturar. RED: `test_sin_credenciales_el_sdk_2_lanza_typeerror_y_se_traduce` → `TypeError`. GREEN: se traduce **solo** ese `TypeError` a `IAError`; cualquier otro se deja ver (`test_otros_typeerror_no_se_ocultan`). Comprobado con el SDK real: "No hay credenciales de Anthropic: crea una API key…". |
| GREEN final | `Ran 175 tests — OK` |

## Ciclo 2: revisión de `ecc:security-reviewer`
La revisión encontró 0 hallazgos críticos, 1 ALTO, 3 MEDIOS y 2 BAJOS. El usuario aprobó corregirlos todos y añadir reglas `deny`.

| Fase | Resultado |
|---|---|
| RED | `Ran 176 tests — FAILED (failures=11, errors=21)`: faltaba `sin_controles`; `confirmado_por_persona` no se aceptaba (×18); no existía `verificar_aprobacion`; `\x1b` se imprimía tal cual; la manipulación no se detectaba; 7 rutas personales no estaban en `.gitignore`; la versión no estaba fijada. |
| GREEN | `Ran 190 tests — OK` |
| Reglas `deny` | Se creó `.claude/settings.json`. Comprobado: un `echo "… buscador aprobar 1"` inofensivo fue **rechazado por los permisos**. |

| Hallazgo | Corrección | Tests |
|---|---|---|
| ALTO: la confirmación no resiste a un proceso con el mismo usuario | `confirmado_por_persona` obligatorio (keyword-only) en `aprobar` y `marcar_enviada`, reglas `deny` y documentación honesta de los límites en `CLAUDE.md` | `test_aprobar_y_enviar_exigen_confirmado_por_persona` |
| MEDIO: inyección de prompt que se cuela en la salida y descripción sin tope | Regla 8 en `carta.md` (sin enlaces, correos, teléfonos ni datos personales), `MAX_DESCRIPCION = 8000` y `alertas_borrador()` (enlaces, correos, números de 7 o más dígitos, cifras que no están en el CV) mostradas al aprobar | `test_descripcion_enorme_se_recorta`, `test_la_plantilla_prohibe_*`, `test_alertas_*`, `test_aprobar_avisa_*` |
| MEDIO: secuencias de escape de terminal | `sin_controles()` aplicado al normalizar (`texto()`, `desde_dict`), a la salida de la IA y a todo lo que imprime la CLI | `test_sin_controles_*`, `test_las_ofertas_se_sanean_*`, `test_la_respuesta_de_la_ia_se_sanea_*`, `test_ver_y_aprobar_no_imprimen_*` |
| MEDIO: la huella no se verificaba | `verificar_aprobacion()`; `ver` avisa y `enviada` (CLI y repositorio) se niega si no coincide | `test_verificar_aprobacion_detecta_texto_manipulado`, `test_enviada_se_niega_si_el_texto_aprobado_fue_manipulado` |
| BAJO: `.gitignore` incompleto | `*.db`, `*.sqlite*`, `.env.*`, `cv.*`, `cv-*`, `config/cv*`, `.coverage` y `htmlcov/` | `test_proyecto.py::TestGitignore` |
| BAJO: rango de versión abierto | `anthropic==2.2.0` | `test_proyecto.py::test_versiones_fijadas` |

**No aplicado (decisión explicada):** prohibir a Claude Code editar `cli.py` y `almacenamiento.py` (sugerido por el revisor). Impediría seguir desarrollando el proyecto. La protección relevante (no ejecutar `aprobar`, `enviada` ni `sqlite3`, y no editar `data/`) sí está aplicada.

## Qué garantizan los tests

| # | Garantía | Test |
|---|---|---|
| 1 | Todos los proveedores cumplen la interfaz y se eligen por configuración | `test_ia.py::TestRegistro`, `test_crear_proveedor_segun_config` |
| 2 | La petición a Claude lleva el modelo, el sistema, el mensaje, el esfuerzo y el fallback por defecto | `test_construye_la_peticion_esperada`, `test_modelo_y_esfuerzo_configurables` |
| 3 | Se guarda el modelo que respondió y los tokens; solo se juntan los bloques de texto | `test_junta_el_texto_e_informa_modelo_real_y_tokens` |
| 4 | Un rechazo, una respuesta cortada o una respuesta vacía dan `IAError` y no se guardan | `test_rechazo_respuesta_cortada_o_vacia_son_errores`, `test_preparacion.py::test_si_la_ia_falla_no_se_guarda_nada` |
| 5 | Los errores del SDK (401, 403, 429, 5xx, red y falta de credenciales) dan un mensaje útil | `test_errores_del_sdk_*`, `test_sin_credenciales_*` |
| 6 | Sin el SDK instalado, el error explica cómo instalarlo | `test_sin_el_sdk_instalado_*` |
| 7 | `config/ia.toml` se valida (proveedor, claves y esfuerzo), y el archivo real es válido | `TestConfigIA` |
| 8 | El prompt incluye el CV y la oferta limpia, y **la oferta no puede romper las etiquetas** | `TestConstruirPrompt` |
| 9 | La huella identifica la versión de la plantilla | `test_huella_identifica_la_version_de_la_plantilla` |
| 10 | No se llama (ni se paga) a la IA si la oferta no se puede preparar | `test_no_llama_a_la_ia_si_la_oferta_no_se_puede_preparar` |
| 11 | Los borradores se versionan, y aprobar registra la versión y la huella de su texto | `TestPreparacionesYAprobaciones` |
| 12 | La BD exige un borrador para `preparada` y la aprobación de la **última** versión para `aprobada`; preparaciones y aprobaciones son solo de añadir | `test_la_bd_exige_*`, `test_solo_se_aprueba_la_ultima_version`, `test_*_solo_de_anadir` |
| 13 | El recorrido exhaustivo de transiciones sigue coincidiendo con `TRANSICIONES` (con los requisitos de v3) | `test_la_bd_aplica_exactamente_la_tabla_de_transiciones` |
| 14 | **Sin una terminal interactiva no se aprueba ni se marca como enviada**, y responder algo distinto de "si" no aprueba | `test_cli.py::test_aprobar_sin_terminal_*`, `test_responder_otra_cosa_no_aprueba`, `test_marcar_enviada_*` |
| 15 | Al aprobar se muestra exactamente la carta que se aprueba | `test_aprobar_muestra_la_carta_y_pide_confirmacion` |
| 16 | Las migraciones v2 → v3 funcionan sobre una BD existente | `test_una_bd_v2_existente_se_migra_a_v3` |

## Cobertura
**89 %** en total. `ia/base.py`, `ia/simulado.py` y `preparacion.py` están al 100 %, `ia/claude.py` al 96 % y `almacenamiento.py` al 96 %.

## Huecos conocidos y siguiente paso
- **Todavía no se ha hecho ninguna llamada real a la API**: no hay API key configurada. La primera llamada real (unos $0,04) requiere la key del usuario y su aprobación del gasto.
- `config/ia.toml` usa `proveedor = "claude"`. Hasta que haya una key, `preparar` da un error claro. Para probar el flujo sin coste, se puede cambiar a `"simulado"`.
- La confirmación por `isatty` es una **barrera razonable, no una garantía absoluta**: alguien que quiera saltársela a propósito (por ejemplo con un pseudo-terminal) puede hacerlo. Está pensada para impedir aprobaciones accidentales o automáticas, no para resistir un ataque deliberado del propio usuario.
