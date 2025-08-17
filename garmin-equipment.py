from datetime import date
from garminconnect import Garmin
from notion_client import Client
import os

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

    # Recuperar el perfil de usuario y su id
    user_profile = garmin.get_user_profile()
    user_profile_number = user_profile.get('id')

    # Obtener el equipamiento
    gears = garmin.get_gear(user_profile_number)

    # Fecha del día de ejecución
    today = date.today().isoformat()

    # Recorrer cada equipo y guardar la información en Notion
    for gear in gears:
        custom_make_model = gear.get('customMakeModel', 'Sin nombre')
        gear_id = gear.get('gearPk', '')
        gear_status = gear.get('gearStatusName', '')
        # Usar displayName si existe, si no, gearTypeName (nunca serán ambos None)
        gear_type = gear.get('displayName')

        # Completo la información del gear
        gear_stats = garmin.get_gear_stats(gear.get('uuid'))
        gear_activities = gear_stats.get('totalActivities', 0)
        gear_distance = round(gear_stats.get('totalDistance', 0)/1000)

        # Filtros para buscar entrada existente
        query_filter = {
            "and": [
                {"property": "Id", "number": {"equals": gear_id}},
                {"property": "Fecha", "date": {"equals": today}}
            ]
        }

        response = client.databases.query(
            database_id=database_id,
            filter=query_filter
        )

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

        if response["results"]:
            # Ya existe, actualiza
            page_id = response["results"][0]["id"]
            client.pages.update(page_id=page_id, properties=properties)
        else:
            # No existe, crea nuevo
            client.pages.create(parent={"database_id": database_id}, properties=properties)

if __name__ == "__main__":
    main()
