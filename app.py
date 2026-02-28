"""
app.py

Geo Pulse - Flask Web Server
Servese the interactive geothermal map on a local network.
Designed to run on a Raspberry Pi Zero 2 W, making it a low-energy production deployment

Routes:
- / : Main dashboard
- /map : Embedded Folium map
- /api/sites : JSON API returning top scored sites
- /api/status : JSON APi returning pipeline status
- /run : Trigger a fresh pipeline run

@Author: Ejay Aguirre
@Date: 2026-02-27
"""

import os
import json
import subprocess
from datetime import datetime
from flask import Flask, render_template, jsonify, send_from_directory

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs')
SCRIPTS_DIR = os.path.join(BASE_DIR, 'scripts')

last_run_time = None
pipleine_status = "idle" # idle, running, done, error

# Routes

@app.route('/')
def index():
    """
    Main dashboard page.
    Renders the HTML template with map iframe + site stats sidebar.
    """
    # Load scored sites for the sidebar stats
    sites = load_sites()
    top_site = sites[0] if sites else None

    return render_template(
        'index.html',
        sites=sites,
        top_site=top_site,
        last_run=last_run_time,
        status=pipeline_status
    )


@app.route('/map')
def serve_map():
    """
    Serve the Folium HTML map file directly.
    Used as the iframe source in the main dashboard.
    If map doesn't exist yet, generate it first.
    """
    map_path = os.path.join(OUTPUT_DIR, 'sweet_spot_map.html')

    # Auto-generate map if it doesn't exist
    if not os.path.exists(map_path):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        subprocess.run(['python', os.path.join(SCRIPTS_DIR, 'visualize.py')], check=True)

    return send_from_directory(OUTPUT_DIR, 'sweet_spot_map.html')


@app.route('/api/sites')
def api_sites():
    """
    JSON API endpoint returning all scored candidate sites.
    Useful for integrating with other tools or frontends.

    Returns:
        JSON array of site objects with GPS scores and coordinates.
    """
    sites = load_sites()
    return jsonify({
        "status": "ok",
        "count": len(sites),
        "sites": sites,
        "generated_at": last_run_time
    })


@app.route('/api/status')
def api_status():
    """
    JSON API endpoint returning current pipeline status.
    Useful for polling from the frontend to check if a run is complete.
    """
    map_exists = os.path.exists(os.path.join(OUTPUT_DIR, 'sweet_spot_map.html'))
    geojson_exists = os.path.exists(os.path.join(OUTPUT_DIR, 'scored_sites.geojson'))

    return jsonify({
        "pipeline_status": pipeline_status,
        "last_run": last_run_time,
        "map_ready": map_exists,
        "sites_ready": geojson_exists,
        "site_count": len(load_sites())
    })


@app.route('/run')
def run_pipeline():
    """
    Trigger a fresh scoring + visualization pipeline run.
    WARNING: This calls GEE and may take several minutes.
    In production, this should be a POST request with auth.
    """
    global last_run_time, pipeline_status

    pipeline_status = "running"
    try:
        # Re-run scoring and map generation
        subprocess.run(
            ['python', os.path.join(SCRIPTS_DIR, 'scoring.py')],
            check=True, capture_output=True
        )
        subprocess.run(
            ['python', os.path.join(SCRIPTS_DIR, 'visualize.py')],
            check=True, capture_output=True
        )
        last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pipeline_status = "done"
        return jsonify({"status": "ok", "message": "Pipeline complete", "ran_at": last_run_time})
    except subprocess.CalledProcessError as e:
        pipeline_status = "error"
        return jsonify({"status": "error", "message": str(e)}), 500


# Helper Function

def load_sites():
    """
    Load scored candidate sites from the GeoJSON output file.
    Falls back to empty list if file doesn't exist yet.

    Returns:
        list: Site dictionaries with lat, lon, GPS score, etc.
    """
    geojson_path = os.path.join(OUTPUT_DIR, 'scored_sites.geojson')

    if not os.path.exists(geojson_path):
        return []

    with open(geojson_path) as f:
        data = json.load(f)

    sites = []
    for i, feat in enumerate(data.get('features', [])[:10]):
        coords = feat['geometry']['coordinates']
        gps = feat['properties'].get('GPS', 50)
        sites.append({
            "rank": i + 1,
            "name": f"Site R-{i+1}",
            "lat": round(coords[1], 4),
            "lon": round(coords[0], 4),
            "gps": round(gps, 1),
            "note": f"GEE-scored site. GPS: {gps:.1f}"
        })

    # Sort by GPS score descending
    sites.sort(key=lambda x: x['gps'], reverse=True)
    return sites

# Run
if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # host='0.0.0.0' makes it accessible from any device on the local network
    # e.g. from your laptop: http://raspberrypi.local:5000
    print("Starting GeoPulse server...")
    print("Access dashboard at: http://0.0.0.0:5000")
    print("On your network:     http://<this-device-ip>:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)