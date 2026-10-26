# TDD: atribución de fuentes generada desde `FUENTES`

- **Fecha:** 2026-10-26
- **Skills:** `ecc:tdd-workflow`
- **Origen:** al ejecutar «Descargar» desde la web, la línea final decía `Fuentes: Remotive, Arbeitnow` aunque se consultaban cuatro fuentes. Estaba escrita a mano en `cli.py` y quedó desactualizada al añadir We Work Remotely y Get on Board. La regla 2 del proyecto exige respetar la atribución de cada fuente.
- **Rama:** `fix/atribucion-fuentes` (desde `main`, independiente de los PR de la web).

## Decisiones
- **Cada fuente declara `titulo` y `sitio`** (atributos de clase en `Fuente`, vacíos por defecto) y `cli.atribucion(nombres)` arma la línea recorriendo `FUENTES`. Una fuente nueva se cita sola. Alternativa descartada: mantener el texto fijo y confiar en acordarse de editarlo (justo lo que falló).
- **Solo se citan las fuentes consultadas:** con `--fuente X` aparece únicamente X.
- **Fuentes de prueba sin título:** se citan por su `nombre`, para no obligar a los tests a declararlos.
- **Test que protege el futuro:** `test_las_fuentes_reales_declaran_titulo_y_sitio` falla si alguien registra una fuente sin título o sin sitio `https://`.

## Ciclos

| Fase | Resultado |
|---|---|
| RED | `Ran 47 — FAILED (failures=2, errors=5)`: `Fuente` no tenía `titulo`/`sitio` y no existía `cli.atribucion`. |
| GREEN | `Ran 229 tests — OK`. Línea real: `Fuentes: Remotive (https://remotive.com), Arbeitnow (https://www.arbeitnow.com), We Work Remotely (https://weworkremotely.com), Get on Board (https://www.getonbrd.com)` |

## Pendiente
- Los títulos y sitios salen de las cabeceras de cada adaptador; no se revisaron los términos de uso de We Work Remotely y Get on Board para ver si piden un texto de atribución concreto.
