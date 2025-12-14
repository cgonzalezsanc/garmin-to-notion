from datetime import date
from garminconnect import Garmin
from notion_client import Client
import os
import sys

    # Añado las imágenes para cada uno
GEAR_IMAGES = {
    "Nike Pegasus Z300": "https://i.ibb.co/WNC1BkSw/nike-air-zoom-pegasus-35-137881-942851-001-960.webp",
    "Nike vaporfly next 2": "https://i.ibb.co/Kx3jLkKn/nike-zoomx-vaporfly-next-2-w-360411-dj5458-100.jpg",
    "ASICS Nimbus 25": "https://i.ibb.co/759tB8N/nimbus-25.jpg",
    "ASICS GEL-Nimbus": "https://i.ibb.co/93NtynVG/ASICS-Gel-Nimbus-24-1920-1-100981.jpg",
    "HOKA Clifton 9": "https://i.ibb.co/1GtnYgFN/clifton-9.jpg",
    "HOKA Skyflow 1": "https://i.ibb.co/1YMvP8zT/Skyflow.jpg",
    "Saucony Ride 18": "https://i.ibb.co/GffzRrzC/saucony-ride-18-7-1200x690.png",
    "Saucony Endorphin Pro 4": "https://i.ibb.co/Pv1mw8Cs/Endorphin-Pro-4.jpg",
    "Asics Superblast 2": "https://i.ibb.co/F49NZJct/ASICS-Superblast-2.webp",
    "Adidas Adizero EVO SL": "https://i.ibb.co/C50J0CWp/Zapatilla-Adizero-EVO-SL-Plateado-JR3419-01-00-standard.jpg",
    "Nike Vomero Plus": "https://static.nike.com/a/images/t_web_pdp_535_v2/f_auto/4746e60f-d845-4f02-a5cf-f06ab8db9312/NIKE+VOMERO+PLUS.png",
    # Add more mappings as needed
}

GEAR_ICONS = {
    "asics": "https://i.ibb.co/PZXnJZds/asics.webp",
    "adidas": "https://i.ibb.co/6RS08KtL/adidas-logo-adidas-icon-transparent-free-png.webp",
    "hoka": "https://i.ibb.co/MDDTwrMN/Hoka-Update-2.png",
    "nike": "https://i.ibb.co/fVpDW7Rw/nike.jpg",
    "saucony": "https://i.ibb.co/rXpXDfX/saucony.webp",
    # Add more mappings as needed
}

def get_gears(garmin):
    # Recuperar el perfil de usuario y su id
    user_profile = garmin.get_user_profile()
    user_profile_number = user_profile.get('id')

    # Obtener el equipamiento
    return garmin.get_gear(user_profile_number)

def assign_gear_to_activities(client, gear_activities, gear_name):
    act_database_id = os.getenv("NOTION_DB_ID")
    # itero sobre cada actividad de al base de datos de actividades
    for gear_activity in gear_activities:
        gear_activity_id = gear_activity.get('activityId', 0)
        # pongo un filtro para buscar en la tabla de actividades por el id de la actividad
        query_filter = {"property": "Activity Id", "number": {"equals": gear_activity_id}}
        filter_response = client.databases.query(
            database_id=act_database_id,
            filter=query_filter
        )
        # si se encuentra la actividad, ver si ya tiene definido el campo gear
        #print(f"Filter response results {filter_response["results"]}")
        if filter_response["results"]:
            # si no lo tiene, añado el gear y sigo con la siguiente iteración del bucle
            #print(f"Nombre de la zapa: {filter_response["results"][0]["properties"]["Shoes"]['select']}")
            if not filter_response["results"][0]["properties"]["Shoes"]['select']:
                properties = {"Shoes": {"select": {"name": gear_name}}}
                page_id = filter_response["results"][0]["id"]
                client.pages.update(
                    page_id=page_id,
                    properties=properties
                )
                #print(f"Añadido {gear_name} a la actividad {filter_response["results"][0]["properties"]["Activity Name"]["title"][0]["plain_text"]}")
            # si lo tiene, termino el bucle y me salgo
    return

def fill_properties(client, gear, garmin, today, gear_name, gear_id):
    gear_status = gear.get('gearStatusName', '')
    gear_type = gear.get('displayName')
    gear_uuid = gear.get('uuid')

    # Completo la información del gear con las actividades y distancia
    gear_stats = garmin.get_gear_stats(gear_uuid)
    gear_nmb_activities = gear_stats.get('totalActivities', 0)
    gear_distance = round(gear_stats.get('totalDistance', 0)/1000)

    # Completo más la información de las actividades
    if gear_status == 'active':
        gear_activities = garmin.get_gear_activities(gear_uuid, limit=3)
        assign_gear_to_activities(client, gear_activities, gear_name)

    # Formato para Notion API (ajusta a tu estructura de propiedades si cambia)
    properties = {
        "Nombre":      {"title": [{"text": {"content": str(gear_name)}}]},
        "Id":          {"number": gear_id},
        "Estado":      {"select": {"name": str(gear_status)}},
        "Tipo":        {"select": {"name": str(gear_type)}},
        "Fecha":       {"date": {"start": today}},
        "Actividades": {"number": gear_nmb_activities},
        "Km":          {"number": gear_distance}
    }

    return properties

# Comprobación de si el equipamiento ya existe en la tabla de Notion
def check_if_gear_exists(gear_id, client, database_id):
    # Filtro para comprobar si ya existe la entrada
    query_filter = {"property": "Id", "number": {"equals": gear_id}}

    # Petición para filtrar la base de datos
    filter_response = client.databases.query(
        database_id=database_id,
        filter=query_filter
    )

    return filter_response

def get_gear_icon_url(gear_name):
    # Convertimos gear_name a minúsculas robustas con casefold
    gear_name_cf = gear_name.casefold()

    for brand, url in GEAR_ICONS.items():
        # Comprobamos si el nombre de la marca aparece en gear_name (ambos en minúsculas con casefold)
        if brand.casefold() in gear_name_cf:
            return url
    return "https://img.icons8.com/?size=100&id=XAUYGhUZyfQG&format=png&color=000000"  # Si no se encuentra ninguna marca

def main():
    # Autenticación y configuración
    garmin_email = os.getenv("GARMIN_EMAIL")
    garmin_password = os.getenv("GARMIN_PASSWORD")
    notion_token = os.getenv("NOTION_TOKEN")
    database_id = os.getenv("NOTION_EQ_DB_ID")

   # Iniciar sesión en Garmin
    garmin = Garmin(garmin_email, garmin_password)
    garmin.login()

    # Iniciar cliente de Notion
    client = Client(auth=notion_token)

    # Recuperamos la lista de equipamientos
    gears = get_gears(garmin)

    # Fecha del día de ejecución
    today = date.today().isoformat()

    # Recorremos cada equipo y guardamos la información en Notion
    for gear in gears:
        gear_name = gear.get('customMakeModel', 'Sin nombre')
        gear_id = gear.get('gearPk', '')
        properties = fill_properties(client, gear, garmin, today, gear_name, gear_id)

        # comprobamos si existe
        filter_response = check_if_gear_exists(gear_id, client, database_id)

        # Saco la URL de la imagen e icono correspondiente
        img_url = GEAR_IMAGES.get(gear_name, "https://i.ibb.co/ynf136zm/16-best-long-distance-running-shoes-15275091-main.webp")

        # Saco la URL del icono
        icon_url = get_gear_icon_url(gear_name)

        if filter_response["results"]:
            # Ya existe, actualiza
            page_id = filter_response["results"][0]["id"]
            client.pages.update(
                page_id=page_id,
                properties=properties,
                cover={"type": "external", "external": {"url": img_url}},
                icon={"type": "external", "external": {"url": icon_url}}
            )
            print(f"Actualizado: {gear_name}")
        else:
            # No existe, crea nuevo
            client.pages.create(
                parent={"database_id": database_id},
                properties=properties,
                cover={"type": "external", "external": {"url": img_url}},
                icon={"type": "external", "external": {"url": icon_url}}
            )
            print(f"Creado: {gear_name}")

if __name__ == "__main__":
    main()
