# GeoPulse
**A Geothermal Sweet Spot Identifier** - an interactive Flask dashboard that runs a lightweight
geothermal site. It has a pipeline that scores locations and renders results on an embedded *Folium* map.

Built for the **Wilkes Center Climate Solutions Hackathon**.

## What it does
GeoPulse:
- Pulls **Landsat 9** imagery from **Google Earth Engine (GEE)** and computes a **Land Surface Temperature (LST)** signal.
- Combines multiple layers into a single **Geothermal Potential Score (GPS)**.
- Extracts the **top candidate sites** (GeoJSON) and generates an interactive **Folium map**.
- Serves a dashboard UI with a “Run Fresh Analysis” button and API endpoints.

## Project structure
Flask server expects the scoring scripts inside a `scripts/` folder

GeoPulse/
app.py
requirements.txt
templates/
index.html
scripts/
scoring.py
visualize.py
lst_analysis.py
data/
svi_utah/ <- downloaded from: https://www.atsdr.cdc.gov/place-health/php/svi/svi-data-documentation-download.html?CDC_AAref_Val=https://www.atsdr.cdc.gov/placeandhealth/svi/data_documentation_download.html
outputs/
scored_sites.geojson
sweet_spot_map.html
last_run_params.json

## Requirements 
Install Python dependencies:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirement.txt
```

## Google Earth Engine
```bash
earthengine authenticate
```
In scripts/scoring.py, update project ID
ee.Initialize(project="YOUR_GEE_PROJECT_ID")

## Gemini Chat
```bash
export GEMINI_API_KEY="YOUR_KEY"
python app.py
```

## Start the server
```bash
python app.py
```

