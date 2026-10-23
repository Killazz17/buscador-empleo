# Buscador de empleo automatizado

CLI en Python que **busca, filtra y prepara** postulaciones de empleo tech, y **nunca envía nada sin aprobación humana**.
Es un proyecto personal para aprender automatización, desarrollo guiado por tests y desarrollo asistido por IA (Claude Code).

```
descargar → filtrar → revisar/descartar → preparar (IA) → aprobar (humano) → postular (humano) → registrar
```

## Qué hace
- **Descarga** ofertas de 4 fuentes con acceso automático permitido (APIs públicas y RSS): Remotive, Arbeitnow, We Work Remotely y Get on Board (LATAM).
- **Puntúa** cada oferta con reglas explicables. Cada punto lleva su motivo, por ejemplo `kotlin (título) +6` o `idioma: german -8`. Las reglas se configuran en `config/perfil.toml`: tecnologías y sinónimos, nivel, idiomas y la regla de ubicación (remoto abierto a tu región, o presencial con reubicación).
- **Guarda** el estado de cada oferta en SQLite (`nueva → preparada → aprobada → enviada`), con historial de auditoría.
- **Prepara** borradores de carta con IA mediante el patrón Strategy: `manual` (gratis, pegando en la web de Claude o Gemini), `claude` (API de Anthropic) o `simulado` (tests).
- **Exige aprobación humana** antes de cualquier envío.

## Decisiones de diseño
- **Ports & Adapters:** las fuentes de empleo y los proveedores de IA son adaptadores intercambiables, registrados en un diccionario. Añadir uno nuevo es crear un archivo.
- **Defensa en profundidad para la regla "nunca enviar sin aprobación":**
  - máquina de estados en Python **y** triggers en SQLite (`enviada` solo es posible desde `aprobada`);
  - confirmación en una terminal interactiva;
  - huella SHA-256 del texto aprobado, que se verifica antes de marcarlo como enviado;
  - reglas `deny` para que el agente de IA no pueda aprobar.
- **Datos externos tratados como no confiables:** validación de la forma de cada respuesta, saneado de secuencias de escape de terminal, neutralización contra inyección de prompt y consultas SQL siempre parametrizadas.
- **Migraciones versionadas** con `PRAGMA user_version`.
- **Sin dependencias en el núcleo**: solo la librería estándar. El SDK `anthropic` es opcional.

## Uso
```bash
python -m pip install -e .        # crea el comando `buscador`
buscador descargar
buscador filtrar
buscador ver 12
buscador preparar 12              # borrador de carta (modo manual: gratis)
buscador aprobar 12               # solo una persona, en una terminal interactiva
buscador enviada 12 --nota "web"  # después de postular TÚ
```
Para usar la API de Claude: `pip install -e ".[claude]"`, define `ANTHROPIC_API_KEY` y pon `proveedor = "claude"` en `config/ia.toml`.

Tu CV va en `config/cv.md`, que está en `.gitignore` y nunca se sube.

## Calidad
- Todo el código se escribió con TDD (RED → GREEN → refactor): **225 tests** sin red ni coste (APIs simuladas) y una cobertura de alrededor del **90 %**.
- Revisiones con agentes especializados (Python, base de datos y seguridad). Cada hallazgo se corrigió con un test que lo reproducía primero.
- La evidencia de cada ciclo está en [`docs/tdd/`](docs/tdd/).

```bash
python -m unittest discover -s tests -t .
```
