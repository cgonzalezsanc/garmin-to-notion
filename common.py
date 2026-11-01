"""
Utilidades compartidas por todos los scripts de sincronización:
- Login único en Garmin (reutiliza tokens si existen).
- Cliente de Notion con modo --dry-run (imprime las escrituras en lugar de hacerlas).
- Caché de data source IDs.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from garminconnect import Garmin
from notion_client import APIErrorCode, APIResponseError, Client
from dotenv import load_dotenv
import json
import os
import sys
import time

MADRID_TZ = ZoneInfo("Europe/Madrid")

load_dotenv()

# La consola de Windows no usa UTF-8 por defecto (tildes, emojis)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def is_dry_run():
    """--dry-run en la línea de comandos o DRY_RUN=1/true en el entorno."""
    return "--dry-run" in sys.argv or os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")


def now_madrid():
    return datetime.now(MADRID_TZ)


# ---------------------------------------------------------------------------
# Garmin
# ---------------------------------------------------------------------------

_GARMIN = None


def get_garmin():
    """Devuelve un cliente de Garmin logueado. Solo hace login una vez por proceso."""
    global _GARMIN
    if _GARMIN is not None:
        return _GARMIN

    # Con tokenstore, garminconnect reutiliza los tokens guardados y, si no
    # existen o caducaron, hace login con usuario/contraseña y los guarda.
    tokenstore = os.getenv("GARMINTOKENS", "~/.garminconnect")
    garmin = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    garmin.login(tokenstore)

    _GARMIN = garmin
    return garmin


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------

# Métodos que escriben en Notion, por endpoint. En dry-run se imprimen y no se ejecutan.
_WRITE_METHODS = {
    "pages": {"create", "update"},
    "data_sources": {"update", "create"},
    "databases": {"update", "create"},
    "blocks": {"update", "delete"},
    "blocks.children": {"append"},
    "comments": {"create"},
}


def _short(value, limit=4000):
    text = json.dumps(value, ensure_ascii=False, default=str, indent=1)
    return text if len(text) <= limit else text[:limit] + " …"


class _DryRunProxy:
    def __init__(self, target, path=""):
        self._target = target
        self._path = path

    def __getattr__(self, attr):
        value = getattr(self._target, attr)
        full = f"{self._path}.{attr}" if self._path else attr

        if callable(value) and attr in _WRITE_METHODS.get(self._path, set()):
            def fake(**kwargs):
                print(f"[DRY-RUN] {full}: {_short(kwargs)}")
                return {"id": "dry-run", "object": "dry-run", "results": []}
            return fake

        if full in _WRITE_METHODS or any(k.startswith(full + ".") for k in _WRITE_METHODS):
            return _DryRunProxy(value, full)
        return value


# Notion admite una media de 3 peticiones por segundo y responde 429 si se supera.
NOTION_MIN_INTERVAL = 0.4  # segundos entre peticiones (2,5/s)
NOTION_MAX_RETRIES = 6


class ThrottledClient(Client):
    """Cliente de Notion con freno de peticiones y reintentos ante 429 (notion-client no reintenta)."""

    _last_request = 0.0

    def request(self, *args, **kwargs):
        for attempt in range(NOTION_MAX_RETRIES + 1):
            pause = self._last_request + NOTION_MIN_INTERVAL - time.monotonic()
            if pause > 0:
                time.sleep(pause)
            self._last_request = time.monotonic()
            try:
                return super().request(*args, **kwargs)
            except APIResponseError as e:
                # Un 429 no se ha procesado: repetirlo es seguro también en escrituras
                if e.code != APIErrorCode.RateLimited or attempt == NOTION_MAX_RETRIES:
                    raise
                wait = float(e.headers.get("retry-after") or 2 ** attempt)
                print(f"Notion 429: reintento {attempt + 1}/{NOTION_MAX_RETRIES} en {wait:g} s")
                time.sleep(wait)


def get_notion():
    client = ThrottledClient(auth=os.getenv("NOTION_TOKEN"))
    if is_dry_run():
        print("[DRY-RUN] No se escribirá nada en Notion.")
        return _DryRunProxy(client)
    return client


_DATA_SOURCE_IDS = {}


def get_data_source_id(client, database_id):
    if database_id not in _DATA_SOURCE_IDS:
        db = client.databases.retrieve(database_id=database_id)
        data_sources = db.get("data_sources", [])
        if not data_sources:
            raise RuntimeError(f"No data_sources found for database {database_id}")
        _DATA_SOURCE_IDS[database_id] = data_sources[0]["id"]
    return _DATA_SOURCE_IDS[database_id]


_ENSURED = set()


def ensure_properties(client, database_id, schema):
    """
    Crea en la base de datos las propiedades de `schema` que no existan.
    Nunca renombra ni borra propiedades existentes.
    schema: {"Nombre": {"number": {}}, "Otro": {"rich_text": {}}, ...}
    """
    if database_id in _ENSURED:
        return
    data_source_id = get_data_source_id(client, database_id)
    existing = client.data_sources.retrieve(data_source_id=data_source_id).get("properties", {})
    missing = {name: definition for name, definition in schema.items() if name not in existing}
    if missing:
        print(f"Creando propiedades nuevas en Notion: {', '.join(missing)}")
        client.data_sources.update(data_source_id=data_source_id, properties=missing)
    _ENSURED.add(database_id)


def format_pace_ms(speed_ms):
    """Velocidad en m/s -> 'm:ss' por km. None si no hay dato."""
    if not speed_ms or speed_ms <= 0:
        return None
    total = round(1000 / speed_ms)
    return f"{total // 60}:{total % 60:02d}"


def query_all(client, data_source_id, **kwargs):
    """Consulta una data source paginando hasta traer todos los resultados."""
    results, cursor = [], None
    while True:
        params = dict(kwargs, data_source_id=data_source_id)
        if cursor:
            params["start_cursor"] = cursor
        resp = client.data_sources.query(**params)
        results.extend(resp.get("results", []))
        if not resp.get("has_more"):
            return results
        cursor = resp.get("next_cursor")
