"""
Punto de entrada de la sincronización Garmin -> Notion.

Uso:
    python main.py              # sincroniza todo
    python main.py --dry-run    # imprime lo que escribiría en Notion sin escribir
    python main.py --only sleep-data garmin-activities
"""
import argparse
import importlib.util
import os
import sys
import traceback

from common import get_garmin, get_notion

HERE = os.path.dirname(os.path.abspath(__file__))

# Mismo orden que el workflow original
SYNC_SCRIPTS = [
    "garmin-activities",
    "personal-records",
    "daily-steps",
    "sleep-data",
    "garmin-equipment",
]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="No escribe en Notion")
    parser.add_argument("--only", nargs="+", choices=SYNC_SCRIPTS, help="Ejecuta solo estos scripts")
    args = parser.parse_args()

    garmin = get_garmin()
    client = get_notion()

    failed = []
    for name in args.only or SYNC_SCRIPTS:
        print(f"\n===== {name} =====")
        try:
            load_script(name).main(garmin=garmin, client=client)
        except Exception:
            # Un fallo en un script no impide que se ejecuten los demás
            traceback.print_exc()
            failed.append(name)

    if failed:
        print(f"\nScripts con error: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
