# TDD: paso 3, almacenamiento en SQLite con estados

- **Fecha:** 2026-10-23
- **Skill:** `ecc:tdd-workflow`
- **Origen:** recomendación tras el paso 2: guardar el estado de cada oferta para no revisar las mismas cada día y preparar la cola de aprobación. Las historias de usuario se escribieron en esta sesión.
- **Git:** el proyecto no está bajo git, así que no hay commits de checkpoint. Este informe es la evidencia.

## User journeys
1. Como candidato, quiero que `filtrar` guarde las ofertas y me diga cuáles son nuevas hoy, para no revisar las mismas cada día.
2. Como candidato, quiero descartar ofertas por su número y poder restaurarlas, para que no vuelvan a aparecer.
3. Como candidato, quiero listar las ofertas por estado.
4. Como candidato, quiero tener la garantía de que nada pasa a "enviada" sin pasar antes por "aprobada", y un historial de cada cambio.

## Decisiones
- **SQLite** (`sqlite3`, librería estándar; SQLite 3.49.1 admite `UPSERT`), con la base de datos en `data/buscador.db`.
- **Patrón Repositorio:** todo el SQL está en `almacenamiento.py`.
- **Máquina de estados** `TRANSICIONES`. Solo `aprobada` lleva a `enviada`, y `enviada` es un estado final.
- **Defensa en profundidad:** las transiciones se validan en Python y además hay un `CHECK (estado IN (...))` en la tabla.
- **Migraciones** versionadas con `PRAGMA user_version`. Cada una se aplica en su propia transacción, y una base de datos de una versión futura se rechaza.
- **`UPSERT`** que refresca los datos y la puntuación pero conserva `estado` y `primera_vez`.
- **Consultas siempre parametrizadas.**

## Ciclo RED → GREEN
Comando: `python -m unittest discover -s tests -t .`

| Fase | Resultado |
|---|---|
| RED | `FAILED (errors=2)`. Es un RED de compilación: `ModuleNotFoundError: No module named 'buscador.almacenamiento'` en `test_almacenamiento` y `test_cli`. |
| GREEN del repositorio | `python -m unittest tests.test_almacenamiento`: `Ran 25 tests — OK` |
| GREEN completo, intento 1 | `Ran 132 tests — FAILED (failures=1)`. **Era un fallo del diseño del test:** `TestEstadosDesdeLaCli` heredaba de `TestFiltrar` y volvía a ejecutar sus tests con otro `setUp`. Se extrajo `BaseFiltrar`, una clase sin tests. |
| GREEN final | `Ran 124 tests — OK` |
| De extremo a extremo con datos reales | El primer `filtrar` dio "61 nuevas" y el segundo "0 nuevas, 61 ya conocidas". `descartar 1 --nota ...` funcionó. Después, `restaurar 1` funcionó y un segundo `restaurar 1` se rechazó ("está 'nueva' y no puede pasar a 'nueva'", código 1). El historial quedó con 2 entradas y el esquema en v1. |

## Ciclo 2: revisión de `ecc:database-reviewer` (migración v2)
La revisión encontró 6 hallazgos: 1 CRÍTICO, 2 ALTOS, 2 MEDIOS y 1 BAJO. El hallazgo crítico fue que la regla "solo se llega a `enviada` desde `aprobada`" vivía **solo en Python**: el `CHECK` valida los valores, no las transiciones, y un `UPDATE` directo `nueva → enviada` se aceptaba.

La base de datos real del usuario ya estaba en la v1, con 61 ofertas. Por eso los cambios de esquema van en una **migración v2 nueva**, sin editar la v1.

| Fase | Resultado |
|---|---|
| RED | `python -m unittest tests.test_almacenamiento`: `Ran 35 tests — FAILED (failures=22, errors=2)`. **La BD aceptaba 14 transiciones prohibidas** (entre ellas `nueva → enviada` y `descartada → enviada`), un `DELETE` del historial, duplicados contados como `(1 nueva, 2 actualizadas)` y títulos de ofertas aprobadas sobrescritos. `JSONDecodeError` en lugar de un error del dominio. |
| GREEN | `Ran 134 tests — OK` |
| Migración real | `data/buscador.db`: **v1 con 61 ofertas → v2 con 61 ofertas** y el historial intacto. Después, un `UPDATE ... estado='enviada'` directo se rechaza con "transición de estado no permitida". |

| Hallazgo | Corrección | Tests |
|---|---|---|
| CRÍTICO: transiciones solo en Python | Migración v2 con triggers: `ofertas_insertar_solo_nueva` y `ofertas_transicion_valida` (`BEFORE UPDATE OF estado`) | `test_la_bd_aplica_exactamente_la_tabla_de_transiciones` (los 20 pares origen→destino contra `TRANSICIONES`), `test_la_bd_no_permite_insertar_*`, `test_actualizar_otras_columnas_sigue_permitido` |
| ALTO: el `SELECT` del estado quedaba fuera de la transacción (modo legacy de `sqlite3`) | `_escritura()` con `BEGIN IMMEDIATE`, y compare-and-swap (`UPDATE ... WHERE estado = ?` + `rowcount`) | `test_bd_ocupada_por_otro_proceso_da_error_claro` |
| ALTO: duplicados dentro del lote | El repositorio deduplica por `(fuente, id_fuente)` quedándose con la mejor puntuación | `test_duplicados_en_el_lote_cuentan_una_vez_y_gana_la_mejor` |
| MEDIO: JSON corrupto → traceback; la lista de estados está duplicada | `_lista_json()` lanza `AlmacenamientoError`, y un test compara `ESTADOS` con el `CHECK` | `test_json_corrupto_da_error_claro`, `test_lista_de_estados_coincide_con_el_check_de_la_tabla` |
| MEDIO: el UPSERT sobrescribía ofertas aprobadas | `DO UPDATE ... WHERE ofertas.estado NOT IN ('aprobada', 'enviada')` | `test_ofertas_aprobadas_o_enviadas_no_se_refrescan` |
| BAJO: historial borrable; "BD ocupada" mal etiquetada | Triggers que hacen el historial solo de añadir e impiden borrar ofertas con historial. `OperationalError` se distingue de una BD corrupta, con `timeout` configurable | `test_historial_es_solo_de_anadir`, `test_bd_ocupada_*` |
| Migración sobre una BD existente | — | `test_una_bd_v1_existente_se_migra_sin_perder_datos` |

**Aplazado al paso 5 (anotado):**
- Las tablas `preparaciones(oferta_id, version, texto, modelo, prompt_hash, …)` y `aprobaciones(oferta_id, preparacion_id, aprobada_en, contenido_hash)`. Pasar a `enviada` exigirá una aprobación sobre una preparación concreta.
- Los `CHECK` de `json_valid` y `datetime()` en las columnas: requieren reconstruir la tabla (SQLite no permite `ALTER ... ADD CHECK`) con `PRAGMA foreign_keys = OFF` fuera de la transacción. Se hará cuando haya que reconstruirla por otro motivo.
- `AUTOINCREMENT` para no reutilizar ids. Hoy no se borran ofertas, y los triggers lo impiden si tienen historial.

## Qué garantizan los tests

| # | Garantía | Test |
|---|---|---|
| 1 | Una oferta se guarda y se lee sin perder ningún campo | `test_almacenamiento.py::test_guarda_y_lee_la_oferta_completa` |
| 2 | Se distinguen las ofertas nuevas de las ya conocidas | `test_resumen_distingue_nuevas_de_conocidas`, `test_cli.py::test_filtrar_distingue_nuevas_de_ya_vistas` |
| 3 | Volver a guardar actualiza los datos y la puntuación, pero conserva el estado y `primera_vez` | `test_volver_a_guardar_actualiza_datos_pero_conserva_estado_y_primera_vez`, `test_descartar_oculta_la_oferta_y_sobrevive_a_volver_a_filtrar` |
| 4 | La misma `id_fuente` en fuentes distintas son ofertas distintas | `test_misma_id_en_distinta_fuente_son_ofertas_distintas` |
| 5 | La inyección SQL se guarda como texto inofensivo | `test_inyeccion_sql_se_guarda_como_texto` |
| 6 | Guardar es atómico: si una evaluación falla, no se guarda ninguna | `test_guardar_es_atomico` |
| 7 | Los datos persisten al reabrir la base de datos | `test_persiste_al_reabrir` |
| 8 | Se ordena por puntuación, se filtra por estado y se aplica el límite | `TestListar` |
| 9 | **No se puede llegar a `enviada` sin `aprobada`**, y solo `aprobada` lleva a `enviada` | `test_no_se_puede_enviar_sin_aprobar`, `test_cada_estado_tiene_sus_transiciones_definidas` |
| 10 | `enviada` es un estado final | `test_enviada_es_final` |
| 11 | Una transición rechazada no cambia nada, ni el estado ni el historial | `test_transicion_rechazada_no_cambia_nada` |
| 12 | El historial registra cada cambio con su fecha y su nota | `test_historial_registra_cada_cambio` |
| 13 | El `CHECK` de la tabla rechaza estados inventados aunque se salte el repositorio | `test_la_base_de_datos_tambien_rechaza_estados_invalidos` |
| 14 | Las migraciones dejan la base en la última versión, no se reaplican y rechazan versiones futuras o archivos que no son SQLite | `TestMigraciones` |
| 15 | Con `descartar` y `restaurar`, un id inválido o una transición imposible dan un error claro sin impedir procesar los demás | `test_cli.py::TestEstadosDesdeLaCli` |
| 16 | `listar` muestra el `#id`, filtra por estado (o todas) y no crea la base de datos si no existe | `test_listar_*` |

## Cobertura
`coverage run --source=src/buscador -m unittest discover -s tests -t .` da un **90 %** en total, con `almacenamiento.py` al **97 %**.

## Huecos conocidos
- No hay test para un fallo de SQL a mitad de una migración (las líneas del rollback). Con una sola migración, no es fácil provocarlo de forma realista.
- `cli.main()`, el argparse de los subcomandos nuevos y las ramas de `AlmacenamientoError` en la CLI no tienen test. Se verificaron a mano.
- Todavía no hay comandos `preparar`, `aprobar` ni `enviar`: llegarán con el paso 5 (preparación con IA). Las transiciones ya están definidas y probadas.
