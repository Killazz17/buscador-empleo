# TDD: corrección de los hallazgos de `ecc:python-reviewer`

- **Fecha:** 2026-10-23
- **Skill:** `ecc:tdd-workflow`
- **Origen:** informe de revisión de `ecc:python-reviewer`, con 8 hallazgos de prioridad crítica a baja. No hubo archivo `*.plan.md`: las historias de usuario se escribieron durante esta sesión.
- **Git:** el proyecto no está bajo git, así que no hay commits de checkpoint. Este informe es la evidencia.

## User journeys
1. Como usuario, quiero que ningún `--search` pueda escribir fuera de `data/` ni romper el programa en Windows.
2. Como usuario, quiero que un fallo de red dé un mensaje claro y no detenga las demás fuentes.
3. Como usuario, quiero conservar lo descargado si una página posterior falla.
4. Como usuario, quiero que una respuesta rara u oferta mala no tumbe toda la descarga.
5. Como usuario, quiero no pasarme del límite diario de Remotive ni quedarme con archivos a medias.

## RED → GREEN
Comando: `python -m unittest discover -s tests -t .`

| Fase | Resultado |
|---|---|
| RED (tests nuevos, código sin cambios) | `Ran 33 tests — FAILED (failures=4, errors=31)` (contando subtests). Errores causados por los bugs: `TimeoutError`, `ConnectionResetError`, `AttributeError: 'NoneType' object has no attribute 'get'`, `OverflowError`, `TypeError: 'str' object cannot be interpreted as an integer`, `FileNotFoundError` (por el `--search` hostil), `4 != 2` (sin límite diario) y `main` llamado al importar. Los 7 tests antiguos seguían en verde. |
| GREEN, intento 1 | Fallaron 2 tests antiguos, porque sus datos falsos usaban `links.next = "https://x/..."`, que ahora se rechaza a propósito. Se actualizaron para usar `URL_API`. |
| GREEN final | `Ran 33 tests — OK` |
| Cobertura (`coverage run`) | **90 %** en total (objetivo: 80 %) |

## Qué garantizan los tests

| # | Garantía | Test | Hallazgo |
|---|---|---|---|
| 1 | Un `--search` con `../`, `\`, `: * ? < > \| "`, `con` o 500 caracteres nunca escribe fuera de `data/` y produce nombres de menos de 120 caracteres | `test_cli.py::test_search_no_escapa_de_data` | 1 |
| 2 | Las búsquedas `c++` y `c#` no comparten archivo | `test_cli.py::test_busquedas_distintas_no_colisionan` | 1 |
| 3 | Timeout, conexión cortada, `IncompleteRead`, HTTP 500 y bytes inválidos dan `FuenteError` en ambas fuentes | `test_errores.py::TestErroresDeRed` | 2 |
| 4 | Un error inesperado en una fuente no corta las demás y la salida termina con código 1 | `test_cli.py::test_error_inesperado_de_una_fuente_no_corta_las_demas` | 2 |
| 5 | Si falla la página 2, se conserva la 1 y se avisa de "descarga parcial". Si falla la 1, es un error | `test_errores.py::TestPaginacionParcial` | 2 |
| 6 | Una respuesta `[]`, `null` o `"texto"`, o `jobs: null`, da `FuenteError`. Con `links: null`, la descarga sigue | `test_errores.py::TestFormaDeLaRespuesta` | 3 |
| 7 | Nunca se sigue un `links.next` hacia otro host, `file://` o una cadena vacía | `test_errores.py::test_arbeitnow_no_sigue_next_a_otro_host` | 3 |
| 8 | Las ofertas que no son objeto se descartan con aviso, sin afectar a las demás | `test_errores.py::test_job_que_no_es_dict_se_salta` | 3 |
| 9 | `created_at` como string, ISO, `None`, valor enorme, `bool` o `dict` no rompe la descarga | `test_errores.py::test_created_at_raro_no_rompe` | 4 |
| 10 | Los campos `null` se convierten en `""` o `[]`, y los tipos raros en `str` | `test_errores.py::TestCamposRaros` | 5 |
| 11 | Si falla el guardado del normalizado no queda el crudo, que es el marcador de "completado" | `test_cli.py::test_si_falla_el_normalizado_no_queda_crudo` | 6 |
| 12 | No quedan archivos `.tmp` | `test_cli.py::test_no_quedan_temporales` | 6 |
| 13 | Se respeta el límite diario aunque se use `--force` o búsquedas distintas | `test_cli.py::TestLimiteDiario` | 6 |
| 14 | Importar `buscador.__main__` no ejecuta la CLI | `test_cli.py::test_importar_main_no_ejecuta_la_cli` | 7 |
| 15 | Un `--search ""` equivale a no buscar | `test_cli.py::test_search_vacio_equivale_a_sin_busqueda` | 7 |

## Refactor
- `_pedir` estaba duplicado en `remotive.py` y `arbeitnow.py`. Se movió a `Fuente` (`base.py`) junto con `_normalizar_todos`, `texto()` y `lista_de_textos()`, así que las fuentes nuevas heredan la defensa ya hecha.

## Segunda ronda: re-revisión de `ecc:python-reviewer`
La re-revisión confirmó que los 8 hallazgos originales están bien resueltos. Encontró 4 nuevos, de prioridad media o baja, que se corrigieron con el mismo ciclo.

| Fase | Resultado |
|---|---|
| RED | `Ran 39 tests — FAILED (failures=2, errors=6)`: `'>=' not supported between 'str' and 'int'`, `PermissionError` sin capturar, `'str' object has no attribute 'get'`, `2 != 1` (mayúsculas) y `0 != 1` (`created_at="²"`) |
| GREEN | `Ran 39 tests — OK`, cobertura del **91 %** |

| # | Garantía | Test |
|---|---|---|
| 16 | Un contador con valores raros (`"3"`, `null`, `true`, una lista) no rompe la CLI | `test_cli.py::test_contador_con_valores_raros_no_rompe` |
| 17 | Si no se puede escribir el contador, no se llama a la API y se devuelve error sin traceback | `test_cli.py::test_fallo_al_escribir_contador_no_da_traceback_y_no_llama_a_la_api` |
| 18 | Un `PermissionError` al leer el contador equivale a un contador vacío | `test_cli.py::test_permiso_denegado_al_leer_contador` |
| 19 | `--search Python` y `--search python` usan el mismo archivo | `test_cli.py::test_mayusculas_reutilizan_el_mismo_archivo` |
| 20 | Un `links` que no es dict (`"x"`, `[1]`, `5`) no hace perder la página | `test_errores.py::test_links_de_tipo_raro_conserva_lo_descargado` |
| 21 | Con `created_at="²"` (un dígito Unicode) la oferta se conserva | `test_errores.py::test_created_at_con_digitos_unicode` |

**Decisión pendiente (no se cambió):** cuando se alcanza el límite diario la salida es 0, aunque no se descargue nada. Se mantiene así a propósito, porque llegar al límite no es un error. Se revisará cuando se automatice la ejecución (cron).

## Huecos conocidos
- `cli.main()`, que solo lee argumentos con argparse y maneja `KeyboardInterrupt`, no tiene test (líneas 123-139).
- Hay ramas de error sin cubrir: `URLError` en `_pedir`, la excepción dentro de `_normalizar` y una página con `data` que no es una lista.
- `coverage` está instalado solo en `.venv` como herramienta de desarrollo. El código de producción sigue usando solo la librería estándar.
