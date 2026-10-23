# Buscador de Empleo Automatizado

## Objetivo
Proyecto de aprendizaje de automatización y Claude Code: un sistema que **busca, filtra y prepara**
postulaciones de empleo a partir del perfil de Sebastián. El usuario está aprendiendo, así que
**explica cada decisión técnica** (qué se eligió, por qué y qué alternativa se descartó) y avanza paso a paso.

## Perfil del candidato
- **Nivel:** estudiante de último año de Ingeniería en Sistemas de Información (UNA, Costa Rica, prevista 2027).
  Busca **práctica profesional o posición junior**.
- **Roles objetivo (por prioridad):** Backend, Full-Stack, Android, QA / Automation, Seguridad junior.
- **Modalidad (regla del usuario):** en **Costa Rica**, cualquier modalidad. **Fuera**: solo remoto abierto a
  Costa Rica (Worldwide, LATAM, Americas…), o presencial/híbrido **si la empresa ofrece reubicación** (visa,
  relocation). Se aplica en `filtros.py` desde la sección `[ubicacion]` de `config/perfil.toml`. Los remotos que
  no dicen región se mantienen con el aviso "verifica si aceptan Costa Rica".
- **Idiomas:** español nativo, inglés B2.
- **Tecnologías:**
  - Lenguajes: Java 17, Kotlin, TypeScript, JavaScript, Python, SQL/PL-SQL, PHP, C++
  - Backend: Spring Boot 3, Spring Security (JWT), JPA/Hibernate, FastAPI, Laravel, Flyway, OpenAPI, WebSocket
  - Frontend y móvil: React 19, Next.js, Vite; Android con Jetpack Compose, Hilt, Room, Retrofit (Clean Architecture, MVVM)
  - Datos y cloud: PostgreSQL, MySQL, Oracle, SQL Server; Docker, Google Cloud Run, Cloud SQL, Vercel, GitHub Actions
  - Calidad: JUnit, Mockito, MockK, PHPUnit, Cypress, Postman/Newman
  - Seguridad: Kali Linux, Metasploit, Nmap, Active Directory (práctica de 5 meses en el ICE)
  - IA aplicada: Claude Code, GitHub Copilot, MCP, ingeniería de prompts y de contexto
- **CV:** `sebastian-benavides-cv-es.pdf` (español) y `sebastian-benavides-cv-en.pdf` (inglés).

## Reglas innegociables
1. **Nunca enviar nada sin aprobación.** La IA busca, filtra y prepara (cartas, resúmenes, respuestas),
   pero **no envía** postulaciones, correos, mensajes ni formularios. Cada envío requiere la aprobación
   explícita del usuario para esa oferta concreta. Una aprobación no se extiende a otras ofertas.
   - Claude Code (o cualquier IA) **nunca ejecuta** `python -m buscador aprobar` ni `enviada`, ni intenta
     saltarse su confirmación (pseudo-terminales, `echo si |`, editar la BD…). Esos comandos los lanza el
     usuario en su propia terminal. `preparar` sí se puede ejecutar, pero **solo si el usuario lo pide,
     porque gasta dinero** de su API key.
   - Capas de protección y sus límites (dicho con honestidad):
     1. `confirmar()` exige una terminal interactiva y un "si" → frena aprobaciones accidentales o automáticas
        (scripts, cron, agentes).
     2. `aprobar()` y `marcar_enviada()` exigen `confirmado_por_persona=True` → un import distraído no aprueba.
     3. Los triggers de la BD → frenan bugs. Además, `verificar_aprobacion()` detecta si se manipuló el texto aprobado.
     4. Las reglas `deny` en `.claude/settings.json` → Claude Code no puede lanzar `aprobar`, `enviada` ni `sqlite3`,
        ni editar `data/`.
     **Ninguna capa resiste a un proceso con tu mismo usuario que quiera saltársela a propósito** (pseudo-terminal,
     importar el módulo, borrar triggers). La garantía real es operativa: esos comandos solo los ejecutas tú.
2. **Solo fuentes con acceso automático permitido:** APIs públicas y feeds RSS.
   - Prohibido el scraping de sitios que no lo permiten (LinkedIn, Indeed, Glassdoor…), saltarse logins o CAPTCHAs
     y usar cuentas del usuario en sitios de empleo.
   - Respetar los términos de cada fuente: límites de peticiones, atribución y User-Agent identificable.
3. **Privacidad:** no subir a git datos personales (CV, teléfono, correo) ni API keys.
   Los secretos van **solo en variables de entorno** (`ANTHROPIC_API_KEY`); el código no lee archivos `.env`.
   `data/`, `*.db`, los CV (`*.pdf`, `cv.*`, `config/cv*`) y cualquier `.env*` están en `.gitignore`.
   Antes del primer commit: `git check-ignore -v config/cv.md data/buscador.db`.
   Al usar la API se envían el CV (sin teléfono ni correo) y la oferta; con fallback puede responder otro modelo de Anthropic.

## Arquitectura
Monolito modular organizado como **pipeline**, con cada fuente como **adaptador** (Ports & Adapters):

```
Fuentes → ingesta → normalización → deduplicación → filtro/puntuación → preparación (IA) → APROBACIÓN → envío manual
```

- `src/buscador/modelos.py`: `Oferta`, el formato común al que se normaliza toda fuente.
- `src/buscador/fuentes/`: un archivo por fuente que hereda de `Fuente` (`base.py`) y se registra en
  `FUENTES` (`fuentes/__init__.py`). Fuentes actuales: Remotive, Arbeitnow (Europa), We Work Remotely
  (RSS, remoto global; `<region>` indica desde dónde aceptan) y Get on Board (LATAM; `remote_modality`
  define la ubicación). `Fuente._descargar()` devuelve bytes y `_pedir()` JSON; `unix_a_iso` y
  `coincide_busqueda` están en `base.py`. Añadir una fuente consiste en
  crear su archivo, registrarla y escribir sus tests. La CLI no se toca.
- `src/buscador/perfil.py` + `config/perfil.toml`: perfil editable (pesos por tecnología con alias, nivel,
  roles excluidos, idiomas, tope de puntos por descripción). TOML porque `tomllib` es de la librería estándar.
- `src/buscador/filtros.py`: puntuación por reglas, con funciones puras. Cada punto lleva un motivo legible.
  Deduplica por `(fuente, id)` y por `(empresa, título)`.
- `src/buscador/ia/`: **patrón Strategy** para la IA. `ProveedorIA.generar()` (`base.py`), con tres proveedores
  registrados en `PROVEEDORES`:
  - `manual` (**el que está configurado por defecto, gratis**): escribe el prompt en `data/manual/`, lo copia al
    portapapeles, y el usuario lo pega en la web de Claude o Gemini;
  - `claude` (SDK `anthropic`, importado solo si se usa);
  - `simulado` (para los tests). Se elige en `config/ia.toml`. Añadir una IA (p. ej. Gemini) = un archivo nuevo + registrarla + sus tests.
- `src/buscador/preparacion.py` + `prompts/carta.md` + `config/cv.md` (el CV sin teléfono ni correo, en
  `.gitignore`). La oferta es texto de terceros: va en `<oferta>` con `<` y `>` neutralizados (contra la inyección de prompt).
- `src/buscador/cli.py`: punto de entrada (`python -m buscador ...`).
- `data/raw/`: respuestas crudas de cada API, para poder reprocesar sin volver a pedir.
- `data/ofertas_<fuente>_<fecha>[_<búsqueda>].json` → `data/filtradas_<fecha>.json` (ranking con puntuación y motivos).
- `src/buscador/almacenamiento.py`: `Repositorio` SQLite en `data/buscador.db`, donde vive todo el SQL.
  - Máquina de estados `TRANSICIONES`: `nueva → descartada/preparada → aprobada → enviada`, y `descartada → nueva`.
    **Solo `aprobada` lleva a `enviada`**: nunca saltarse esta regla ni añadir transiciones que la rompan.
    La BD también la hace cumplir con triggers (migración v2), y el historial es solo de añadir.
    Si cambias `TRANSICIONES`, añade una migración nueva que actualice el trigger `ofertas_transicion_valida`.
    El test `test_la_bd_aplica_exactamente_la_tabla_de_transiciones` comprueba que ambos coinciden.
  - Toda escritura pasa por `_escritura()` (`BEGIN IMMEDIATE`): la lectura y la escritura van bajo el mismo bloqueo.
  - El esquema se versiona con `MIGRACIONES` y `PRAGMA user_version`. Las migraciones publicadas no se editan:
    los cambios se añaden como una migración nueva al final.
  - Todas las consultas son parametrizadas, sin f-strings con datos.

No introducir microservicios, colas ni infraestructura extra hasta que haya un problema real que lo justifique.

## Convenciones
- Python 3.12. El código de producción usa la librería estándar, salvo `anthropic`, que es opcional y se usa solo con
  el proveedor `claude`. `coverage` está únicamente en `.venv` (desarrollo).
- Código y comentarios en español, con comentarios didácticos (explican el *porqué*).
- Instalado con `python -m pip install -e .` (`pyproject.toml`): el comando `buscador ...` funciona desde
  cualquier carpeta (equivale a `python -m buscador ...` desde `src/`). Con la API de Claude: `pip install -e ".[claude]"`.
- `filtrar` re-evalúa también las pendientes ya guardadas: si dejan de cumplir el perfil pasan a `descartada`,
  con el motivo en el historial.
- Comandos (`buscador X` o `python -m buscador X` desde `src/`):
  - `python -m buscador descargar [--fuente X] [--search X] [--force]`
  - `python -m buscador filtrar [--fecha AAAA-MM-DD] [--top N] [--minimo N]` (también guarda en SQLite)
  - `python -m buscador listar [--estado nueva|descartada|preparada|aprobada|enviada|todas] [--top N]`
  - `python -m buscador descartar ID [ID ...] [--nota TEXTO]` y `python -m buscador restaurar ID [ID ...]`
  - `python -m buscador preparar ID [ID ...]` (la IA redacta un borrador → 'preparada') y `python -m buscador ver ID`
  - `python -m buscador aprobar ID` y `python -m buscador enviada ID [--nota TEXTO]`: **solo el usuario**, con
    confirmación en una terminal interactiva
  - Con `proveedor = "claude"` hay que usar el Python del venv (`../.venv/Scripts/python -m buscador ...`), que tiene
    el SDK `anthropic` (`requirements.txt`), y definir `ANTHROPIC_API_KEY`.
- Cobertura, desde la raíz: `.venv/Scripts/python -m coverage run --source=src/buscador -m unittest discover -s tests -t .`
  y después `coverage report -m`.
- Cada cambio se hace con `ecc:tdd-workflow` (RED → GREEN → refactor) y deja su informe en `docs/tdd/`.
- Tests sin red (se hace mock de `_pedir`), ejecutados desde la raíz: `python -m unittest discover -s tests -t .`
- Para construir, usar el plugin **ecc** del usuario: sus skills (p. ej. `ecc:api-connector-builder` para fuentes nuevas,
  `ecc:python-patterns`, `ecc:tdd-workflow`) y sus agentes de revisión (`ecc:python-reviewer`).
