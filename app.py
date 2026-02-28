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
import zipfile
import io
from datetime import datetime
from flask import Flask, render_template, jsonify, send_from_directory, request, send_file

# App setup
app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs')
SCRIPTS_DIR = os.path.join(BASE_DIR, 'scripts')

# Default params
DEFAULT_PARAMS = {
    "start_date": "2023-05-01",
    "end_date": "2024-09-30",
    "cloud_cover": 20,
    "weight_lst":  0.5,
    "weight_grid": 0.3,
    "weight_svi":  0.2,
    "num_sites":   10,
    "percentile":  70,

    # Study region 
    "region": "southern_utah",  # southern_utah, central_utah, northern_utah, all_utah, custom
    "custom_lat_min": 37.0,
    "custom_lat_max": 42.0,
    "custom_lon_min": -114.0,
    "custom_lon_max": -109.0,

    # Heatmap Visual
    "hm_radius": 25,
    "hm_blur": 15,
    "hm_opacity": 0.3,
    "min_gps_score": 0,

    # County Filter
    "county_beaver": True,
    "county_millard": True,
    "county_saltlake": True,

    # LST Temperature Celsius
    "lst_min": 20, 
    "lst_max": 60, 
}

# Pipeline state
pipeline = {
    "status":  "idle",
    "step":    "",
    "last_run": None,
    "error":   None,
    "progress": 0,
    "params":  DEFAULT_PARAMS.copy(),
    "has_run": False
}

# Pipline Runner
def run_pipeline_background(params=None, force=False):
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
    p = params or pipeline["params"]
    pipeline["params"]   = p
    pipeline["status"]   = "running"
    pipeline["error"]    = None
    pipeline["progress"] = 0
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Inject parameters
    env = os.environ.copy()
    env["GEOPULSE_START_DATE"]     = str(p["start_date"])
    env["GEOPULSE_END_DATE"]       = str(p["end_date"])
    env["GEOPULSE_CLOUD_COVER"]    = str(p["cloud_cover"])
    env["GEOPULSE_WEIGHT_LST"]     = str(p["weight_lst"])
    env["GEOPULSE_WEIGHT_GRID"]    = str(p["weight_grid"])
    env["GEOPULSE_WEIGHT_SVI"]     = str(p["weight_svi"])
    env["GEOPULSE_NUM_SITES"]      = str(p["num_sites"])
    env["GEOPULSE_PERCENTILE"]     = str(p["percentile"])
    env["GEOPULSE_REGION"]         = str(p.get("region", "southern_utah"))
    env["GEOPULSE_CUSTOM_LAT_MIN"] = str(p.get("custom_lat_min",  37.0))
    env["GEOPULSE_CUSTOM_LAT_MAX"] = str(p.get("custom_lat_max",  42.0))
    env["GEOPULSE_CUSTOM_LON_MIN"] = str(p.get("custom_lon_min", -114.0))
    env["GEOPULSE_CUSTOM_LON_MAX"] = str(p.get("custom_lon_max", -109.0))
    env["GEOPULSE_HM_RADIUS"]      = str(p.get("hm_radius",  25))
    env["GEOPULSE_HM_BLUR"]        = str(p.get("hm_blur",    15))
    env["GEOPULSE_HM_OPACITY"]     = str(p.get("hm_opacity", 0.3))
    env["GEOPULSE_MIN_GPS"]        = str(p.get("min_gps",    0))
    env["GEOPULSE_MIN_LST"]        = str(p.get("min_lst",    20))
    env["GEOPULSE_MAX_LST"]        = str(p.get("max_lst",    60))

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
        pipeline["step"] = step["name"]
        print(f"[GeoPulse] {step['name']}...")
        try:
            result = subprocess.run([python, step["script"]],
                                    capture_output=True, text=True, env=env)
            print(result.stdout)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout)
            pipeline["progress"] = step["progress"]
        except Exception as e:
            pipeline["status"] = "error"
            pipeline["step"]   = f"Failed: {step['name']}"
            pipeline["error"]  = str(e)
            print(f"[GeoPulse] ERROR: {e}")
            return

    pipeline["status"]   = "done"
    pipeline["step"]     = "Pipeline complete"
    pipeline["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pipeline["has_run"]  = True 
    # Save params used for this run
    with open(os.path.join(OUTPUT_DIR, 'last_run_params.json'), 'w') as f:
        json.dump(p, f, indent=2)
    print(f"[GeoPulse] Complete at {pipeline['last_run']}")

# Routes

@app.route('/')
def index():
    """
    Main dashboard page.
    Renders the HTML template with map iframe + site stats sidebar.
    """
    has_run = pipeline.get("has_run", False)
    sites = load_sites() if has_run else []
    top_site = sites[0] if sites else None
    last_params = load_last_params()
    svi_available = check_svi_available()
    return render_template(
        'index.html',
        sites=sites,
        top_site=top_site,
        status=pipeline["status"],
        step=pipeline["step"],
        progress=pipeline["progress"],
        last_run=pipeline["last_run"],
        error=pipeline["error"],
        params=last_params,
        defaults=DEFAULT_PARAMS,
        svi_available=svi_available,
        has_run=has_run,
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
                <div style="font-size:48px; margin-bottom:16px;">GeoPulse</div>
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



@app.route('/run', methods=['GET', 'POST'])
def run_pipeline():
    """
    Trigger a fresh scoring + visualization pipeline run.
    'Run Fresh Analysis' button on the dashboard calls this route.
    """
    if pipeline["status"] == "running":
        return jsonify({"status": "already_running", "message": "Pipeline is already running."})

    params = DEFAULT_PARAMS.copy()
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        try:
            if "start_date"  in data: params["start_date"]  = data["start_date"]
            if "end_date"    in data: params["end_date"]     = data["end_date"]
            if "cloud_cover" in data: params["cloud_cover"]  = int(data["cloud_cover"])
            if "weight_lst"  in data: params["weight_lst"]   = float(data["weight_lst"])
            if "weight_grid" in data: params["weight_grid"]  = float(data["weight_grid"])
            if "weight_svi"  in data: params["weight_svi"]   = float(data["weight_svi"])
            if "num_sites"   in data: params["num_sites"]    = int(data["num_sites"])
            if "percentile"  in data: params["percentile"]   = int(data["percentile"])
            if "region"         in data: params["region"]         = data["region"]
            if "custom_lat_min" in data: params["custom_lat_min"] = float(data["custom_lat_min"])
            if "custom_lat_max" in data: params["custom_lat_max"] = float(data["custom_lat_max"])
            if "custom_lon_min" in data: params["custom_lon_min"] = float(data["custom_lon_min"])
            if "custom_lon_max" in data: params["custom_lon_max"] = float(data["custom_lon_max"])
            if "hm_radius"  in data: params["hm_radius"]  = int(data["hm_radius"])
            if "hm_blur"    in data: params["hm_blur"]    = int(data["hm_blur"])
            if "hm_opacity" in data: params["hm_opacity"] = float(data["hm_opacity"])
            if "min_gps"    in data: params["min_gps"]    = int(data["min_gps"])
            if "min_lst"    in data: params["min_lst"]    = int(data["min_lst"])
            if "max_lst"    in data: params["max_lst"]    = int(data["max_lst"])
        except (ValueError, TypeError) as e:
            return jsonify({"status": "error", "message": str(e)}), 400

    t = threading.Thread(target=run_pipeline_background,
                        kwargs={"params": params, "force": True}, daemon=True)
    t.start()
    return jsonify({"status": "started", "message": "Pipeline started in background."})

# Helper Function
def load_sites():
    """
    Load scored candidate sites from GeoJSON.
    Returns enriched dicts with tier, county estimate, and note.
    """
    path = os.path.join(OUTPUT_DIR, 'scored_sites.geojson')
    if not os.path.exists(path):
        return []

    with open(path) as f:
        data = json.load(f)

    def estimate_county(lat, lon):
        if lat < 38.7:   return "Beaver Co."
        elif lat < 40.0: return "Millard Co."
        elif lat < 40.8: return "Salt Lake Co."
        else:            return "N. Utah"

    def tier(gps):
        if gps >= 85: return "Excellent"
        if gps >= 70: return "Very Good"
        if gps >= 60: return "Good"
        return "Moderate"

    sites = []
    for i, feat in enumerate(data.get('features', [])):
        coords = feat['geometry']['coordinates']
        props  = feat['properties']
        gps    = props.get('GPS', props.get('gps', 50))
        lat, lon = round(coords[1], 4), round(coords[0], 4)
        sites.append({
            "rank":   i + 1,
            "name":   f"Site R-{i+1}",
            "lat":    lat,
            "lon":    lon,
            "gps":    round(float(gps), 1),
            "county": estimate_county(lat, lon),
            "tier":   tier(float(gps)),
            "note":   f"GPS {gps:.1f} — {estimate_county(lat, lon)}",
        })

    sites.sort(key=lambda x: x['gps'], reverse=True)
    for i, s in enumerate(sites):
        s['rank'] = i + 1

    num = int(pipeline.get("params", {}).get("num_sites", 10))
    return sites[:num]

def load_last_params():
    path = os.path.join(OUTPUT_DIR, 'last_run_params.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return DEFAULT_PARAMS.copy()


def check_svi_available():
    svi_path = os.path.join(BASE_DIR, 'data', 'svi_utah')
    return os.path.exists(svi_path) and bool(os.listdir(svi_path))

@app.route('/download')
def download_outputs():
    """Zip and stream outputs/ folder as a download."""
    zip_buffer = io.BytesIO()
    added = 0
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fname in ['sweet_spot_map.html', 'scored_sites.geojson', 'last_run_params.json']:
            fpath = os.path.join(OUTPUT_DIR, fname)
            if os.path.exists(fpath):
                zf.write(fpath, fname)
                added += 1
    if added == 0:
        return jsonify({"error": "No outputs found. Run the pipeline first."}), 404
    zip_buffer.seek(0)
    filename = f"geopulse_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return send_file(zip_buffer, as_attachment=True, download_name=filename, mimetype='application/zip')

# Gemini Chat Route
import urllib.request as _urllib_request

@app.route('/api/chat', methods=['POST'])
def chat():
    """
    Chat messages to Gemini API with current site data as context.
    Set GEMINI_API_KEY as environment variable before starting the server:
        export GEMINI_API_KEY=your_key_here
        python app.py
    """
    GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
    if not GEMINI_KEY:
        return jsonify({"reply": "Gemini API key not configured. Run: export GEMINI_API_KEY=your_key then restart the server."}), 200

    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "")
    sites = load_sites()
    last_params = load_last_params()

    context = f"""You are a geothermal energy analyst assistant for GeoPulse, a satellite-based geothermal site identification tool.

    Current analysis parameters:
    - Date range: {last_params.get('start_date')} to {last_params.get('end_date')}
    - Scoring formula: GPS = {last_params.get('weight_lst',0.5)}*LST + {last_params.get('weight_grid',0.3)}*Grid + {last_params.get('weight_svi',0.2)}*SVI
    - LST = Land Surface Temperature from Landsat 9 satellite (geothermal heat signal)
    - Grid = Population density proxy for transmission infrastructure proximity
    - SVI = CDC Social Vulnerability Index (equity layer — prioritizes underserved communities)

    Top scored sites:
    {chr(10).join([f"  {s['rank']}. {s['name']} — GPS: {s['gps']} at ({s['lat']}, {s['lon']})" for s in sites[:5]])}

    Study region covers Beaver County (validation zone near Utah FORGE EGS facility), Millard County (exploration zone), and Salt Lake County (urban demand center).

    Answer concisely in plain English. Focus on what the data means for real-world geothermal development and climate impact.
    """

    payload = json.dumps({
        "contents": [{"parts": [{"text": context + "\n\nUser question: " + user_message}]}]
    }).encode("utf-8")

    model_id = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent"

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_KEY
    }

    req = _urllib_request.Request(url, data=payload, headers=headers, method="POST")

    try:
        with _urllib_request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            reply = result["candidates"][0]["content"]["parts"][0]["text"]
            return jsonify({"reply": reply})
    except _urllib_request.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"[Gemini] HTTP {e.code}: {body}")
        return jsonify({"reply": f"Gemini error {e.code}: {body}"}), 200
    except Exception as e:
        print(f"[Gemini] Exception: {e}")
        return jsonify({"reply": f"Error: {str(e)}"}), 200

# Run
if __name__ == '__main__':
    print("  GeoPulse - Geothermal Sweet Spot Identifier")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    pipeline["status"] = "idle"
    pipeline["step"]   = "Ready — click Run to start analysis"

    print("[GeoPulse] Server starting at http://0.0.0.0:5001")
    print("[GeoPulse] Open http://localhost:5001 in your browser")
    print("[GeoPulse] On your network: http://<this-device-ip>:5001")
    print("=" * 50)

    # Start Flask
    app.run(host='0.0.0.0', port=5001, debug=False)
