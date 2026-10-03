from datetime import date, datetime, timedelta, UTC
from common import get_garmin, get_notion, get_data_source_id
import pytz
import os

# Constants
local_tz = pytz.timezone("Europe/Madrid")

def get_sleep_data(garmin):
    today = datetime.today().date()
    return garmin.get_sleep_data(today.isoformat())

def format_duration(seconds):
    minutes = (seconds or 0) // 60
    return f"{minutes // 60}h {minutes % 60}m"

def format_time(timestamp):
    return (
        datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if timestamp else None
    )

def format_time_readable(timestamp):
    return (
        datetime.fromtimestamp(timestamp / 1000, local_tz).strftime("%H:%M")
        if timestamp else "Unknown"
    )

def format_date_for_name(sleep_date):
    return datetime.strptime(sleep_date, "%Y-%m-%d").strftime("%d.%m.%Y") if sleep_date else "Unknown"

def create_sleep_data(client, database_id, sleep_data, skip_zero_sleep=True):
    daily_sleep = sleep_data.get('dailySleepDTO', {})
    if not daily_sleep:
        return
    
    sleep_date = daily_sleep.get('calendarDate', "Unknown Date")
    total_sleep = sum(
        (daily_sleep.get(k, 0) or 0) for k in ['deepSleepSeconds', 'lightSleepSeconds', 'remSleepSeconds']
    )
    
    
    if skip_zero_sleep and total_sleep == 0:
        print(f"Skipping sleep data for {sleep_date} as total sleep is 0")
        return

    properties = {
        "Date": {"title": [{"text": {"content": format_date_for_name(sleep_date)}}]},
        "Times": {"rich_text": [{"text": {"content": f"{format_time_readable(daily_sleep.get('sleepStartTimestampGMT'))} → {format_time_readable(daily_sleep.get('sleepEndTimestampGMT'))}"}}]},
        "Long Date": {"date": {"start": sleep_date}},
        "Full Date/Time": {"date": {"start": format_time(daily_sleep.get('sleepStartTimestampGMT')), "end": format_time(daily_sleep.get('sleepEndTimestampGMT'))}},
        "Total Sleep (h)": {"number": round(total_sleep / 3600, 1)},
        "Light Sleep (h)": {"number": round(daily_sleep.get('lightSleepSeconds', 0) / 3600, 1)},
        "Deep Sleep (h)": {"number": round(daily_sleep.get('deepSleepSeconds', 0) / 3600, 1)},
        "REM Sleep (h)": {"number": round(daily_sleep.get('remSleepSeconds', 0) / 3600, 1)},
        "Awake Time (h)": {"number": round(daily_sleep.get('awakeSleepSeconds', 0) / 3600, 1)},
        "Total Sleep": {"rich_text": [{"text": {"content": format_duration(total_sleep)}}]},
        "Light Sleep": {"rich_text": [{"text": {"content": format_duration(daily_sleep.get('lightSleepSeconds', 0))}}]},
        "Deep Sleep": {"rich_text": [{"text": {"content": format_duration(daily_sleep.get('deepSleepSeconds', 0))}}]},
        "REM Sleep": {"rich_text": [{"text": {"content": format_duration(daily_sleep.get('remSleepSeconds', 0))}}]},
        "Awake Time": {"rich_text": [{"text": {"content": format_duration(daily_sleep.get('awakeSleepSeconds', 0))}}]},
        # Sin dato -> vacío (un 0 contaminaría la mediana de FC en reposo)
        "Resting HR": {"number": sleep_data.get('restingHeartRate') or None},
        "Score": {"number": daily_sleep.get('sleepScores', {}).get('overall', {}).get('value', None)}
    }

    # Filtro para comprobar si ya existe la entrada
    query_filter = {"property": "Long Date", "date": {"equals": sleep_date}}

    data_source_id = get_data_source_id(client, database_id)
    response = client.data_sources.query(
        data_source_id=data_source_id,
        filter=query_filter
    )
    
    if response["results"]:
        # Ya existe, actualiza
        page_id = response["results"][0]["id"]
        client.pages.update(page_id=page_id, properties=properties, icon={"emoji": "😴"})
        print(f"Not updated sleep entry for: {sleep_date}")
    else:
        # No existe, crea nuevo
        client.pages.create(parent={"database_id": database_id}, properties=properties, icon={"emoji": "😴"})
        print(f"Created sleep entry for: {sleep_date}")
    #client.pages.create(parent={"database_id": database_id}, properties=properties, icon={"emoji": "😴"})


def main(garmin=None, client=None):
    database_id = os.getenv("NOTION_SLEEP_DB_ID")

    garmin = garmin or get_garmin()
    client = client or get_notion()

    """
    Get last x days of daily step count data from Garmin Connect.
    """
    startdate = date.today() - timedelta(days=3)
    enddate = date.today()
    daterange = [startdate + timedelta(days=x) for x in range((enddate - startdate).days + 1)]

    daily_sleep = []
    for d in daterange:
        daily_sleep = garmin.get_sleep_data(d.isoformat())
        if daily_sleep:
            sleep_date = daily_sleep.get('dailySleepDTO', {}).get('calendarDate')
            # if sleep_date and not sleep_data_exists(client, database_id, sleep_date):
            if sleep_date:
                create_sleep_data(client, database_id, daily_sleep, skip_zero_sleep=True)

if __name__ == '__main__':
    main()
