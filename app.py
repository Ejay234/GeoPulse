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
import threading
import subprocess
import sys
from datetime import datetime
from flask import Flask, render_template, jsonify, send_from_directory

# App setup
app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs')
SCRIPTS_DIR = os.path.join(BASE_DIR, 'scripts')

# Pipeline state
pipeline = {
    "status": "idle", # idle, running, done, error
    "step": "",
    "last_run": None,
    "error": None,
    "progress": 0
}

# Pipline Runner
def run_pipeline_background(force=False):
    """
    Run the full GeoPulse pipeline in the background

    Steps:
    1. scoring.py - ftch LST from GEE, score all pixels, export top sites
    2. visualize.py - generate Folium map with scored sites
    
    lst_anlaysis will be handled inside scoring.py
    """

    global pipeline

    if pipeline["status"] == "running":
        return
    
    geojson_ok = os.path.exists(os.path.join(OUTPUT_DIR, 'scored_sites.geojson'))
    map_ok = os.path.exists(os.path.join(OUTPUT_DIR, 'sweet_spot_map.html'))


    if geojson_ok and map_ok and not force:
        pipeline["status"]   = "done"
        pipeline["step"]     = "Outputs already exist"
        pipeline["progress"] = 100
        pipeline["last_run"] = "Previous session"
        return
    
    # Start Pipeline
    pipeline["status"]   = "running"
    pipeline["error"]    = None
    pipeline["progress"] = 0
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Python interpreter as the current venv
    python = sys.executable

    steps = [
        {
            "name":    "Fetching satellite data & scoring sites",
            "script":  os.path.join(SCRIPTS_DIR, 'scoring.py'),
            "progress": 60,
        },
        {
            "name":    "Generating interactive map",
            "script":  os.path.join(SCRIPTS_DIR, 'visualize.py'),
            "progress": 100,
        },
    ]

    for step in steps:
        pipeline["step"]     = step["name"]
        print(f"[GeoPulse] Running: {step['name']}...")

        try:
            result = subprocess.run(
                [python, step["script"]],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                # Fail, error and stops
                raise RuntimeError(result.stderr or result.stdout)

            pipeline["progress"] = step["progress"]
            print(f"[GeoPulse] Done: {step['name']}")

        except Exception as e:
            pipeline["status"] = "error"
            pipeline["step"]   = f"Failed at: {step['name']}"
            pipeline["error"]  = str(e)
            print(f"[GeoPulse] ERROR: {e}")
            return

    # Compeletion
    pipeline["status"]   = "done"
    pipeline["step"]     = "Pipeline complete"
    pipeline["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[GeoPulse] Pipeline complete at {pipeline['last_run']}")

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
        status=pipeline["status"],
        step=pipeline["step"],
        progress=pipeline["progress"],
        last_run=pipeline["last_run"],
        error=pipeline["error"],
    )


@app.route('/map')
def serve_map():
    """
    Serve the Folium HTML map file directly.
    Used as the iframe source in the main dashboard.
    If map doesn't exist yet, generate it first.
    """
    map_path = os.path.join(OUTPUT_DIR, 'sweet_spot_map.html')

    # Placeholder if it doesn't exist
    if not os.path.exists(map_path):
        return """
        <html><body style="background:#0f1117; color:#888; font-family:Arial;
            display:flex; align-items:center; justify-content:center; height:100vh; margin:0;">
            <div style="text-align:center;">
                <div style="font-size:48px; margin-bottom:16px;">🌋</div>
                <div style="font-size:18px; color:#d73027;">Generating map...</div>
                <div style="font-size:13px; margin-top:8px;">This page will refresh automatically.</div>
            </div>
        </body></html>
        """

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
        "count":  len(sites),
        "sites":  sites,
    })


@app.route('/api/status')
def api_status():
    """
    JSON API endpoint returning current pipeline status.
    Useful for polling from the frontend to check if a run is complete.
    """
    return jsonify({
        "status":    pipeline["status"],
        "step":      pipeline["step"],
        "progress":  pipeline["progress"],
        "last_run":  pipeline["last_run"],
        "error":     pipeline["error"],
        "map_ready": os.path.exists(os.path.join(OUTPUT_DIR, 'sweet_spot_map.html')),
        "sites":     len(load_sites()),
    })



@app.route('/run')
def run_pipeline():
    """
    Trigger a fresh scoring + visualization pipeline run.
    'Run Fresh Analysis' button on the dashboard calls this route.
    """
    if pipeline["status"] == "running":
        return jsonify({"status": "already_running", "message": "Pipeline is already running."})

    # Start pipeline in background thread so request returns immediately
    t = threading.Thread(target=run_pipeline_background, kwargs={"force": True}, daemon=True)
    t.start()

    return jsonify({"status": "started", "message": "Pipeline started in background."})



# Helper Function

def load_sites():
    """
    Load scored candidate sites from the GeoJSON output file.
    Falls back to empty list if file doesn't exist yet.

    Returns:
        list: Site dictionaries with lat, lon, GPS score, etc.
    """
    path = os.path.join(OUTPUT_DIR, 'scored_sites.geojson')
    if not os.path.exists(path):
        return []

    with open(path) as f:
        data = json.load(f)

    sites = []
    for i, feat in enumerate(data.get('features', [])[:10]):
        coords = feat['geometry']['coordinates']
        gps    = feat['properties'].get('GPS', 50)
        sites.append({
            "rank": i + 1,
            "name": f"Site R-{i+1}",
            "lat":  round(coords[1], 4),
            "lon":  round(coords[0], 4),
            "gps":  round(gps, 1),
            "note": f"GEE-scored site. GPS: {gps:.1f}"
        })

    return sorted(sites, key=lambda x: x['gps'], reverse=True)

# Run
if __name__ == '__main__':
    print("  GeoPulse - Geothermal Sweet Spot Identifier")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Auto-run pipeline on startup in background thread
    # If outputs already exist, this returns immediately without re-running
    print("[GeoPulse] Checking for existing outputs...")
    t = threading.Thread(target=run_pipeline_background, daemon=True)
    t.start()

    print("[GeoPulse] Server starting at http://0.0.0.0:5001")
    print("[GeoPulse] Open http://localhost:5001 in your browser")
    print("[GeoPulse] On your network: http://<this-device-ip>:5001")
    print("=" * 50)

    # Start Flask
    app.run(host='0.0.0.0', port=5000, debug=False)
