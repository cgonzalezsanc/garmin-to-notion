from datetime import date
from garminconnect import Garmin
from notion_client import Client
import os

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
    # Add more mappings as needed
}

def get_gears(garmin):
    # Recuperar el perfil de usuario y su id
    user_profile = garmin.get_user_profile()
    user_profile_number = user_profile.get('id')

    # Obtener el equipamiento
    return garmin.get_gear(user_profile_number)

def fill_properties(gear, garmin, today, custom_make_model, gear_id):
    gear_status = gear.get('gearStatusName', '')
    gear_type = gear.get('displayName')

    # Completo la información del gear
    gear_stats = garmin.get_gear_stats(gear.get('uuid'))
    gear_activities = gear_stats.get('totalActivities', 0)
    gear_distance = round(gear_stats.get('totalDistance', 0)/1000)

    # Formato para Notion API (ajusta a tu estructura de propiedades si cambia)
    properties = {
        "Nombre":      {"title": [{"text": {"content": str(custom_make_model)}}]},
        "Id":          {"number": gear_id},
        "Estado":      {"select": {"name": str(gear_status)}},
        "Tipo":        {"select": {"name": str(gear_type)}},
        "Fecha":       {"date": {"start": today}},
        "Actividades": {"number": gear_activities},
        "Km":          {"number": gear_distance}
    }

    return properties

# Comprobación de si el equipamiento ya existe en la tabla de Notion
def check_gear_exists(gear_id, today, client, database_id):
    # Filtros para buscar entrada existente
    query_filter = {
        "and": [
            {"property": "Id", "number": {"equals": gear_id}},
            {"property": "Fecha", "date": {"equals": today}}
        ]
    }

    filter_response = client.databases.query(
        database_id=database_id,
        filter=query_filter
    )

    return filter_response

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
        custom_make_model = gear.get('customMakeModel', 'Sin nombre')
        gear_id = gear.get('gearPk', '')
        properties = fill_properties(gear, garmin, today, custom_make_model, gear_id)

        # comprobamos si existe
        filter_response = check_gear_exists(gear_id, today, client, database_id)

        # Saco la URL de la imagen correspondiente
        img_url = GEAR_IMAGES.get(custom_make_model, "https://i.ibb.co/ynf136zm/16-best-long-distance-running-shoes-15275091-main.webp")
        print("Imagen " + img_url)

        if filter_response["results"]:
            # Ya existe, actualiza
            page_id = filter_response["results"][0]["id"]
            client.pages.update(
                page_id=page_id,
                properties=properties,
                cover={"type": "external", "external": {"url": img_url}}
            )
            print(f"Actualizado: {custom_make_model}")
        else:
            # No existe, crea nuevo
            client.pages.create(
                parent={"database_id": database_id},
                properties=properties,
                cover={"type": "external", "external": {"url": img_url}}
            )
            print(f"Creado: {custom_make_model}")

if __name__ == "__main__":
    main()
