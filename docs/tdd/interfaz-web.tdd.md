# TDD: cierre de la rama `feat/interfaz-web` (revisión y endurecimiento)

- **Fecha:** 2026-10-26
- **Skills / agentes:** `ecc:tdd-workflow`, `ecc:python-reviewer` y `ecc:security-reviewer` (en paralelo, solo lectura)
- **Origen:** el usuario pidió cerrar la rama web. Las dos revisiones se lanzaron sobre `src/buscador/web/` antes de abrir el PR.
- **Alcance:** la interfaz web ya existía (commits `cafe108` y `88bb922`); este informe cubre la revisión y los arreglos.

## Resultado de las revisiones
- `security-reviewer`: 0 críticos, 0 altos, 3 medios, 3 bajos. Verificó CSRF, CSP con nonce, TrustedHost, autoescape, `url_segura`, lista cerrada `AVISOS`, bind a 127.0.0.1 y la máquina de estados en `/aprobar` y `/enviada`.
- `python-reviewer`: 1 alto, 7 medios, 5 bajos. El alto bloqueaba el PR.

## Ciclos

| Fase | Resultado |
|---|---|
| RED 1 (alto) | `test_token_con_caracteres_no_ascii_da_403_y_no_500` → `TypeError: comparing strings with non-ASCII characters is not supported`. Un POST con `token=ñ` daba 500. |
| GREEN 1 | `verificar_token` compara bytes (`.encode()`). `Ran 248 tests — OK` |
| RED 2 | Tests nuevos en `TestCierreDeRama` y `TestComandoWeb`: `Ran 31 — FAILED (failures=7)`. Fallaban: fuentes de terceros, POST con `Origin` ajeno, errores 403/404/409 en JSON, nota sin tope, carta enorme. |
| GREEN 2 | `Ran 257 tests — OK` |

## Decisiones
- **Sin fuentes de terceros:** se quitó Google Fonts y se usa la tipografía del sistema. Cada carga ya no expone la IP a un tercero y el CSP queda sin orígenes externos. Alternativa descartada: alojar la fuente en local (añade un binario al repo para ganar solo estética).
- **Comprobación de `Origin` en los POST:** defensa en profundidad sobre el token CSRF. Si el navegador manda un `Origin` que no es 127.0.0.1/localhost, 403. Sin cabecera (curl, tests) manda el token.
- **Errores como página HTML:** un `exception_handler` para `HTTPException` y otro para `AlmacenamientoError` (→ 409) reemplazan los `try/except` repetidos y el JSON crudo. Alternativa descartada: capturar en cada ruta (lo que había).
- **Topes de entrada:** nota ≤ 500 caracteres (se recorta) y carta ≤ 20.000 (se rechaza con 400). La BD no se infla con pegados accidentales.
- **`render` fuera de `with Repositorio`:** abre su propia conexión para el embudo; en `preparar` se evitó tener dos conexiones a la vez.
- **Tipos, constantes (`PROVEEDOR_WEB`, `MODELO_WEB`), `_coincide` con nombre y docstring, `aviso_id` convertido a entero en Python.**
- **Tests del comando `web`:** ahora cubren `--sin-navegador` y la ausencia de dependencias (código de salida 1), y no imprimen en el reporte.

## Hallazgo del revisor que NO se reproduce
El `security-reviewer` indicó que los 400 de "Host inválido" salían sin CSP. Se comprobó con una petición real: **ya llevaban CSP y `nosniff`**, porque en Starlette el último middleware añadido es la capa externa. No se cambió código; se dejó el test de regresión `test_host_invalido_responde_400_con_cabeceras_de_seguridad`.

## Pendiente (no bloquea el PR)
- `_tipo_motivo` interpreta el texto de los motivos de `filtros.py` para colorear chips; si cambia el formato, se rompe en silencio. Lo ideal es que `filtros.py` entregue el signo del motivo.
- `CLAUDE.md` debería mencionar el extra `[web]` (FastAPI, uvicorn, Jinja2) además de `anthropic`, y fijar la versión de `coverage` en `dev`.
- No se miró visualmente el cambio de tipografía en un navegador.
