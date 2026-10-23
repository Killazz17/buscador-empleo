# TDD: paso 2, filtrar y puntuar ofertas contra el perfil

- **Fecha:** 2026-10-23
- **Skill:** `ecc:tdd-workflow`
- **Origen:** hoja de ruta del plan inicial ("Paso 2: filtrar por palabras clave del CV y puntuar"). Las historias de usuario se escribieron en esta sesión.
- **Git:** el proyecto no está bajo git, así que no hay commits de checkpoint. Este informe es la evidencia.

## User journeys
1. Como candidato, quiero ver primero las ofertas que mejor encajan con mi stack, con el motivo de cada punto.
2. Como candidato, quiero que se descarten o bajen las ofertas Senior, las que piden alemán y las de roles que no son técnicos.
3. Como candidato, quiero ajustar los pesos editando un archivo, sin tocar código, y que un error en ese archivo dé un mensaje claro.
4. Como candidato, quiero un comando `filtrar` que junte las descargas del día, sin duplicados, y guarde un ranking.

## Decisiones
- **El perfil va en TOML** (`config/perfil.toml`) y no en YAML: `tomllib` es de la librería estándar, así que no se añade ninguna dependencia.
- **La puntuación es por reglas, con motivos.** Así es explicable y se puede ajustar. La IA queda para el paso 5.
- **Coincidencia de "palabra completa" con lookarounds**, no con `\b`: `\b` falla con `c++` y `next.js`.
- **Nuevo campo `Oferta.remoto`**, con `Oferta.desde_dict()` para leer el JSON de forma robusta y aceptar archivos antiguos.

## Ciclo 1: funcionalidad base
Comando: `python -m unittest discover -s tests -t .`

| Fase | Resultado |
|---|---|
| RED | `Ran 47 tests — FAILED (errors=10)`. Es un RED de compilación por implementación ausente: `No module named 'buscador.filtros'`, `cli` sin `RUTA_PERFIL` y `'Oferta' object has no attribute 'remoto'`. |
| GREEN | `Ran 73 tests — OK` |

## Ciclo 2: problemas descubiertos con datos reales
Al ejecutar `python -m buscador filtrar` con las 918 ofertas reales aparecieron tres problemas:
1. Ofertas Senior arriba del todo (+26), porque su descripción listaba 15 tecnologías.
2. La misma oferta duplicada en `arbeitnow.nl` y `arbeitnow.co.uk`, con ids distintos.
3. `full-stack` y `fullstack` contaban dos veces.

| Fase | Resultado |
|---|---|
| RED | `Ran 79 tests — FAILED (failures=1, errors=5)`: `Perfil` no tenía `alias` ni `puntos_tope_descripcion`, y el duplicado entre dominios daba `2 != 1`. |
| Ajuste de un test existente | `test_quita_duplicados_por_fuente_e_id` usaba títulos iguales en fuentes distintas. Con la nueva regla ("misma empresa y título = duplicado") se cambió el título de la tercera oferta para conservar su intención: probar solo la clave `(fuente, id)`. |
| GREEN | `Ran 79 tests — OK` |
| Ajuste del perfil (configuración, no código) | `nivel_penaliza` pasó de -6 a -12, se añadió `tope_descripcion = 8` y alias para `backend`, `fullstack` y `gcp`. |
| Resultado real | De 918 ofertas, 57 llegan al mínimo. El puesto 1 es "Software Engineer Fullstack Kotlin / React" (+22). Aparecen una práctica de fin de carrera y puestos de QA y Test Automation. Ya no hay Senior arriba ni duplicados. |

## Ciclo 3: revisión de `ecc:python-reviewer`
La revisión encontró 6 hallazgos (2 ALTOS, ninguno crítico). El revisor comprobó varios ejecutando el código.

| Fase | Resultado |
|---|---|
| RED | `Ran 90 tests — FAILED (failures=26)` (contando subtests). Dos tests de seguridad ya pasaban y siguieron en verde: `test_idioma_exigido_resta` y `test_sufijo_de_letras_sigue_sin_coincidir`. |
| GREEN | `Ran 90 tests — OK`, cobertura del **92 %** |
| Resultado real | Pasan el mínimo 61 ofertas en vez de 57. Se recuperan ofertas que antes quedaban fuera por penalizaciones de idioma mal aplicadas o por duplicados que no lo eran. |

| Hallazgo | Corrección | Tests |
|---|---|---|
| ALTO: un término vacío (`""`) coincidía con casi todo | `_termino()` rechaza los términos vacíos o en blanco con `PerfilError` | `test_errores_de_escritura_en_el_perfil` |
| ALTO: "No German required" restaba -8 | El idioma se evalúa **frase a frase**: solo penaliza si hay una señal de exigencia (fluent, required, C1…) y no hay negación ni opcionalidad (no, plus, nice to have…) | `test_idioma_negado_u_opcional_no_resta`, `test_idioma_exigido_resta` |
| MEDIO: la deduplicación fusionaba ofertas sin empresa, o de la misma empresa en otra ciudad, y se quedaba con la primera | La clave de contenido es `(empresa, título, ubicación)` y solo se usa si hay empresa. Se ordena antes de deduplicar para que sobreviva la mejor copia | `test_sin_empresa_*`, `test_misma_empresa_en_otra_ciudad_*`, `test_entre_duplicados_se_queda_la_mejor` |
| MEDIO: `<2 years of Java` borraba "Java" | Solo cuenta como etiqueta un `<` seguido de letra o `/` | `test_menor_que_no_borra_texto` |
| MEDIO: falsos negativos con `REST\nAPI`, `Python3` y `C++17` | Los espacios del término aceptan `\s+`, y después del término se permiten dígitos pero no letras. Se añadieron alias `nextjs`, `nodejs`, `rest apis` y `restful` en el perfil | `test_terminos_con_espacio_*`, `test_sufijo_numerico_de_version`, `test_sufijo_de_letras_sigue_sin_coincidir` |
| BAJO: typos y signos incoherentes en el perfil se aceptaban en silencio | Se rechazan las claves desconocidas (arriba, en `[puntos]` y en cada tecnología), las tecnologías duplicadas por mayúsculas, `[tecnologias]` vacía y los signos incoherentes | `test_errores_de_escritura_en_el_perfil` (12 casos) |
| BAJO: el glob podía colar `--search <fecha>` hecho otro día | `fullmatch` con la forma exacta del nombre | `test_busqueda_con_forma_de_fecha_no_confunde_el_dia` |

**No aplicado (anotado):**
- **Rendimiento:** unos 3,6 s para 900 ofertas. Es aceptable en una CLI que se ejecuta un par de veces al día. Si molesta, se puede añadir un prefiltro con `in` antes de la regex.
- **Descartes por rol invisibles:** las ofertas descartadas por "legal", "marketing"… no se muestran. Es una decisión de diseño. Se puede añadir un recuento en pantalla más adelante.

## Ciclo 4: regla de ubicación del usuario y arreglos salidos del primer uso real
Pedido del usuario: "si la oferta es fuera de mi país, tiene que ser en remoto; solo las de mi país en remoto o presencial, a menos de que ellos me ayuden a ir a sus países". Para los remotos sin región clara, el usuario eligió mantenerlos con aviso.

| Fase | Resultado |
|---|---|
| RED 1 | `Ran 208 — FAILED (failures=2, errors=17)`: `Perfil` no tenía `pais`; el HTML escapado dejaba `<h2><strong>What`; al pegar quedaban `'﻿Dear X,\n\n\n\n…'` (el bug real de la primera carta); faltaba `pyproject.toml`. |
| GREEN 1 | `Ran 208 — OK`. Con datos reales: de 61 ofertas se pasó a 15, **pero** la lista seguía mostrando pendientes antiguas que ya no cumplían la regla. |
| RED 2 | `'nueva' != 'descartada'`: las pendientes guardadas antes de la regla no se re-evaluaban. |
| GREEN 2 | `Ran 211 — OK`. Con datos reales: "46 pendientes ya no cumplen tu perfil". El resultado mostró 3 falsos positivos más. |
| RED 3 | *"We do not offer visa sponsorship"* contaba como reubicación; "Remote from EU" en el título no restringía; una oferta que había desaparecido del feed quedaba pendiente. |
| GREEN 3 | `Ran 214 — OK`. Con datos reales: 4 descartadas más (AlmavivA, European Dynamics, Accurids, HEMERIA), cada una con su motivo en el historial. |

Cambios:
- `[ubicacion]` (`pais`, `remoto_aceptado`, `reubicacion`, `restricciones_remoto`) y `puntos.remoto_dudoso`.
- `_descartar_por_ubicacion()`, y `_reubicacion_ofrecida()` analizando frase a frase con negaciones.
- `limpiar_html` con dos pasadas (HTML escapado).
- `Manual._leer_respuesta()` (BOM y `\r\r\n`).
- `pyproject.toml` con el comando `buscador`.
- `retirar_las_que_ya_no_cumplen()` re-evalúa desde la BD.

## Qué garantizan los tests

| # | Garantía | Test |
|---|---|---|
| 1 | Una tecnología en el título vale el doble que en la descripción | `test_filtros.py::test_en_titulo_vale_doble` |
| 2 | Cada tecnología cuenta una vez, con su mejor coincidencia, y los alias no suman doble | `test_cada_tecnologia_cuenta_una_sola_vez`, `test_sinonimos_cuentan_una_sola_vez` |
| 3 | `java` no coincide con `javascript`, `c++` sí se encuentra y no se distinguen mayúsculas | `test_java_no_coincide_con_javascript`, `test_terminos_con_simbolos`, `test_no_distingue_mayusculas_ni_puntuacion` |
| 4 | Cada punto tiene un motivo legible (`kotlin (título) +6`) | `test_motivos_explican_cada_punto` |
| 5 | Se limpia el HTML: los atributos no puntúan y el texto sí | `TestHtml` |
| 6 | El nivel solo cuenta en el título: junior suma, senior resta | `TestNivelYRol` |
| 7 | Un rol excluido en el título descarta la oferta | `test_rol_excluido_descarta` |
| 8 | Un idioma excluyente o una descripción en alemán restan una sola vez | `TestIdiomaYModalidad` |
| 9 | Hay un tope de puntos por tecnologías que solo están en la descripción, y el recorte aparece como motivo | `test_tope_de_puntos_*`, `test_tope_explica_el_recorte` |
| 10 | Se quitan duplicados por `(fuente, id)` y por `(empresa, título)` | `test_quita_duplicados_*`, `test_misma_oferta_en_otro_dominio_es_duplicado` |
| 11 | El resultado se ordena de mejor a peor, aplica el mínimo del perfil o de `--minimo` y quita las descartadas | `TestEvaluarTodas` |
| 12 | El perfil se valida: archivo inexistente, TOML mal escrito, claves que faltan o tipos incorrectos dan `PerfilError` con un mensaje claro | `TestCargarPerfil` |
| 13 | El `config/perfil.toml` real del proyecto es válido | `test_el_perfil_real_del_proyecto_es_valido` |
| 14 | `filtrar` junta solo los archivos del día, los ordena y guarda `filtradas_<fecha>.json` | `test_cli.py::TestFiltrar` |
| 15 | Los archivos u ofertas corruptos se saltan, y se aceptan archivos antiguos sin `remoto` | `test_archivos_o_ofertas_corruptas_se_saltan`, `test_archivos_antiguos_sin_campo_remoto_se_aceptan` |
| 16 | Un perfil inválido o la falta de descargas dan código 1, sin traceback | `test_perfil_invalido_*`, `test_sin_descargas_del_dia_es_error` |
| 17 | Las fuentes rellenan `remoto` (Remotive siempre `True`; Arbeitnow según `remote`) | `test_fuentes.py::test_normaliza_*` |

## Cobertura
`coverage run --source=src/buscador -m unittest discover -s tests -t .` da un **92 %** en total, con `filtros.py` al **100 %**.

## Huecos conocidos
- `cli.main()` (argparse) y la impresión del ranking en pantalla no tienen test. Se verificaron a mano con datos reales.
- `fecha_iso` (la validación de `--fecha`) no tiene test directo.
- La detección de alemán es heurística (5 o más palabras frecuentes). Un texto en inglés con muchas palabras como "die" o "mit" podría marcarse por error.
- No se penalizan las ofertas remotas restringidas a un país (por ejemplo, "USA only"). Queda como posible mejora.
