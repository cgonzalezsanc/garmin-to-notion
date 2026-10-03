"""
Informe diario de entrenamiento.

Fase 3: calcula de forma determinista las métricas del día a partir de Notion
(y de Garmin para la línea base de HRV) y las imprime como JSON en el log.

Uso:
    python daily_report.py --dry-run
    python daily_report.py --fecha 2026-10-03     # calcular para otro día
"""
import argparse
import json
import os
import statistics
from datetime import date, datetime, timedelta, timezone

from common import MADRID_TZ, get_garmin, get_notion, now_madrid, query_all

# IDs de data sources (API 2025-09-03). Se pueden sobrescribir por entorno.
DS_ACTIVIDADES = os.getenv("NOTION_DS_ACTIVIDADES", "251bb6e9-4ddc-81e5-9a4c-000b38da493d")
DS_SUENO = os.getenv("NOTION_DS_SUENO", "251bb6e9-4ddc-81c0-93bc-000b03f4370d")
DS_EJERCICIOS = os.getenv("NOTION_DS_EJERCICIOS", "2c9bb6e9-4ddc-8045-8947-000b79ee7e74")
DS_CARRERAS = os.getenv("NOTION_DS_CARRERAS", "260bb6e9-4ddc-8004-a2f6-000be5be46de")
DS_PLAN = os.getenv("NOTION_DS_PLAN", "59f320f1-acce-40f0-bdd9-ffe2bbcdefd0")
DS_INFORMES = os.getenv("NOTION_DS_INFORMES", "f2cc4735-d45f-471b-9dfb-de87cf944b52")

LEG_GROUPS = {"Cuadriceps", "Cuádriceps", "Isquiotibiales", "Glúteos", "Gemelos"}


# ---------------------------------------------------------------------------
# Lectura de propiedades de Notion
# ---------------------------------------------------------------------------

def prop(page, name):
    """Valor 'plano' de una propiedad de Notion (texto, número, fecha, select...)."""
    p = page["properties"].get(name)
    if not p:
        return None
    t = p["type"]
    v = p.get(t)
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in v or []) or None
    if t in ("select", "status"):
        return v["name"] if v else None
    if t == "multi_select":
        return [x["name"] for x in v or []]
    if t == "date":
        return v["start"] if v else None
    if t == "relation":
        return [x["id"] for x in v or []]
    return v  # number, checkbox, url...


def to_madrid_date(iso):
    """'2026-09-26T06:16:00.000+00:00' o '2026-09-26' -> date en hora de Madrid."""
    if not iso:
        return None
    if len(iso) == 10:
        return date.fromisoformat(iso)
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:  # sin zona: el script guarda startTimeGMT, es decir, UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MADRID_TZ).date()


def date_filter(prop_name, on_or_after=None, on_or_before=None):
    conds = []
    if on_or_after:
        conds.append({"property": prop_name, "date": {"on_or_after": on_or_after.isoformat()}})
    if on_or_before:
        conds.append({"property": prop_name, "date": {"on_or_before": on_or_before.isoformat()}})
    return {"and": conds} if len(conds) > 1 else conds[0]


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

def compute_metrics(client, garmin, today):
    yesterday = today - timedelta(days=1)
    warnings = []

    # --- Actividades (29 días hacia atrás + hoy; margen de 1 día por la zona horaria)
    acts = query_all(client, DS_ACTIVIDADES,
                     filter=date_filter("Date", on_or_after=today - timedelta(days=30)))
    activities = []
    for a in acts:
        d = to_madrid_date(prop(a, "Date"))
        if d is None or d < today - timedelta(days=28) or d > today:
            continue
        activities.append({"page": a, "date": d})

    def km_corridos(a):
        v = prop(a["page"], "Km corridos")
        if v is None:
            # Sin calcular todavía: solo es fiable para lo que no es running
            if prop(a["page"], "Activity Type") != "Running":
                return 0
            warnings.append(f"Actividad sin 'Km corridos': {prop(a['page'], 'Activity Name')} ({a['date']})")
            return prop(a["page"], "Distance (km)") or 0
        return v

    def km_between(start, end):
        return round(sum(km_corridos(a) for a in activities if start <= a["date"] <= end), 2)

    km_7d = km_between(today - timedelta(days=7), yesterday)
    km_28d = km_between(today - timedelta(days=28), yesterday)
    km_media_28d = round(km_28d / 4, 2)
    ratio = round(km_7d / km_media_28d, 2) if km_media_28d else None

    run_days = {a["date"] for a in activities if km_corridos(a) > 0}
    streak, d = 0, yesterday
    while d in run_days:
        streak += 1
        d -= timedelta(days=1)

    actividades_7d = []
    for a in sorted(activities, key=lambda x: prop(x["page"], "Date") or ""):
        if a["date"] < today - timedelta(days=7):
            continue
        p = a["page"]
        actividades_7d.append({
            "fecha": a["date"].isoformat(),
            "nombre": prop(p, "Activity Name"),
            "tipo": prop(p, "Activity Type"),
            "train_type": prop(p, "Train Type"),
            "km": prop(p, "Distance (km)"),
            "km_corridos": prop(p, "Km corridos"),
            "duracion_min": prop(p, "Duration (min)"),
            "ritmo": prop(p, "Avg Pace"),
            "gap": prop(p, "GAP"),
            "fc_media": prop(p, "Avg HR"),
            "fc_max": prop(p, "Max HR"),
            "desnivel_pos_m": prop(p, "Elev Gain (m)"),
            "desnivel_neg_m": prop(p, "Elev Loss (m)"),
            "temp_c": prop(p, "Temp (°C)"),
            "training_effect": prop(p, "Training Effect"),
            "aerobic_te": prop(p, "Aerobic"),
            "anaerobic_te": prop(p, "Anaerobic"),
        })

    # --- Sueño y recuperación
    sleep_rows = query_all(client, DS_SUENO,
                           filter=date_filter("Long Date", on_or_after=today - timedelta(days=28), on_or_before=today))
    by_day = {}
    for s in sleep_rows:
        d = prop(s, "Long Date")
        if d:
            by_day[date.fromisoformat(d[:10])] = s
    hoy = by_day.get(today)
    rhr_prev = [prop(s, "Resting HR") for d, s in by_day.items() if d < today]
    rhr_prev = [v for v in rhr_prev if v]

    hrv_baseline = None
    try:
        summary = (garmin.get_hrv_data(today.isoformat()) or {}).get("hrvSummary") or {}
        b = summary.get("baseline") or {}
        if b:
            hrv_baseline = {"balanced_low": b.get("balancedLow"), "balanced_upper": b.get("balancedUpper"),
                            "weekly_avg": summary.get("weeklyAvg")}
    except Exception as e:
        warnings.append(f"No se pudo leer la línea base de HRV de Garmin: {e}")

    # --- Gimnasio de pierna en las últimas 48 h
    since_48h = now_madrid() - timedelta(hours=48) if today == now_madrid().date() \
        else datetime.combine(today, datetime.min.time(), MADRID_TZ) - timedelta(hours=48)
    ex_rows = query_all(client, DS_EJERCICIOS,
                        filter=date_filter("Fecha", on_or_after=since_48h.date() - timedelta(days=1)))
    leg_ex = []
    for e in ex_rows:
        start = prop(e, "Fecha")
        if not start:
            continue
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if dt.tzinfo is None:  # Fecha = startTimeGMT de la actividad
            dt = dt.replace(tzinfo=timezone.utc)
        groups = set(prop(e, "Grupo muscular") or [])
        if dt >= since_48h and groups & LEG_GROUPS:
            leg_ex.append(f"{prop(e, 'Nombre')} ({dt.astimezone(MADRID_TZ):%d/%m %H:%M})")

    # --- Plan de entrenamiento
    plan_rows = query_all(client, DS_PLAN,
                          filter=date_filter("Fecha", on_or_after=today, on_or_before=today + timedelta(days=6)),
                          sorts=[{"property": "Fecha", "direction": "ascending"}])

    def session(p):
        return {
            "id": p["id"],
            "fecha": (prop(p, "Fecha") or "")[:10],
            "sesion": prop(p, "Sesión"),
            "tipo": prop(p, "Tipo"),
            "km_objetivo": prop(p, "Km objetivo"),
            "fc_techo": prop(p, "FC techo"),
            "ritmo_objetivo": prop(p, "Ritmo objetivo"),
            "detalle": prop(p, "Detalle"),
            "estado": prop(p, "Estado"),
            "bloque": prop(p, "Bloque"),
            "notas": prop(p, "Notas"),
        }
    plan_7d = [session(p) for p in plan_rows]
    sesion_hoy = [s for s in plan_7d if s["fecha"] == today.isoformat()]

    # --- Próxima carrera
    races = query_all(client, DS_CARRERAS, filter=date_filter("Fecha", on_or_after=today),
                      sorts=[{"property": "Fecha", "direction": "ascending"}])
    proxima = None
    if races:
        r = races[0]
        rd = to_madrid_date(prop(r, "Fecha"))
        proxima = {"nombre": prop(r, "Nombre"), "fecha": rd.isoformat() if rd else None,
                   "dias": (rd - today).days if rd else None, "distancia": prop(r, "Distancia (km)"),
                   "ubicacion": prop(r, "Ubicación")}

    metrics = {
        "fecha": today.isoformat(),
        "km_corridos_7d": km_7d,
        "km_corridos_media_28d": km_media_28d,
        "ratio_carga": ratio,
        "dias_seguidos_corriendo": streak,
        "fc_reposo_hoy": prop(hoy, "Resting HR") if hoy else None,
        "fc_reposo_base_28d": statistics.median(rhr_prev) if rhr_prev else None,
        "hrv_hoy": prop(hoy, "HRV (ms)") if hoy else None,
        "hrv_status": prop(hoy, "HRV status") if hoy else None,
        "hrv_baseline_garmin": hrv_baseline,
        "sueno_h": prop(hoy, "Total Sleep (h)") if hoy else None,
        "sueno_score": prop(hoy, "Score") if hoy else None,
        "body_battery_manana": prop(hoy, "Body Battery mañana") if hoy else None,
        "training_readiness": prop(hoy, "Training Readiness") if hoy else None,
        "carga_aguda_garmin": prop(hoy, "Carga aguda") if hoy else None,
        "training_status_garmin": prop(hoy, "Training Status") if hoy else None,
        "pierna_48h": bool(leg_ex),
        "ejercicios_pierna_48h": leg_ex,
        "sesion_hoy": sesion_hoy,
        "plan_7d": plan_7d,
        "actividades_7d": actividades_7d,
        "proxima_carrera": proxima,
    }
    missing = [k for k in ("sueno_h", "hrv_hoy", "fc_reposo_hoy") if metrics[k] is None]
    metrics["datos_incompletos"] = bool(missing)
    metrics["datos_que_faltan"] = missing
    metrics["avisos"] = warnings
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fecha", help="Fecha del informe (YYYY-MM-DD). Por defecto, hoy en Madrid.")
    args = parser.parse_args()

    today = date.fromisoformat(args.fecha) if args.fecha else now_madrid().date()
    garmin = get_garmin()
    client = get_notion()

    metrics = compute_metrics(client, garmin, today)
    print("MÉTRICAS DEL INFORME:")
    print(json.dumps(metrics, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
