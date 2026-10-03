"""
Backfill único de las propiedades nuevas de Actividades (Km corridos, desnivel,
temperatura, cadencia, GAP, FC máx.) para actividades ya existentes en Notion.

Solo actualiza esas propiedades (y Date, que viene de Garmin); no toca el resto.
Con --create-missing crea las actividades que no estén en Notion.

Uso:
    python backfill_km_corridos.py --dry-run                  # desde 2026-06-01
    python backfill_km_corridos.py --since 2022-09-01         # todo el histórico
    python backfill_km_corridos.py --force                    # recalcula aunque ya haya valor
    python backfill_km_corridos.py --create-missing           # crea las actividades que falten
"""
import argparse
import importlib.util
import os
from datetime import date

from common import get_garmin, get_notion, ensure_properties

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("garmin_activities", os.path.join(HERE, "garmin-activities.py"))
ga = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ga)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-06-01")
    parser.add_argument("--until", default=date.today().isoformat())
    parser.add_argument("--force", action="store_true", help="Recalcula Km corridos aunque ya tenga valor")
    parser.add_argument("--create-missing", action="store_true", help="Crea en Notion las actividades que falten")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    database_id = os.getenv("NOTION_DB_ID")
    garmin = get_garmin()
    client = get_notion()
    ensure_properties(client, database_id, ga.NEW_ACTIVITY_PROPERTIES)

    activities = garmin.get_activities_by_date(args.since, args.until)
    activities.sort(key=lambda a: a.get("startTimeGMT") or "")
    print(f"{len(activities)} actividades en Garmin entre {args.since} y {args.until}")

    rows, missing = [], []
    for activity in activities:
        name = ga.format_entertainment(activity.get("activityName", "Unnamed Activity"))
        activity_type, _ = ga.format_activity_type(activity.get("activityType", {}).get("typeKey", "Unknown"), name)
        existing = ga.activity_exists(client, database_id, activity.get("activityId"),
                                      activity.get("startTimeGMT"), activity_type, name)
        if not existing:
            missing.append(f"{activity.get('startTimeGMT')} {name}")
            if args.create_missing:
                km = ga.compute_km_corridos(garmin, activity, activity_type)
                ga.create_activity(client, database_id, activity, ga.get_training_type(activity_type, name), km)
                print(f"Creada: {activity_type} - {name} (Km corridos {km})")
            continue

        properties = ga.new_metrics_properties(activity)
        # La fecha viene de Garmin (no se edita a mano). Corrige filas en las que
        # el antiguo upsert por día+tipo mezcló dos actividades.
        properties["Date"] = {"date": {"start": activity.get("startTimeGMT")}}
        current_km = (existing["properties"].get("Km corridos") or {}).get("number")
        km = current_km
        if current_km is None or args.force:
            km = ga.compute_km_corridos(garmin, activity, activity_type)
            properties["Km corridos"] = {"number": km}
        # Rellena el Activity Id en filas antiguas que no lo tenían
        if not (existing["properties"].get("Activity Id") or {}).get("number"):
            properties["Activity Id"] = {"number": activity.get("activityId")}

        client.pages.update(page_id=existing["id"], properties=properties)
        distance = round((activity.get("distance") or 0) / 1000, 2)
        rows.append((activity.get("startTimeGMT", "")[:10], activity_type, name, distance, km))

    print(f"\n{'Fecha':10}  {'Tipo':10}  {'Km':>7}  {'Corridos':>8}  Nombre")
    for d, t, n, dist, km in rows:
        flag = "  <-- mixta" if t == "Running" and km is not None and km < dist - 0.05 else ""
        print(f"{d:10}  {t[:10]:10}  {dist:7.2f}  {km if km is not None else '-':>8}  {n}{flag}")

    run_total = sum(r[4] or 0 for r in rows)
    print(f"\nActualizadas: {len(rows)} · Km corridos totales: {run_total:.1f}")
    if missing:
        print(f"No encontradas en Notion ({len(missing)}){', creadas' if args.create_missing else ', no se crean'}:")
        for m in missing:
            print(f"  {m}")


if __name__ == "__main__":
    main()
