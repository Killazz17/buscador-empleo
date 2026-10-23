# Permite ejecutar el paquete con `python -m buscador`.
from buscador.cli import main

# El guard evita que importar este módulo (p. ej. desde un test) lance la CLI.
if __name__ == "__main__":
    main()
