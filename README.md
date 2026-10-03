[![Sync Garmin to Notion](https://github.com/cgonzalezsanc/garmin-to-notion/actions/workflows/sync_garmin_to_notion.yml/badge.svg?branch=main)](https://github.com/cgonzalezsanc/garmin-to-notion/actions/workflows/sync_garmin_to_notion.yml)

## Esta versión (Carlos): sincronización + informe diario

### Horarios (GitHub Actions, en UTC)
| Cron (UTC) | Hora Madrid | Qué hace |
|---|---|---|
| `23 6 * * *` | 08:23 verano | Sincroniza + informe diario |
| `23 7 * * *` | 08:23 invierno / 09:23 verano | Sincroniza + informe diario (si aún no existe o tenía datos incompletos) |
| `7 10 * * *` | ~12:00 | Sincroniza + regenera el informe si tenía datos incompletos |
| `7 16 * * *` | ~18:00 | Sincroniza |
| `7 20 * * *` | ~22:00 | Sincroniza + informe pre-carrera si mañana hay Competición en el Plan |

Qué informe toca lo decide `daily_report.py` según la hora de Madrid (`zoneinfo`), así que el cambio de horario no requiere tocar nada.
A las 07:45 UTC una **rutina de Claude** (suscripción, sin API de pago) revisa el informe del día
(`Revisado Claude` desmarcado), lee la página *Contexto entrenador* y redacta el texto final.
Gestión de la rutina: https://claude.ai/code/routines

### Lanzar a mano
GitHub → Actions → *Sync Garmin to Notion* → **Run workflow**:
- `dry_run`: imprime lo que escribiría en Notion sin escribir.
- `informe`: `auto` (según la hora), `diario` o `pre-carrera` (fuerza el informe).

### Secrets necesarios
`GARMIN_EMAIL`, `GARMIN_PASSWORD`, `NOTION_TOKEN`, `NOTION_DB_ID`, `NOTION_PR_DB_ID`,
`NOTION_STEPS_DB_ID`, `NOTION_SLEEP_DB_ID`, `NOTION_EQ_DB_ID`, `NOTION_EX_DB_ID`.
Los IDs de Plan de entrenamiento, Informes diarios, Carreras y Contexto entrenador están en `daily_report.py`
(se pueden sobrescribir por variable de entorno). La integración de Notion necesita acceso a la página *Deporte*.

### En local
```bash
cp .env.example .env      # y rellenar credenciales
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python main.py --dry-run                      # sincronización sin escribir
python daily_report.py --dry-run --tipo diario
python backfill_km_corridos.py --dry-run --since 2025-06-01
```

### Datos añadidos
- **Actividades**: `Elev Gain (m)`, `Elev Loss (m)`, `Temp (°C)` (mín–máx; Garmin no da media y no siempre la da),
  `Cadence`, `GAP`, `Max HR`, `Km corridos` (tramos más rápidos que 8:00/km; ver `compute_km_corridos`).
- **Registro de sueño**: `HRV (ms)`, `HRV status`, `Body Battery mañana`, `Training Readiness` (al despertar),
  `Carga aguda`, `Training Status`.
- Las propiedades `Train Type`, `Shoes` y `Fav` no se sobrescriben al actualizar (se editan a mano).

---

# Garmin to Notion Integration :watch:
This project connects your Garmin activities and personal records to your Notion database, allowing you to keep track of your performance metrics in one place.

## Features :sparkles:  
  🔄  Automatically sync Garmin activities to Notion in real-time  
  📊  Track detailed activity metrics (distance, pace, heart rate)  
  🎯  Extract and track personal records (fastest 1K, longest ride)  
  👣  Optional daily steps tracker
  😴  Optional sleep data tracker  
  🤖  Zero-touch automation once configured  
  📱  Compatible with all Garmin activities and devices  
  🔧  Easy setup with clear instructions and minimal coding required  

test
## Prerequisites :hammer_and_wrench:  
- A Notion account with API access.
- A Garmin Connect account to pull activity data.
- If you wish to sync your Peloton workouts with Garmin, see [peloton-to-garmin](https://github.com/philosowaffle/peloton-to-garmin)
## Getting Started :dart:
A detailed step-by-step guide is provided on my Notion template [here](https://chloevoyer.notion.site/Set-up-Guide-17915ce7058880559a3ac9f8a0720046).
For more advanced users, follow these steps to set up the integration:
### 1. Fork this GitHub Repository
### 2. Duplicate my [Notion Template](https://www.notion.so/templates/fitness-tracker-738)
* Save your Activities and Personal Records database ID (you will need it for step 4)
  * Optional: Daily Steps database ID
  * Look at the URL: notion.so/username/[string-of-characters]
  * The database ID is everything after your “username/“ and before the “?v”
### 3. Create Notion Token
* Go to [Notion Integrations](https://www.notion.so/profile/integrations).
* [Create](https://developers.notion.com/docs/create-a-notion-integration) a new integration and copy the integration token.
* [Share](https://www.notion.so/help/add-and-manage-connections-with-the-api#enterprise-connection-settings) the integration with the target database in Notion.
### 4. Set Environment Secrets
* Environment secrets to define:
  * GARMIN_EMAIL
  * GARMIN_PASSWORD
  * NOTION_TOKEN
  * NOTION_DB_ID
  * NOTION_PR_DB_ID
  * NOTION_STEPS_DB_ID (optional)
  * NOTION_SLEEP_DB_ID (optional)
  * NOTION_EQ_DB_ID (optional)
  * NOTION_EX_DB_ID (optional)
### 5. Run Scripts (if not using automatic workflow)
* Run [garmin-activities.py](https://github.com/chloevoyer/garmin-to-notion/blob/main/garmin-activities.py) to sync your Garmin activities to Notion.  
`python garmin-activities.py`
* Run [person-records.py](https://github.com/chloevoyer/garmin-to-notion/blob/main/personal-records.py) to extract activity records (e.g., fastest run, longest ride).  
`python personal-records.py` 
## Example Configuration :pencil:  
You can customize the scripts to fit your needs by modifying environment variables and Notion database settings.  

Here is a screenshot of what my Notion dashboard looks like:  
![garmin-to-notion-template](https://github.com/user-attachments/assets/b37077cc-fe87-466f-9424-8ba9e4efa909)


My Notion template is available for free and can be duplicated to your Notion [here](https://www.notion.so/templates/fitness-tracker-738)

## Acknowledgements :raised_hands:  
- Reference dictionary and examples can be found in [cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect.git).
- This project was inspired by [n-kratz/garmin-notion](https://github.com/n-kratz/garmin-notion.git).
## Contributing :handshake:   
Contributions are welcome! If you find a bug or want to add a feature, feel free to open an issue or submit a pull request. Financial contributions are also greatly appreciated :blush:    

<a href="https://www.buymeacoffee.com/cvoyer" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="41" width="174"></a>   

## :copyright: License  
This project is licensed under the MIT License. See the LICENSE file for more details.
