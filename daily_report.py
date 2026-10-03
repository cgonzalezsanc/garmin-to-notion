"""
Informe diario de entrenamiento.

1. Calcula de forma determinista las métricas del día a partir de Notion (y de
   Garmin para la línea base de HRV) y las imprime como JSON en el log.
2. Aplica reglas heurísticas (sacadas de la página "Contexto entrenador") para
   el semáforo y la sesión ajustada, y escribe una página en "Informes diarios".
3. Una rutina de Claude (suscripción) revisa después las páginas con
   "Revisado Claude" desmarcado y redacta el informe final.

Cuándo genera informe (hora de Madrid), salvo que se fuerce con --tipo:
- 08:00-15:59  Diario si aún no existe, o si existe con "Datos incompletos"
               (lo regenera en la misma página). Si existe completo, no hace nada.
- 20:00-23:59  Pre-carrera, solo si en el Plan hay Competición mañana.

Uso:
    python daily_report.py --dry-run
    python daily_report.py --tipo diario            # forzar (p.ej. lanzado a mano)
    python daily_report.py --fecha 2026-10-03 --tipo diario
"""
import argparse
import json
import os
import statistics
import traceback
from datetime import date, datetime, timedelta, timezone

from common import MADRID_TZ, get_garmin, get_notion, now_madrid, query_all, ensure_properties

# IDs de data sources (API 2025-09-03). Se pueden sobrescribir por entorno.
DS_ACTIVIDADES = os.getenv("NOTION_DS_ACTIVIDADES", "251bb6e9-4ddc-81e5-9a4c-000b38da493d")
DS_SUENO = os.getenv("NOTION_DS_SUENO", "251bb6e9-4ddc-81c0-93bc-000b03f4370d")
DS_EJERCICIOS = os.getenv("NOTION_DS_EJERCICIOS", "2c9bb6e9-4ddc-8045-8947-000b79ee7e74")
DS_CARRERAS = os.getenv("NOTION_DS_CARRERAS", "260bb6e9-4ddc-8004-a2f6-000be5be46de")
DS_PLAN = os.getenv("NOTION_DS_PLAN", "59f320f1-acce-40f0-bdd9-ffe2bbcdefd0")
DS_INFORMES = os.getenv("NOTION_DS_INFORMES", "f2cc4735-d45f-471b-9dfb-de87cf944b52")
DB_INFORMES = os.getenv("NOTION_REPORTS_DB_ID", "47ba50c01a9c49e2b0e13dc6c2973b7e")

# Usuario de Notion al que se menciona para que llegue la notificación
NOTIFY_USER_ID = os.getenv("NOTION_NOTIFY_USER_ID", "250d872b-594c-818a-975e-000251ce5736")

QUALITY_TYPES = {"Umbral", "Series", "Tempo", "Competición", "Tirada larga"}
RUN_TYPES = QUALITY_TYPES | {"Rodaje"}

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


def minutes_above(garmin, activity_id, techo):
    """
    Minutos con FC por encima de `techo` en una actividad.
    1) Serie temporal (FC muestra a muestra; cuenta tiempo de cronómetro, sin pausas).
    2) Si no hay serie temporal: suma de la duración de las vueltas con FC media > techo.
    Devuelve (minutos, método) o (None, motivo).
    """
    try:
        details = garmin.get_activity_details(str(activity_id), maxchart=100000)
        keys = {m["key"]: m["metricsIndex"] for m in details["metricDescriptors"]}
        i_hr, i_dur = keys["directHeartRate"], keys["sumDuration"]
        rows = [r["metrics"] for r in details.get("activityDetailMetrics", [])]
        secs = 0.0
        for prev, cur in zip(rows, rows[1:]):
            if cur[i_hr] is None or cur[i_dur] is None or prev[i_dur] is None:
                continue
            if cur[i_hr] > techo:
                secs += max(cur[i_dur] - prev[i_dur], 0)
        if rows:
            return round(secs / 60, 1), "serie_temporal"
    except Exception as e:
        print(f"Aviso: sin serie temporal de FC para {activity_id} ({e}); se usan las vueltas")
    try:
        laps = garmin.get_activity_splits(str(activity_id)).get("lapDTOs") or []
        secs = sum(l.get("duration") or 0 for l in laps if (l.get("averageHR") or 0) > techo)
        return round(secs / 60, 1), "vueltas"
    except Exception as e:
        return None, f"sin datos de FC ({e})"


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

    # Sesiones del Plan de los últimos 7 días, para el tiempo sobre el techo de FC
    past_plan = query_all(client, DS_PLAN,
                          filter=date_filter("Fecha", on_or_after=today - timedelta(days=7), on_or_before=today))

    def plan_session_for(activity_page, day):
        """Sesión enlazada a la actividad; si no hay, la de carrera de ese mismo día."""
        linked = [s for s in past_plan if activity_page["id"] in (prop(s, "Actividad") or [])]
        same_day = [s for s in past_plan if (prop(s, "Fecha") or "")[:10] == day.isoformat()
                    and prop(s, "Tipo") in RUN_TYPES]
        return (linked or same_day or [None])[0], bool(linked)

    actividades_7d = []
    for a in sorted(activities, key=lambda x: prop(x["page"], "Date") or ""):
        if a["date"] < today - timedelta(days=7):
            continue
        p = a["page"]
        sobre_techo = None
        if prop(p, "Activity Type") == "Running":
            session, enlazada = plan_session_for(p, a["date"])
            techo = prop(session, "FC techo") if session else None
            if techo and prop(p, "Activity Id"):
                minutos, metodo = minutes_above(garmin, int(prop(p, "Activity Id")), techo)
                sobre_techo = {"minutos": minutos, "techo": techo, "metodo": metodo,
                               "sesion_plan": prop(session, "Sesión"), "sesion_enlazada": enlazada}
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
            "sensacion_termica_c": prop(p, "Sensación térmica (°C)"),
            "humedad_pct": prop(p, "Humedad (%)"),
            "viento_kmh": prop(p, "Viento (km/h)"),
            "sensacion": prop(p, "Sensación"),
            "rpe": prop(p, "RPE"),
            "min_sobre_techo": sobre_techo,
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


# ---------------------------------------------------------------------------
# Reglas (heurísticas, no validadas; ver página "Contexto entrenador")
# ---------------------------------------------------------------------------

DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def session_label(s):
    if not s:
        return "Sin sesión planificada"
    parts = [s["sesion"] or s["tipo"] or "Sesión"]
    if s.get("fc_techo"):
        parts.append(f"FC techo {s['fc_techo']}")
    if s.get("ritmo_objetivo"):
        parts.append(f"ritmo {s['ritmo_objetivo']}")
    return " · ".join(parts)


def apply_rules(m, tipo):
    """Devuelve (semaforo, sesion_ajustada, motivos, avisos). Solo heurísticas explícitas."""
    red, yellow, info = [], [], []
    sesion = m["sesion_hoy"][0] if m["sesion_hoy"] else None
    tipo_sesion = sesion["tipo"] if sesion else None

    rhr, base = m["fc_reposo_hoy"], m["fc_reposo_base_28d"]
    if rhr and base:
        diff = rhr - base
        if diff >= 7:
            red.append(f"FC reposo {rhr} ppm, +{diff:g} sobre la base de 28 días ({base:g}).")
        elif diff >= 4:
            yellow.append(f"FC reposo {rhr} ppm, +{diff:g} sobre la base de 28 días ({base:g}).")

    status = (m["hrv_status"] or "").lower()
    if status in ("low", "poor"):
        red.append(f"HRV {m['hrv_hoy']} ms con estado '{m['hrv_status']}' según Garmin.")
    elif status == "unbalanced":
        yellow.append(f"HRV {m['hrv_hoy']} ms con estado 'Unbalanced' según Garmin.")
    else:
        b = m["hrv_baseline_garmin"] or {}
        if m["hrv_hoy"] and b.get("balanced_low") and m["hrv_hoy"] < b["balanced_low"]:
            yellow.append(f"HRV {m['hrv_hoy']} ms, por debajo de tu rango normal ({b['balanced_low']}-{b['balanced_upper']}).")

    sleep = m["sueno_h"]
    if sleep is not None:
        if sleep < 5:
            red.append(f"Solo {sleep} h de sueño.")
        elif sleep < 6.5:
            yellow.append(f"Sueño corto: {sleep} h.")

    tr = m["training_readiness"]
    if tr is not None:
        if tr < 25:
            red.append(f"Training Readiness {tr} (muy baja).")
        elif tr < 50:
            yellow.append(f"Training Readiness {tr} (baja).")

    if m["ratio_carga"] and m["ratio_carga"] > 1.3:
        yellow.append(f"Carga de 7 días {m['km_corridos_7d']} km vs media {m['km_corridos_media_28d']} km/semana "
                      f"(ratio {m['ratio_carga']}). Heurístico, evidencia débil.")

    if m["dias_seguidos_corriendo"] >= 3 and tipo_sesion in RUN_TYPES:
        yellow.append(f"Llevas {m['dias_seguidos_corriendo']} días seguidos corriendo (regla: máximo 3 en descarga).")

    if m["pierna_48h"] and tipo_sesion in QUALITY_TYPES:
        yellow.append("Gimnasio de pierna en las últimas 48 h antes de una sesión de calidad "
                      "(la fatiga muscular local no se ve en la FC).")
    elif m["pierna_48h"]:
        info.append("Hubo gimnasio de pierna en las últimas 48 h.")

    if m["datos_incompletos"]:
        info.append(f"Faltan datos: {', '.join(m['datos_que_faltan'])}. No se infieren.")

    if not any(m[k] is not None for k in ("sueno_h", "hrv_hoy", "fc_reposo_hoy", "training_readiness")):
        semaforo = "Sin datos"
    elif red:
        semaforo = "Rojo"
    elif yellow:
        semaforo = "Amarillo"
    else:
        semaforo = "Verde"

    planned = session_label(sesion)
    if not sesion or tipo_sesion in ("Descanso", "Gimnasio"):
        ajustada = planned
    elif semaforo == "Rojo":
        ajustada = "Descanso, o como mucho 30-40' muy suaves por debajo de 130 ppm"
    elif semaforo == "Amarillo" and tipo_sesion in QUALITY_TYPES:
        km = f"{sesion['km_objetivo']:g} km" if sesion.get("km_objetivo") else "rodaje"
        ajustada = f"Cambiar la calidad por un rodaje fácil ({km}) con techo de 142 ppm"
    else:
        ajustada = planned

    return semaforo, ajustada, red + yellow, info


# ---------------------------------------------------------------------------
# Escritura en Notion
# ---------------------------------------------------------------------------

def _rt(text):
    """Texto -> rich_text de Notion, troceado en bloques de 2000 caracteres."""
    return [{"type": "text", "text": {"content": text[i:i + 2000]}} for i in range(0, len(text), 2000)] or []


def markdown_to_blocks(md):
    """Conversión mínima: '## ' títulos, '- ' viñetas, resto párrafos."""
    blocks = []
    for line in md.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("### "):
            blocks.append({"type": "heading_3", "heading_3": {"rich_text": _rt(line[4:])}})
        elif line.startswith("## "):
            blocks.append({"type": "heading_2", "heading_2": {"rich_text": _rt(line[3:])}})
        elif line.startswith(("- ", "* ")):
            blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rt(line[2:])}})
        else:
            blocks.append({"type": "paragraph", "paragraph": {"rich_text": _rt(line)}})
    return blocks


def build_body(m, tipo, semaforo, ajustada, motivos, info, error=None):
    sesion = m["sesion_hoy"][0] if m["sesion_hoy"] else None
    lines = ["## Estado", f"Semáforo **{semaforo}** (reglas automáticas, heurísticas)."]
    lines.append(
        f"Sueño {m['sueno_h'] if m['sueno_h'] is not None else '—'} h (score {m['sueno_score'] or '—'}) · "
        f"HRV {m['hrv_hoy'] or '—'} ms ({m['hrv_status'] or '—'}) · "
        f"FC reposo {m['fc_reposo_hoy'] or '—'} (base {m['fc_reposo_base_28d'] or '—'}) · "
        f"Readiness {m['training_readiness'] if m['training_readiness'] is not None else '—'} · "
        f"Body Battery {m['body_battery_manana'] if m['body_battery_manana'] is not None else '—'}")
    lines.append(f"Km corridos 7 d: {m['km_corridos_7d']} · media 28 d: {m['km_corridos_media_28d']} km/sem · "
                 f"días seguidos corriendo: {m['dias_seguidos_corriendo']}")
    lines.append("## Motivos")
    lines += [f"- {x}" for x in motivos] or ["- Ninguna regla de alerta se ha disparado."]
    if tipo == "Pre-carrera":
        lines.append("## Carrera de mañana")
        manana = [s for s in m["plan_7d"] if s["tipo"] == "Competición"]
        for s in manana[:1]:
            lines.append(f"- {session_label(s)}")
            if s.get("detalle"):
                lines.append(f"- {s['detalle']}")
            if s.get("fc_techo"):
                lines.append(f"- Carrera de entrenamiento: no pasar de {s['fc_techo']} ppm.")
    else:
        lines.append("## Sesión de hoy")
        lines.append(f"- Planificada: {session_label(sesion)}")
        lines.append(f"- Ajustada: {ajustada}")
        if sesion and sesion.get("detalle"):
            lines.append(f"- {sesion['detalle']}")
    if info or m["avisos"] or error:
        lines.append("## Avisos")
        lines += [f"- {x}" for x in info + m["avisos"]]
        if error:
            lines.append(f"- Error al generar el informe: {error}")
    if m["proxima_carrera"]:
        r = m["proxima_carrera"]
        lines.append(f"Próxima carrera: {r['nombre']} ({r['fecha']}, faltan {r['dias']} días).")
    return "\n".join(lines).replace("**", "")


def find_report(client, day, tipo):
    rows = client.data_sources.query(
        data_source_id=DS_INFORMES,
        filter={"and": [
            {"property": "Fecha", "date": {"equals": day.isoformat()}},
            {"property": "Tipo", "select": {"equals": tipo}},
        ]},
    ).get("results", [])
    return rows[0] if rows else None


def write_report(client, existing, day, tipo, m, semaforo, ajustada, title, body):
    sesion = m["sesion_hoy"][0] if m["sesion_hoy"] else None
    properties = {
        "Informe": {"title": _rt(title)},
        "Fecha": {"date": {"start": day.isoformat()}},
        "Semáforo": {"select": {"name": semaforo}},
        "Tipo": {"select": {"name": tipo}},
        "Sesión planificada": {"rich_text": _rt(session_label(sesion) if sesion else "")},
        "Sesión ajustada": {"rich_text": _rt(ajustada)},
        "Km corridos 7d": {"number": m["km_corridos_7d"]},
        "Km corridos media 28d": {"number": m["km_corridos_media_28d"]},
        "FC reposo": {"number": m["fc_reposo_hoy"]},
        "FC reposo base 28d": {"number": m["fc_reposo_base_28d"]},
        "HRV": {"number": m["hrv_hoy"]},
        "Sueño (h)": {"number": m["sueno_h"]},
        "Training Readiness": {"number": m["training_readiness"]},
        "Días seguidos corriendo": {"number": m["dias_seguidos_corriendo"]},
        "Datos incompletos": {"checkbox": m["datos_incompletos"]},
        "Revisado Claude": {"checkbox": False},
    }

    mention = {"type": "paragraph", "paragraph": {"rich_text": [
        {"type": "mention", "mention": {"type": "user", "user": {"id": NOTIFY_USER_ID}}},
        {"type": "text", "text": {"content": f" informe {tipo.lower()} listo."}},
    ]}}
    metrics_block = {"type": "toggle", "toggle": {
        "rich_text": _rt("Métricas (JSON para la revisión de Claude)"),
        "children": [{"type": "code", "code": {"language": "json", "rich_text": _rt(
            json.dumps(m, ensure_ascii=False, indent=1, default=str))}}],
    }}
    children = [mention] + markdown_to_blocks(body) + [metrics_block]

    if existing:
        page_id = existing["id"]
        client.pages.update(page_id=page_id, properties=properties)
        # Sustituye el contenido de la página
        old = client.blocks.children.list(block_id=page_id).get("results", [])
        for b in old:
            client.blocks.delete(block_id=b["id"])
        client.blocks.children.append(block_id=page_id, children=children)
        print(f"Informe actualizado: {title}")
    else:
        page = client.pages.create(parent={"type": "data_source_id", "data_source_id": DS_INFORMES},
                                   icon={"type": "emoji", "emoji": {"Verde": "🟢", "Amarillo": "🟡", "Rojo": "🔴"}.get(semaforo, "⚪")},
                                   properties=properties, children=children)
        print(f"Informe creado: {title} ({page.get('url', page.get('id'))})")


def decide_tipo(now, client, day, forced):
    """Devuelve el tipo de informe a generar ahora, o None. Ver docstring del módulo."""
    if forced:
        return {"diario": "Diario", "pre-carrera": "Pre-carrera"}[forced]
    h = now.hour
    if 8 <= h < 16:
        existing = find_report(client, day, "Diario")
        if existing is None or prop(existing, "Datos incompletos"):
            return "Diario"
        print("El informe diario de hoy ya existe y está completo.")
        return None
    if h >= 20:
        tomorrow = (day + timedelta(days=1)).isoformat()
        rows = client.data_sources.query(data_source_id=DS_PLAN, filter={"and": [
            {"property": "Fecha", "date": {"equals": tomorrow}},
            {"property": "Tipo", "select": {"equals": "Competición"}},
        ]}).get("results", [])
        if rows and find_report(client, day, "Pre-carrera") is None:
            return "Pre-carrera"
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fecha", help="Fecha del informe (YYYY-MM-DD). Por defecto, hoy en Madrid.")
    parser.add_argument("--tipo", choices=["diario", "pre-carrera"], help="Fuerza el informe aunque no toque por la hora")
    args = parser.parse_args()

    now = now_madrid()
    today = date.fromisoformat(args.fecha) if args.fecha else now.date()
    client = get_notion()

    tipo = decide_tipo(now, client, today, args.tipo)
    if not tipo:
        print(f"{now:%H:%M} Madrid: no toca generar informe.")
        return
    print(f"Generando informe {tipo} para {today}")

    ensure_properties(client, DB_INFORMES, {"Revisado Claude": {"checkbox": {}}})
    existing = find_report(client, today, tipo)

    try:
        metrics = compute_metrics(client, get_garmin(), today)
    except Exception as e:
        # Fallo controlado: el workflow no falla, pero queda registrado
        traceback.print_exc()
        print(f"ERROR calculando métricas: {e}")
        return

    print("MÉTRICAS DEL INFORME:")
    print(json.dumps(metrics, ensure_ascii=False, indent=1, default=str))

    error = None
    try:
        semaforo, ajustada, motivos, info = apply_rules(metrics, tipo)
    except Exception as e:
        traceback.print_exc()
        semaforo, ajustada, motivos, info, error = "Sin datos", "", [], [], str(e)

    sesion = metrics["sesion_hoy"][0] if metrics["sesion_hoy"] else None
    resumen = "Sin sesión" if not sesion else (
        f"{sesion['sesion']} se mantiene" if ajustada == session_label(sesion) else f"{sesion['sesion']} → ajustar")
    prefix = "Pre-carrera · " if tipo == "Pre-carrera" else ""
    title = f"{DIAS[today.weekday()]} {today:%d/%m} · {prefix}{semaforo} · {resumen}"
    body = build_body(metrics, tipo, semaforo, ajustada, motivos, info, error)

    try:
        write_report(client, existing, today, tipo, metrics, semaforo, ajustada, title, body)
    except Exception as e:
        traceback.print_exc()
        print(f"ERROR escribiendo el informe en Notion: {e}")


if __name__ == "__main__":
    main()
