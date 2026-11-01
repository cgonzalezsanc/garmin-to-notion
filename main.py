"""
Punto de entrada de la sincronización Garmin -> Notion.

Uso:
    python main.py              # sincroniza (marcas y equipamiento, una vez al día)
    python main.py --dry-run    # imprime lo que escribiría en Notion sin escribir
    python main.py --only sleep-data garmin-activities
"""
import argparse
import importlib.util
import os
import sys
import traceback

from common import get_data_source_id, get_garmin, get_notion, now_madrid

HERE = os.path.dirname(os.path.abspath(__file__))

# Mismo orden que el workflow original
SYNC_SCRIPTS = [
    "garmin-activities",
    "personal-records",
    "daily-steps",
    "sleep-data",
    "garmin-equipment",
]

# Cambian poco y gastan ~100 peticiones a Notion: solo una vez al día
DAILY_SCRIPTS = {"personal-records", "garmin-equipment"}


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def daily_scripts_due(client):
    """
    True si alguna zapatilla activa tiene Fecha anterior a hoy. garmin-equipment
    pone la fecha del día en cada zapatilla, así que una pasada cortada a medias
    se repite en la siguiente ejecución. Ante cualquier duda, se ejecutan.
    """
    database_id = os.getenv("NOTION_EQ_DB_ID")
    if not database_id:
        return True
    try:
        pending = client.data_sources.query(
            data_source_id=get_data_source_id(client, database_id),
            page_size=1,
            filter={"and": [
                {"property": "Estado", "select": {"equals": "active"}},
                {"property": "Fecha", "date": {"before": now_madrid().date().isoformat()}},
            ]},
        )
        return bool(pending.get("results"))
    except Exception as e:
        print(f"Aviso: no se pudo comprobar si tocan marcas y equipamiento ({e}); se ejecutan")
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="No escribe en Notion")
    parser.add_argument("--only", nargs="+", choices=SYNC_SCRIPTS, help="Ejecuta solo estos scripts")
    args = parser.parse_args()

    garmin = get_garmin()
    client = get_notion()

    scripts = args.only
    if not scripts:
        scripts = SYNC_SCRIPTS
        if not daily_scripts_due(client):
            print("Marcas y equipamiento ya actualizados hoy: se omiten.")
            scripts = [name for name in SYNC_SCRIPTS if name not in DAILY_SCRIPTS]

    failed = []
    for name in scripts:
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
