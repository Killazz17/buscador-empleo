"""Tests de configuración del repositorio: que nada personal acabe en git y que las dependencias estén fijadas.

El proyecto aún no es un repositorio git, así que se comprueban los patrones de .gitignore
con fnmatch (una aproximación suficiente a cómo los interpreta git para estos casos).
"""

import fnmatch
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def ignorado(ruta: str, patrones: list[str]) -> bool:
    partes = ruta.split("/")
    for patron in patrones:
        p = patron.rstrip("/")
        if "/" in p:
            if fnmatch.fnmatch(ruta, p) or ruta.startswith(p + "/"):
                return True
        elif any(fnmatch.fnmatch(parte, p) for parte in partes):
            return True
    return False


class TestGitignore(unittest.TestCase):
    def setUp(self):
        lineas = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.patrones = [l.strip() for l in lineas if l.strip() and not l.startswith("#")]

    def test_datos_personales_y_secretos_no_se_suben(self):
        for ruta in ("config/cv.md", "config/cv-en.md", "config/cv.txt", "cv.md", "sebastian-cv.pdf",
                     "data/buscador.db", "otra/carpeta/buscador.db", "copia.sqlite3", "data/buscador.db-journal",
                     ".env", ".env.local", ".coverage", ".venv/lib/x.py"):
            with self.subTest(ruta=ruta):
                self.assertTrue(ignorado(ruta, self.patrones), f"{ruta} se subiría a git")

    def test_el_codigo_y_la_config_publica_si_se_suben(self):
        for ruta in ("src/buscador/cli.py", "config/perfil.toml", "config/ia.toml", "prompts/carta.md",
                     "requirements.txt", "CLAUDE.md"):
            with self.subTest(ruta=ruta):
                self.assertFalse(ignorado(ruta, self.patrones), f"{ruta} quedaría fuera de git")


class TestEmpaquetado(unittest.TestCase):
    def test_pyproject_declara_el_comando_buscador(self):
        # `pip install -e .` crea el comando `buscador`, usable desde cualquier carpeta.
        import tomllib
        datos = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(datos["project"]["scripts"]["buscador"], "buscador.cli:main")
        self.assertEqual(datos["tool"]["setuptools"]["packages"]["find"]["where"], ["src"])
        self.assertEqual(datos["project"]["dependencies"], [])  # el núcleo no necesita nada
        self.assertIn("anthropic==2.2.0", datos["project"]["optional-dependencies"]["claude"])


class TestDependencias(unittest.TestCase):
    def test_versiones_fijadas(self):
        # Una versión exacta: actualizar es una decisión, no algo que pase solo en cada instalación.
        lineas = [l.strip() for l in (RAIZ / "requirements.txt").read_text(encoding="utf-8").splitlines()
                  if l.strip() and not l.startswith("#")]
        self.assertTrue(lineas)
        for linea in lineas:
            self.assertIn("==", linea, f"versión sin fijar: {linea}")


if __name__ == "__main__":
    unittest.main()
