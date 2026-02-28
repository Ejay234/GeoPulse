"""
visualize.py

Geo Pulse - Interactive Map Visualization
Generates a Folium HTML map showing the following:
- LST Heatmap Overlay
- Top 10 Geothermal "sweet spots" sites (candidate locations)
- Soical Vulnerability index (SVI) by census tract
- County boundaries (Beaver, Millard, Salt Lake)
- Existing grid infrastructure (reference layer)

Output: sweet_spot_map.html

@author: Ejay Aguirre
@date: 2026-02-27
"""

import json
import os
import folium
from folium.plugins import HeatMap, MarkerCluster
import geopandas as gpd
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs')
DATA_DIR = os.path.join(BASE_DIR, 'data')

COUNTIES = [
    {"name": "Beaver County", "lat": 38.35, "lon": -113.10, "color": "#d73027",
     "note": "Validation Zone, Utah FORGE EGS research site nearby (Milford)"},
    {"name": "Millard County", "lat": 39.20, "lon": -113.10, "color": "#fc8d59",
     "note": "Exploration Zone, Strong geothermal signals, underdeveloped"},
    {"name": "Salt Lake County", "lat": 40.66, "lon": -111.89, "color": "#fee08b",
     "note": "Demand Center, Urban population, transmission endpoint"},
]

# Simulate LST heatmap data (replace with actual LST data in production)
def generate_sample_heatmap():
    """
    Generate representative LST heatmap points as a demo.
    In production, replace with actual GEE-exported raster values
    """

    np.random.seed(42)
    points = []

    # Beaver County hot zone 
    for _ in range(120):
        lat = np.random.normal(38.5, 0.3)
        lon = np.random.normal(-112.9, 0.3)
        intensity = np.random.uniform(0.6, 1.0)
        points.append([lat, lon, intensity])

    # Millard County moderate zone
    for _ in range(80):
        lat = np.random.normal(39.2, 0.4)
        lon = np.random.normal(-113.1, 0.4)
        intensity = np.random.uniform(0.4, 0.8)
        points.append([lat, lon, intensity])

    # Salt Lake lower thermal, urban heat island only
    for _ in range(40):
        lat = np.random.normal(40.7, 0.2)
        lon = np.random.normal(-111.9, 0.2)
        intensity = np.random.uniform(0.1, 0.35)
        points.append([lat, lon, intensity])

    return points

def generate_sample_sites():
    """
    Generate top 10 candidate sites with GPS scores.
    In production, loaded from outputs/scored_sites.geojson
    """
    sites = [
        # Beaver / Milford area — highest scores
        {"name": "Site B-1", "lat": 38.43, "lon": -113.01, "gps": 91, "county": "Beaver",
         "lst_c": 38.2, "note": "Adjacent to Utah FORGE infrastructure"},
        {"name": "Site B-2", "lat": 38.61, "lon": -112.88, "gps": 87, "county": "Beaver",
         "lst_c": 36.8, "note": "High heat anomaly, rural community nearby"},
        {"name": "Site B-3", "lat": 38.30, "lon": -113.20, "gps": 83, "county": "Beaver",
         "lst_c": 35.1, "note": "Accessible via US-257 corridor"},
        # Millard sites
        {"name": "Site M-1", "lat": 39.17, "lon": -112.95, "gps": 78, "county": "Millard",
         "lst_c": 33.4, "note": "Near Delta, UT — high energy burden community"},
        {"name": "Site M-2", "lat": 39.35, "lon": -113.25, "gps": 74, "county": "Millard",
         "lst_c": 32.0, "note": "Greenfield site, low land use conflict"},
        {"name": "Site M-3", "lat": 38.95, "lon": -112.80, "gps": 71, "county": "Millard",
         "lst_c": 31.2, "note": "Moderate SVI — workforce development opportunity"},
        {"name": "Site M-4", "lat": 39.50, "lon": -113.00, "gps": 68, "county": "Millard",
         "lst_c": 30.5, "note": "Near existing Rocky Mountain Power line"},
        # Connectivity sites (supply → demand corridor)
        {"name": "Site C-1", "lat": 40.05, "lon": -112.50, "gps": 62, "county": "Juab/Tooele",
         "lst_c": 28.1, "note": "Transmission corridor — connects south to SLC"},
        {"name": "Site C-2", "lat": 40.30, "lon": -112.10, "gps": 58, "county": "Tooele",
         "lst_c": 26.3, "note": "Proximity to West Valley City — high SVI"},
        {"name": "Site SL-1", "lat": 40.62, "lon": -112.10, "gps": 51, "county": "Salt Lake",
         "lst_c": 23.8, "note": "Urban fringe — grid connection, lower geothermal signal"},
    ]
    return sites


def score_to_color(gps):
    """
    Map GPS score to color (red = highest potential)
    """
    if gps >= 85:   return '#d73027'  # deep red
    elif gps >= 70: return '#fc8d59'  # orange-red
    elif gps >= 60: return '#fee08b'  # yellow
    else:           return '#91cf60'  # green


def build_map():
    """Build and return the full Folium map."""
    print("Building GeoPulse interactive map...")

    # Center map on study region
    m = folium.Map(
        location=[39.0, -112.8],
        zoom_start=7,
        tiles='CartoDB positron',
        attr='CartoDB'
    )

    # ── Layer 1: Title & Legend ─────────────────────────────────────────────
    title_html = """
    <div style="position: fixed; top: 10px; left: 55px; z-index:1000; background:white;
                padding: 12px 16px; border-radius: 8px; box-shadow: 2px 2px 8px rgba(0,0,0,0.3);
                font-family: Arial, sans-serif; max-width: 280px;">
        <h3 style="margin:0 0 6px 0; color:#d73027;">GeoPulse</h3>
        <p style="margin:0 0 8px 0; font-size:12px; color:#555;">
            Geothermal Sweet Spot Identifier<br>
            <em>Wilkes Center Climate Hackathon 2025</em>
        </p>
        <hr style="margin:6px 0;">
        <b style="font-size:12px;">GPS Score Legend</b><br>
        <span style="color:#d73027;">■</span> 85–100 — Excellent<br>
        <span style="color:#fc8d59;">■</span> 70–84 — Very Good<br>
        <span style="color:#fee08b;">■</span> 60–69 — Good<br>
        <span style="color:#91cf60;">■</span> &lt;60 — Moderate<br>
        <hr style="margin:6px 0;">
        <span style="font-size:11px; color:#888;">
            Formula: GPS = 0.5×LST + 0.3×Grid + 0.2×SVI
        </span>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    # Layer 2: LST Heatmap
    heat_data = generate_sample_heatmap()
    HeatMap(
        heat_data,
        name='Land Surface Temperature (LST)',
        min_opacity=0.3,
        max_zoom=12,
        radius=20,
        blur=15,
        gradient={0.2: 'blue', 0.5: 'yellow', 0.8: 'orange', 1.0: 'red'}
    ).add_to(m)

    # Layer 3: County Labels
    county_layer = folium.FeatureGroup(name='Study Counties')
    for county in COUNTIES:
        folium.Marker(
            location=[county['lat'], county['lon']],
            popup=folium.Popup(
                f"<b>{county['name']}</b><br>{county['note']}",
                max_width=250
            ),
            tooltip=county['name'],
            icon=folium.DivIcon(
                html=f"""<div style="font-size:11px; font-weight:bold; color:{county['color']};
                    background:white; padding:2px 6px; border-radius:4px;
                    border: 2px solid {county['color']}; white-space:nowrap;">
                    {county['name']}</div>""",
                icon_size=(140, 24),
                icon_anchor=(70, 12)
            )
        ).add_to(county_layer)
    county_layer.add_to(m)

    # Layer 4: Sweet Spot Candidate Sites
    sites = generate_sample_sites()

    # Try loading real data if available
    # scored_path = os.path.join(OUTPUT_DIR, 'scored_sites.geojson')
    # if os.path.exists(scored_path):
    #     print("  Loading real scored_sites.geojson...")
    #     with open(scored_path) as f:
    #         real_sites = json.load(f)
            # Parse real features if format matches
            # (extend this block once GEE export is confirmed)
    scored_path = os.path.join(OUTPUT_DIR, 'scored_sites.geojson')
    if os.path.exists(scored_path):
        print("  Loading real scored_sites.geojson...")
        with open(scored_path) as f:
            real_data = json.load(f)
        features = real_data.get('features', [])
        if features:
            sites = []
            for i, feat in enumerate(features[:10]):
                coords = feat['geometry']['coordinates']
                gps = feat['properties'].get('GPS', 50)
                sites.append({
                    "name": f"Site R-{i+1}",
                    "lat": coords[1],
                    "lon": coords[0],
                    "gps": round(gps, 1),
                    "county": "Utah",
                    "lst_c": round(gps * 0.4, 1),
                    "note": f"Real GEE-scored site. GPS: {gps:.1f}"
                })
            print(f"  Using {len(sites)} real sites from GEE.")

    sites_layer = folium.FeatureGroup(name='Geothermal Sweet Spots (Top 10)')
    for i, site in enumerate(sites, 1):
        color = score_to_color(site['gps'])
        popup_html = f"""
        <div style="font-family:Arial; min-width:200px;">
            <h4 style="margin:0 0 6px 0; color:{color};">
                #{i} {site['name']}
            </h4>
            <table style="font-size:12px; width:100%;">
                <tr><td><b>GPS Score</b></td><td><b style="color:{color};">{site['gps']}/100</b></td></tr>
                <tr><td>County</td><td>{site['county']}</td></tr>
                <tr><td>LST</td><td>{site['lst_c']}°C</td></tr>
                <tr><td>Coords</td><td>{site['lat']:.3f}, {site['lon']:.3f}</td></tr>
            </table>
            <p style="font-size:11px; color:#666; margin:6px 0 0 0;">💡 {site['note']}</p>
        </div>
        """
        folium.CircleMarker(
            location=[site['lat'], site['lon']],
            radius=12 + (site['gps'] - 50) / 10,  # larger = higher score
            color='white',
            weight=2,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=f"#{i} {site['name']} — GPS: {site['gps']}"
        ).add_to(sites_layer)

        # Rank number label
        folium.Marker(
            location=[site['lat'], site['lon']],
            icon=folium.DivIcon(
                html=f'<div style="font-size:9px; font-weight:bold; color:white; '
                    f'text-align:center; line-height:18px;">#{i}</div>',
                icon_size=(18, 18),
                icon_anchor=(9, 9)
            )
        ).add_to(sites_layer)

    sites_layer.add_to(m)

    # Layer 5: Utah FORGE Reference Marker
    forge_layer = folium.FeatureGroup(name='Utah FORGE Reference Site')
    folium.Marker(
        location=[38.507, -112.893],
        popup=folium.Popup(
            "<b>Utah FORGE</b><br>Enhanced Geothermal Systems<br>"
            "research facility — Milford, UT<br>"
            "<em>GeoPulse validation baseline</em>",
            max_width=220
        ),
        tooltip="Utah FORGE — Validation Baseline",
        icon=folium.Icon(color='red', icon='flash', prefix='fa')
    ).add_to(forge_layer)
    forge_layer.add_to(m)

    # Layer Control
    folium.LayerControl(collapsed=False).add_to(m)

    return m

# Run
if __name__ == "__main__":

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    m = build_map()
    out_path = os.path.join(OUTPUT_DIR, 'sweet_spot_map.html')
    m.save(out_path)

    print(f"Map savied to {out_path}")
    print("Open this file in a web browser to explore the interactive GeoPulse map!")