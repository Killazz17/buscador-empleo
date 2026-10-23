"""Punto de entrada por línea de comandos:

    python -m buscador descargar [--fuente X] [--search X] [--force]
    python -m buscador filtrar   [--fecha AAAA-MM-DD] [--top N] [--minimo N]
    python -m buscador listar    [--estado nueva|descartada|...|todas] [--top N]
    python -m buscador descartar ID [ID ...] [--nota TEXTO]
    python -m buscador restaurar ID [ID ...]
    python -m buscador preparar  ID [ID ...]          (la IA redacta un borrador)
    python -m buscador ver       ID
    python -m buscador aprobar   ID                   (solo una persona, en una terminal interactiva)
    python -m buscador enviada   ID [--nota TEXTO]    (registra que TÚ la enviaste)
"""

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from buscador.almacenamiento import ESTADOS, AlmacenamientoError, OfertaGuardada, Repositorio
from buscador.filtros import evaluar, evaluar_todas
from buscador.fuentes import FUENTES
from buscador.fuentes.base import Fuente, FuenteError
from buscador.ia import cargar_config_ia, crear_proveedor
from buscador.ia.base import IAError
from buscador.modelos import Oferta
from buscador.modelos import sin_controles as _s  # todo texto de terceros o de la IA se imprime saneado
from buscador.perfil import PerfilError, cargar_perfil
from buscador.preparacion import alertas_borrador, preparar_oferta

# La carpeta data/ vive en la raíz del proyecto, independientemente de desde dónde se ejecute.
# __file__ = .../src/buscador/cli.py → parents[2] = raíz del proyecto.
RAIZ = Path(__file__).resolve().parents[2]
DIR_DATA = RAIZ / "data"
DIR_RAW = DIR_DATA / "raw"
RUTA_PERFIL = RAIZ / "config" / "perfil.toml"
RUTA_CONFIG_IA = RAIZ / "config" / "ia.toml"
RUTA_CV = RAIZ / "config" / "cv.md"
RUTA_PLANTILLA = RAIZ / "prompts" / "carta.md"


def ruta_db() -> Path:
    # Función y no constante: así sigue a DIR_DATA (los tests lo cambian por una carpeta temporal).
    return DIR_DATA / "buscador.db"


def slug_busqueda(search: str) -> str:
    """Convierte la búsqueda (texto NO confiable) en un trozo de nombre de archivo seguro.

    Solo [a-z0-9-] → imposible escapar de data/ con "../" o usar caracteres prohibidos en Windows.
    El hash corto evita colisiones entre búsquedas que dan el mismo slug ("c++" y "c#" → "c").
    """
    legible = re.sub(r"[^a-z0-9]+", "-", search.lower()).strip("-")[:40]
    # casefold: "Python" y "python" son la misma búsqueda → mismo archivo, sin gastar otra petición.
    huella = hashlib.sha1(search.casefold().encode("utf-8")).hexdigest()[:6]
    return f"{legible}-{huella}" if legible else huella


def guardar_json(datos, ruta: Path) -> None:
    """Escritura atómica: se escribe a un .tmp y luego se renombra.

    Si el proceso muere a mitad, queda el .tmp (que se ignora), nunca un JSON cortado con el nombre bueno.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.name + ".tmp")
    try:
        # ensure_ascii=False conserva tildes y ñ legibles; indent=2 lo hace fácil de leer a mano.
        temporal.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporal, ruta)  # atómico en el mismo disco
    finally:
        temporal.unlink(missing_ok=True)


def leer_contador(ruta: Path) -> dict:
    """Peticiones hechas hoy por fuente. Si el archivo no existe, no se puede leer o está dañado, empezamos de cero."""
    try:
        contador = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # OSError incluye FileNotFoundError y PermissionError
        return {}
    return contador if isinstance(contador, dict) else {}


def descargar_fuente(fuente: Fuente, args: argparse.Namespace) -> bool:
    """Descarga una fuente y guarda sus archivos. Devuelve False si falló."""
    hoy = date.today().isoformat()
    # Una búsqueda vacía o de solo espacios equivale a no buscar.
    search = (args.search or "").strip() or None
    # Un archivo por fuente, día y búsqueda: así hay historial y sabemos si ya pedimos hoy.
    sufijo = f"_{slug_busqueda(search)}" if search else ""
    nombre = f"{fuente.nombre}_{hoy}{sufijo}.json"
    ruta_raw = DIR_RAW / nombre
    ruta_ofertas = DIR_DATA / f"ofertas_{nombre}"

    print(f"[{fuente.nombre}]")
    # Si ya descargamos hoy esta búsqueda, no repetimos (salvo --force).
    if ruta_raw.exists() and not args.force:
        print(f"  Ya existe {ruta_raw.relative_to(RAIZ)}; no se vuelve a llamar a la API (usa --force).")
        return True

    # Límite diario de los términos de la API: ni --force ni búsquedas distintas lo saltan.
    ruta_contador = DIR_DATA / f"peticiones_{hoy}.json"
    contador = leer_contador(ruta_contador)
    usadas = contador.get(fuente.nombre)
    # El archivo pudo editarse a mano: solo aceptamos enteros (bool es subclase de int, lo excluimos).
    if not isinstance(usadas, int) or isinstance(usadas, bool):
        usadas = 0
    if fuente.limite_diario is not None and usadas >= fuente.limite_diario:
        print(f"  Límite diario alcanzado ({usadas}/{fuente.limite_diario}); se omite hasta mañana.")
        return True
    # Se cuenta ANTES de llamar: un intento fallido también consume petición en el servidor.
    contador[fuente.nombre] = usadas + 1
    try:
        guardar_json(contador, ruta_contador)
    except OSError as e:
        # Si no podemos registrar la petición no la hacemos: no podríamos garantizar el límite.
        print(f"  Error al actualizar el contador de peticiones: {e}", file=sys.stderr)
        return False

    try:
        crudo, ofertas = fuente.descargar(search)
    except FuenteError as e:
        print(f"  Error: {e}", file=sys.stderr)
        return False
    except Exception as e:
        # Red de seguridad: un fallo inesperado en una fuente no debe frenar a las demás.
        print(f"  Error inesperado: {e!r}", file=sys.stderr)
        return False

    for aviso in fuente.avisos:
        print(f"  Aviso: {aviso}")

    try:
        # El crudo se escribe AL FINAL: su existencia significa "descarga completada".
        guardar_json([asdict(o) for o in ofertas], ruta_ofertas)
        guardar_json(crudo, ruta_raw)
    except OSError as e:
        print(f"  Error al guardar: {e}", file=sys.stderr)
        return False

    print(f"  {len(ofertas)} ofertas descargadas.")
    print(f"  crudo:       {ruta_raw.relative_to(RAIZ)}")
    print(f"  normalizado: {ruta_ofertas.relative_to(RAIZ)}")
    return True


def comando_descargar(args: argparse.Namespace) -> int:
    nombres = [args.fuente] if args.fuente else list(FUENTES)
    # Si una fuente falla, seguimos con las demás: una API caída no debe frenar todo el pipeline.
    resultados = [descargar_fuente(FUENTES[n](), args) for n in nombres]
    # Atribución que piden los términos de ambas APIs.
    print("Fuentes: Remotive (https://remotive.com), Arbeitnow (https://www.arbeitnow.com)")
    return 0 if all(resultados) else 1


def cargar_ofertas(ruta: Path) -> tuple[list[Oferta], list[str]]:
    """Lee un ofertas_*.json. Devuelve (ofertas válidas, avisos). Un archivo o una oferta mala no detiene nada."""
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [], [f"{ruta.name}: no se pudo leer ({e})"]
    if not isinstance(datos, list):
        return [], [f"{ruta.name}: se esperaba una lista de ofertas"]

    ofertas, avisos = [], []
    for i, item in enumerate(datos):
        try:
            ofertas.append(Oferta.desde_dict(item))
        except (TypeError, ValueError) as e:
            avisos.append(f"{ruta.name}, oferta #{i} descartada: {e}")
    return ofertas, avisos


def comando_filtrar(args: argparse.Namespace) -> int:
    fecha = args.fecha or date.today().isoformat()
    try:
        perfil = cargar_perfil(RUTA_PERFIL)
    except PerfilError as e:
        print(f"Error en el perfil: {e}", file=sys.stderr)
        return 1

    # Todas las descargas de ese día, de cualquier fuente y búsqueda. El glob es un primer filtro amplio;
    # fullmatch exige la forma exacta ofertas_<fuente>_<fecha>[_<búsqueda>].json, para que una búsqueda
    # que parezca una fecha (--search 2026-10-23 hecha otro día) no se cuele. "*.json" excluye los ".json.tmp".
    forma = re.compile(rf"ofertas_[a-z0-9]+_{re.escape(fecha)}(_[a-z0-9-]+)?\.json")
    archivos = sorted(p for p in DIR_DATA.glob(f"ofertas_*_{fecha}*.json") if forma.fullmatch(p.name))
    if not archivos:
        print(f"No hay descargas del {fecha}. Ejecuta primero: python -m buscador descargar", file=sys.stderr)
        return 1

    ofertas: list[Oferta] = []
    for archivo in archivos:
        nuevas, avisos = cargar_ofertas(archivo)
        ofertas += nuevas
        for aviso in avisos:
            print(f"Aviso: {aviso}")

    evaluaciones = evaluar_todas(ofertas, perfil, args.minimo)
    minimo = perfil.puntuacion_minima if args.minimo is None else args.minimo
    ruta_salida = DIR_DATA / f"filtradas_{fecha}.json"
    try:
        guardar_json([{**asdict(e.oferta), "puntuacion": e.puntuacion, "motivos": e.motivos}
                      for e in evaluaciones], ruta_salida)
    except OSError as e:
        print(f"Error al guardar: {e}", file=sys.stderr)
        return 1

    try:
        with Repositorio(ruta_db()) as repo:
            resumen = repo.guardar_evaluaciones(evaluaciones)
            retiradas = retirar_las_que_ya_no_cumplen(repo, perfil, minimo)
            pendientes = repo.listar(estado="nueva", limite=args.top)
    except AlmacenamientoError as e:
        print(f"Error en la base de datos: {e}", file=sys.stderr)
        return 1

    print(f"{len(evaluaciones)} de {len(ofertas)} ofertas llegan a {minimo} puntos: "
          f"{len(resumen.ids_nuevos)} nuevas, {resumen.actualizadas} ya conocidas.")
    if retiradas:
        print(f"{retiradas} pendientes ya no cumplen tu perfil y pasan a 'descartada' "
              "(míralas con: listar --estado descartada; recupéralas con: restaurar ID).")
    print(f"Pendientes de revisar (estado 'nueva'), mejores {args.top}:\n")
    imprimir_ofertas(pendientes, nuevas=resumen.ids_nuevos)
    print(f"\nRanking del día: {ruta_salida.relative_to(RAIZ)}")
    print("Descarta las que no te interesen con: python -m buscador descartar ID [ID ...]")
    return 0


def retirar_las_que_ya_no_cumplen(repo: Repositorio, perfil, minimo: int) -> int:
    """Re-evalúa TODAS las pendientes ('nueva') de la BD con el perfil actual; las que no lo cumplen → 'descartada'.

    Desde la BD y no solo desde la descarga del día: una oferta puede haber desaparecido del feed
    (Arbeitnow rota cada hora) y quedaría pendiente sin evaluar para siempre.
    Solo toca 'nueva': lo que ya estás preparando o aprobaste nunca se cambia solo. El motivo queda
    en el historial y se puede deshacer con `restaurar`.
    """
    retiradas = 0
    for guardada in repo.listar(estado="nueva"):
        oferta_id = guardada.id
        ev = evaluar(guardada.oferta, perfil)
        if not ev.descartada and ev.puntuacion >= minimo:
            continue
        # Si la descartó una regla, su motivo es el último; si no, fue por puntuación.
        motivo = ev.motivos[-1] if ev.descartada else f"puntuación {ev.puntuacion:+d} por debajo del mínimo {minimo}"
        repo.cambiar_estado(oferta_id, "descartada", nota=f"ya no cumple tu perfil: {motivo}")
        retiradas += 1
    return retiradas


def imprimir_ofertas(ofertas: list[OfertaGuardada], nuevas: set[int] = frozenset()) -> None:
    for g in ofertas:
        o = g.oferta
        marca = " [NUEVA]" if g.id in nuevas else ""
        # El #id es el número que el usuario escribe en `descartar`/`restaurar`.
        print(f"#{g.id:<5} [{g.puntuacion:+3d}] {_s(o.titulo[:60])} — {_s(o.empresa[:30])} ({_s(o.ubicacion[:30])}){marca}")
        print(f"       {', '.join(g.motivos)}")
        # Solo se muestra el enlace: postular es decisión (y acción) del usuario.
        print(f"       {_s(o.url)}")


def comando_listar(args: argparse.Namespace) -> int:
    # No creamos la BD solo por listar: si no existe, simplemente no hay nada.
    if not ruta_db().exists():
        print("No hay ofertas guardadas todavía. Ejecuta primero: python -m buscador filtrar")
        return 0
    estado = None if args.estado == "todas" else args.estado
    try:
        with Repositorio(ruta_db()) as repo:
            ofertas = repo.listar(estado=estado, limite=args.top)
    except AlmacenamientoError as e:
        print(f"Error en la base de datos: {e}", file=sys.stderr)
        return 1
    if not ofertas:
        print(f"No hay ofertas en estado '{args.estado}'.")
        return 0
    imprimir_ofertas(ofertas)
    return 0


def cambiar_estados(ids: list[int], nuevo: str, nota: str) -> int:
    """Aplica el cambio a cada id. Un id inválido se informa, pero no impide procesar los demás."""
    if not ruta_db().exists():
        print("No hay base de datos todavía. Ejecuta primero: python -m buscador filtrar", file=sys.stderr)
        return 1
    todo_bien = True
    try:
        with Repositorio(ruta_db()) as repo:
            for oferta_id in ids:
                try:
                    repo.cambiar_estado(oferta_id, nuevo, nota=nota)
                    print(f"#{oferta_id} → {nuevo}")
                except AlmacenamientoError as e:  # incluye OfertaNoEncontrada y TransicionInvalida
                    print(f"Error: {e}", file=sys.stderr)
                    todo_bien = False
    except AlmacenamientoError as e:
        print(f"Error en la base de datos: {e}", file=sys.stderr)
        return 1
    return 0 if todo_bien else 1


def comando_descartar(args: argparse.Namespace) -> int:
    return cambiar_estados(args.ids, "descartada", args.nota or "")


def comando_restaurar(args: argparse.Namespace) -> int:
    return cambiar_estados(args.ids, "nueva", "restaurada")


def _ruta_legible(ruta: Path) -> str:
    try:
        return str(ruta.relative_to(RAIZ))
    except ValueError:
        return str(ruta)


def _leer_texto(ruta: Path, que_es: str) -> str | None:
    try:
        return ruta.read_text(encoding="utf-8")
    except OSError:
        print(f"Falta {que_es}: {_ruta_legible(ruta)}", file=sys.stderr)
        return None


def comando_preparar(args: argparse.Namespace) -> int:
    cv = _leer_texto(RUTA_CV, "tu CV en texto (config/cv.md)")
    plantilla = _leer_texto(RUTA_PLANTILLA, "la plantilla de la carta")
    if cv is None or plantilla is None:
        return 1
    if not ruta_db().exists():
        print("No hay ofertas guardadas todavía. Ejecuta primero: python -m buscador filtrar", file=sys.stderr)
        return 1
    try:
        # Aquí se elige la estrategia (Claude, Simulado…) según config/ia.toml.
        proveedor = crear_proveedor(cargar_config_ia(RUTA_CONFIG_IA))
    except IAError as e:
        print(f"Error de configuración de la IA: {e}", file=sys.stderr)
        return 1

    todo_bien = True
    try:
        with Repositorio(ruta_db()) as repo:
            for oferta_id in args.ids:
                try:
                    prep = preparar_oferta(repo, proveedor, oferta_id, cv, plantilla)
                except (AlmacenamientoError, IAError) as e:
                    print(f"Error con la oferta #{oferta_id}: {e}", file=sys.stderr)
                    todo_bien = False
                    continue
                print(f"\n=== Oferta #{oferta_id} · borrador v{prep.version} · {prep.proveedor}/{prep.modelo}"
                      f" · {prep.tokens_entrada}+{prep.tokens_salida} tokens ===\n")
                print(_s(prep.texto))
    except AlmacenamientoError as e:
        print(f"Error en la base de datos: {e}", file=sys.stderr)
        return 1
    print("\nRevisa cada borrador. Para aprobarlo (solo tú, en tu terminal): python -m buscador aprobar ID")
    return 0 if todo_bien else 1


def confirmar(pregunta: str) -> bool:
    """Pide un 'si' explícito a una PERSONA.

    Si no hay una terminal interactiva (el comando lo lanza un script, un cron o una IA como
    Claude Code), se niega: aprobar y marcar como enviada son decisiones humanas, siempre.
    """
    if sys.stdin is None or not sys.stdin.isatty():
        print("Esta acción necesita la confirmación de una persona en una terminal interactiva. "
              "Ejecútala tú mismo en tu terminal.", file=sys.stderr)
        return False
    return input(f"{pregunta} Escribe 'si' para confirmar: ").strip().lower() in {"si", "sí"}


def comando_aprobar(args: argparse.Namespace) -> int:
    try:
        with Repositorio(ruta_db()) as repo:
            guardada = repo.obtener(args.id)
            prep = repo.ultima_preparacion(args.id)
            if guardada.estado != "preparada" or prep is None:
                print(f"La oferta #{args.id} está '{guardada.estado}': solo se aprueban ofertas con borrador "
                      f"(estado 'preparada'). Prepárala con: python -m buscador preparar {args.id}", file=sys.stderr)
                return 1
            # Se muestra EXACTAMENTE lo que se va a aprobar (saneado: sin escapes que oculten texto).
            print(f"Oferta #{args.id}: {_s(guardada.oferta.titulo)} — {_s(guardada.oferta.empresa)}")
            print(f"Borrador v{prep.version} ({prep.proveedor}/{prep.modelo}):\n")
            print(_s(prep.texto))
            print()
            # Avisos (no bloquean): posibles inyecciones desde la oferta o cifras inventadas.
            cv = RUTA_CV.read_text(encoding="utf-8") if RUTA_CV.exists() else ""
            for alerta in alertas_borrador(prep.texto, cv):
                print(f"⚠ ATENCIÓN: el borrador {_s(alerta)}")
            if not confirmar(f"¿Apruebas la versión {prep.version} de esta carta?"):
                print("No se aprobó nada.")
                return 1
            # version=…: si entretanto se generó otro borrador, no se aprueba uno que no has leído.
            repo.aprobar(args.id, confirmado_por_persona=True, version=prep.version)
    except AlmacenamientoError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"#{args.id} → aprobada. El envío lo haces TÚ (esta herramienta nunca envía nada): {_s(guardada.oferta.url)}")
    print(f"Cuando la hayas enviado, regístralo con: python -m buscador enviada {args.id}")
    return 0


def comando_enviada(args: argparse.Namespace) -> int:
    try:
        with Repositorio(ruta_db()) as repo:
            guardada = repo.obtener(args.id)
            if guardada.estado != "aprobada":
                print(f"La oferta #{args.id} está '{guardada.estado}': solo se marca como enviada una oferta aprobada.",
                      file=sys.stderr)
                return 1
            if repo.verificar_aprobacion(args.id) is False:
                print(f"El texto aprobado de la oferta #{args.id} no coincide con su huella: "
                      "la base de datos fue manipulada. No se registra nada.", file=sys.stderr)
                return 1
            if not confirmar(f"¿Confirmas que TÚ ya enviaste la postulación a {_s(guardada.oferta.empresa)}?"):
                print("No se registró nada.")
                return 1
            repo.marcar_enviada(args.id, nota=args.nota or "", confirmado_por_persona=True)
    except AlmacenamientoError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"#{args.id} → enviada. ¡Suerte!")
    return 0


def comando_ver(args: argparse.Namespace) -> int:
    try:
        with Repositorio(ruta_db()) as repo:
            g = repo.obtener(args.id)
            prep = repo.ultima_preparacion(args.id)
            historial = repo.historial(args.id)
            integra = repo.verificar_aprobacion(args.id)
    except AlmacenamientoError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    o = g.oferta
    print(f"#{g.id} [{g.estado}] {_s(o.titulo)} — {_s(o.empresa)} ({_s(o.ubicacion)})")
    print(f"Puntuación {g.puntuacion:+d}: {', '.join(g.motivos)}")
    print(_s(o.url))
    if integra is False:
        print("\n⚠ ATENCIÓN: el texto aprobado no coincide con su huella: la base de datos fue manipulada.")
    if prep:
        print(f"\n--- Borrador v{prep.version} ({prep.proveedor}/{prep.modelo}, {prep.generada_en}) ---\n")
        print(_s(prep.texto))
    else:
        print(f"\n(Sin borrador todavía: python -m buscador preparar {g.id})")
    if historial:
        print("\n--- Historial ---")
        for h in historial:
            print(f"{h.fecha}  {h.de} → {h.a}" + (f"  ({h.nota})" if h.nota else ""))
    return 0


def fecha_iso(valor: str) -> str:
    """Tipo de argparse: valida AAAA-MM-DD. Evita que algo como "*" se cuele en el patrón glob."""
    try:
        return date.fromisoformat(valor).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"fecha inválida {valor!r}, usa AAAA-MM-DD") from None


def main() -> None:
    parser = argparse.ArgumentParser(prog="buscador", description="Buscador de empleo automatizado")
    subcomandos = parser.add_subparsers(dest="comando", required=True)

    # Subcomandos: hoy solo "descargar"; luego vendrán "filtrar", "preparar", "aprobar"…
    p_descargar = subcomandos.add_parser("descargar", help="Descarga ofertas de las fuentes")
    p_descargar.add_argument("--search", help="Palabra clave, ej. kotlin, java, python")
    p_descargar.add_argument("--fuente", choices=list(FUENTES), help="Solo esta fuente (por defecto, todas)")
    p_descargar.add_argument("--force", action="store_true", help="Descargar aunque ya exista el archivo de hoy")
    p_descargar.set_defaults(funcion=comando_descargar)

    p_filtrar = subcomandos.add_parser("filtrar", help="Puntúa las ofertas descargadas contra tu perfil")
    p_filtrar.add_argument("--fecha", type=fecha_iso, help="Día de las descargas (AAAA-MM-DD); por defecto, hoy")
    p_filtrar.add_argument("--top", type=int, default=15, help="Cuántas mostrar en pantalla (por defecto 15)")
    p_filtrar.add_argument("--minimo", type=int, help="Puntuación mínima (por defecto, la de perfil.toml)")
    p_filtrar.set_defaults(funcion=comando_filtrar)

    p_listar = subcomandos.add_parser("listar", help="Muestra las ofertas guardadas por estado")
    p_listar.add_argument("--estado", choices=[*sorted(ESTADOS), "todas"], default="nueva",
                          help="Estado a mostrar (por defecto 'nueva')")
    p_listar.add_argument("--top", type=int, default=20, help="Cuántas mostrar (por defecto 20)")
    p_listar.set_defaults(funcion=comando_listar)

    p_descartar = subcomandos.add_parser("descartar", help="Marca ofertas como descartadas (no vuelven a salir)")
    p_descartar.add_argument("ids", type=int, nargs="+", metavar="ID", help="Números #ID que muestra filtrar/listar")
    p_descartar.add_argument("--nota", help="Motivo, queda en el historial (ej. 'pide alemán')")
    p_descartar.set_defaults(funcion=comando_descartar)

    p_restaurar = subcomandos.add_parser("restaurar", help="Devuelve ofertas descartadas a 'nueva'")
    p_restaurar.add_argument("ids", type=int, nargs="+", metavar="ID")
    p_restaurar.set_defaults(funcion=comando_restaurar)

    p_preparar = subcomandos.add_parser("preparar", help="La IA redacta un borrador de carta (queda 'preparada')")
    p_preparar.add_argument("ids", type=int, nargs="+", metavar="ID")
    p_preparar.set_defaults(funcion=comando_preparar)

    p_ver = subcomandos.add_parser("ver", help="Muestra una oferta, su último borrador y su historial")
    p_ver.add_argument("id", type=int, metavar="ID")
    p_ver.set_defaults(funcion=comando_ver)

    p_aprobar = subcomandos.add_parser("aprobar", help="Apruebas TÚ el último borrador (pide confirmación)")
    p_aprobar.add_argument("id", type=int, metavar="ID")
    p_aprobar.set_defaults(funcion=comando_aprobar)

    p_enviada = subcomandos.add_parser("enviada", help="Registra que TÚ enviaste la postulación (pide confirmación)")
    p_enviada.add_argument("id", type=int, metavar="ID")
    p_enviada.add_argument("--nota", help="Por dónde la enviaste, ej. 'formulario web'")
    p_enviada.set_defaults(funcion=comando_enviada)

    args = parser.parse_args()
    try:
        sys.exit(args.funcion(args))
    except KeyboardInterrupt:
        # Ctrl+C: salida limpia en vez de traceback. 130 es el código estándar para "interrumpido".
        print("\nInterrumpido por el usuario.", file=sys.stderr)
        sys.exit(130)
