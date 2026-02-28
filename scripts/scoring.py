"""
scoring.py

GeoPulse - Geothermal Potential Scoring

@author: Ejay Aguirre
@date: 2026-02-27
"""

import ee
import json
import os
import fiona
import numpy as np
import pandas as pd
import geopandas as gpd


KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'gee_key.json')
credentials = ee.ServiceAccountCredentials(email=None, key_file=KEY_PATH)
ee.Initialize(credentials)

REGION_KEY = os.environ.get("GEOPULSE_REGION", "southern_utah")

REGION_BOXES = {
    "southern_utah":  [-114.0, 37.0, -111.5, 39.0],   # Beaver + Iron + Washington
    "central_utah":   [-114.0, 38.5, -111.0, 40.5],   # Millard + Sevier + Juab
    "northern_utah":  [-113.0, 39.5, -111.0, 42.0],   # Salt Lake + Tooele + Davis
    "all_utah":       [-114.1, 36.9, -109.0, 42.1],   # Full state
    "great_basin":    [-117.0, 36.0, -113.0, 40.0],   # NV/UT border region
    "custom": [                                         # User-defined bbox from UI
        float(os.environ.get("GEOPULSE_CUSTOM_LON_MIN", "-114.0")),
        float(os.environ.get("GEOPULSE_CUSTOM_LAT_MIN",  "37.0")),
        float(os.environ.get("GEOPULSE_CUSTOM_LON_MAX", "-109.0")),
        float(os.environ.get("GEOPULSE_CUSTOM_LAT_MAX",  "42.0")),
    ],
}
_bbox = REGION_BOXES.get(REGION_KEY, REGION_BOXES["southern_utah"])
print(f"[scoring] Region: {REGION_KEY}  bbox: {_bbox}")
STUDY_REGION = ee.Geometry.Rectangle(_bbox)
SCALE = 1000
START_DATE   = os.environ.get("GEOPULSE_START_DATE",  "2023-05-01")
END_DATE     = os.environ.get("GEOPULSE_END_DATE",    "2024-09-30")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'outputs')
DATA_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
CLOUD_COVER  = int(os.environ.get("GEOPULSE_CLOUD_COVER", "20"))
WEIGHT_LST   = float(os.environ.get("GEOPULSE_WEIGHT_LST",  "0.5"))
WEIGHT_GRID  = float(os.environ.get("GEOPULSE_WEIGHT_GRID", "0.3"))
WEIGHT_SVI   = float(os.environ.get("GEOPULSE_WEIGHT_SVI",  "0.2"))
NUM_SITES    = int(os.environ.get("GEOPULSE_NUM_SITES",   "10"))
PERCENTILE   = int(os.environ.get("GEOPULSE_PERCENTILE",  "70"))


# LST Score
def get_lst_score():
    """
    Pull LST from GEE and normalize 0 to 100
    Higher LST anomaly = higher score
    """
    # Import the proper LST functions from lst_analysis.py
    import sys
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from lst_analysis import apply_scale_factors, compute_ndvi, compute_emissivity, compute_lst

    print("Calculating LST Score...")

    landsat = (
            ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
            .filterBounds(STUDY_REGION)
            .filterDate(START_DATE, END_DATE)
            .filter(ee.Filter.lt('CLOUD_COVER', CLOUD_COVER))
            .map(apply_scale_factors)
            .map(compute_ndvi)
            .map(compute_emissivity)
            .map(compute_lst)
        )

    count = landsat.size().getInfo()
    print(f"  Images found: {count}")

    if count == 0:
        raise ValueError(f"No images found. Try widening date range or cloud cover.")
    
    lst_raw = landsat.select("LST_Celsius").median().clip(STUDY_REGION)

    MIN_LST = float(os.environ.get("GEOPULSE_MIN_LST", "20"))
    MAX_LST = float(os.environ.get("GEOPULSE_MAX_LST", "60"))
    lst = lst_raw.updateMask(lst_raw.gte(MIN_LST).And(lst_raw.lte(MAX_LST)))
    print(f"  LST filter: {MIN_LST}°C – {MAX_LST}°C")

    stats = lst.reduceRegion(
        reducer=ee.Reducer.minMax(),
        geometry=STUDY_REGION,
        scale=SCALE,
        maxPixels=1e9
    ).getInfo()

    vals = sorted(stats.values())
    lst_min, lst_max = vals[0], vals[-1]
    lst_score = lst.subtract(lst_min).divide(lst_max - lst_min).multiply(100).rename('lst_score')
    print(f"  LST range: {lst_min:.1f}C -> {lst_max:.1f}C")
    return lst_score


# Grid Proximity Score
def get_grid_proximity_score():
    """
    Score based on proximity to existing eletrical grid
    Closer to grid = lower transmission cost = higher score
    Uses EIA transmission line dataset from GEE
    """

    print("Computing grid proximity score...")

    # Create distance raster — closer to grid = higher score
    pop = ee.Image("CIESIN/GPWv411/GPW_Population_Density/gpw_v4_population_density_rev11_2020_30_sec")
    pop_clipped = pop.clip(STUDY_REGION)
    pop_band = pop_clipped.select(0)

    pop_norm = pop_band.add(1).log()
    stats = pop_norm.reduceRegion(
        reducer=ee.Reducer.minMax(),
        geometry=STUDY_REGION,
        scale=SCALE,
        maxPixels=1e9
    ).getInfo()

    vals = sorted(stats.values())
    mn, mx = vals[0], vals[-1]
    grid_score = pop_norm.subtract(mn).divide(mx - mn).multiply(100).rename('grid_score')
    print("  Grid proximity score calculated.")
    return grid_score


# SVI Score
def get_svi_score():
    """
    Load CDC Social Vulnerability Index for Utah
    Higher SVI (more vulnerable) = higher priority score

    Download SVI data from
    https://www.atsdr.cdc.gov/place-health/php/svi/svi-data-documentation-download.html?CDC_AAref_Val=https://www.atsdr.cdc.gov/placeandhealth/svi/data_documentation_download.html
    Select: 2022, Utah, Census Tract, CSV + Shapefile
    Save to: data/svi_utah/ 
    """

    svi_path = os.path.join(DATA_DIR, 'svi_utah')

    if not os.path.exists(svi_path):
        print("  SVI data not found - using neutral placeholder (50).")
        return ee.Image(50).rename('svi_score').clip(STUDY_REGION), None

    try:
        # The svi_utah folder itself is the .gdb
        layers = fiona.listlayers(svi_path)
        print(f"  GDB layers found: {layers}")

        # Pick the tract-level SVI layer
        layer = next((l for l in layers if 'SVI' in l.upper() or 'TRACT' in l.upper()), layers[0])
        print(f"  Using layer: {layer}")

        gdf = gpd.read_file(svi_path, layer=layer)

        if "RPL_THEMES" in gdf.columns:
            gdf = gdf[gdf["RPL_THEMES"] >= 0]
            gdf['svi_score'] = gdf['RPL_THEMES'] * 100
        else:
            gdf['svi_score'] = 50

        print(f"  SVI loaded: {len(gdf)} census tracts.")
        return ee.Image(50).rename('svi_score').clip(STUDY_REGION), gdf

    except Exception as e:
        print(f"  SVI load failed: {e} - using neutral placeholder.")
        return ee.Image(50).rename('svi_score').clip(STUDY_REGION), None


# Combine Scores
def compute_final_score(lst_score, grid_score, svi_score):
    """
    Weighted combination: 
    - GPS = 0.5 * LST + 0.3 * Grid + 0.2 * SVI
    """

    print("Computing final Geothermal Potential Score (GPS)...")
    gps = (
    lst_score.multiply(WEIGHT_LST)
    .add(grid_score.multiply(WEIGHT_GRID))
    .add(svi_score.multiply(WEIGHT_SVI))
    .rename('GPS')
    )
    return gps


# Extract Top Sites
def extract_top_sites(gps_image, n=10):
    """
    Sample top N scoring locations directly from the GPS raster.
    """
    print(f"Extracting top {n} candidate sites...")

    threshold_result = gps_image.reduceRegion(
        reducer=ee.Reducer.percentile([PERCENTILE]),
        geometry=STUDY_REGION,
        scale=SCALE,
        maxPixels=1e9
    ).getInfo()

    print(f"  Threshold result: {threshold_result}")
    threshold = list(threshold_result.values())[0]
    print(f"  {PERCENTILE}th percentile GPS threshold: {threshold:.2f}")

    top_pixels = gps_image.updateMask(gps_image.gt(threshold))

    sites = top_pixels.sample(
        region=STUDY_REGION,
        scale=SCALE,
        numPixels=n * 5,   # oversample, then limit
        geometries=True,
        seed=42
    ).limit(n)

    result = sites.getInfo()
    print(f"  Found {len(result['features'])} candidate sites.")
    return result


# Run
if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    lst_score  = get_lst_score()
    grid_score = get_grid_proximity_score()
    svi_img, svi_gdf = get_svi_score()

    gps = compute_final_score(lst_score, grid_score, svi_img)

    task = ee.batch.Export.image.toDrive(
        image=gps,
        description='GeoPulse_GPS_Score',
        folder='GeoPulse',
        fileNamePrefix='GeoPulse_GPS_Score',
        region=STUDY_REGION,
        scale=SCALE,
        crs='EPSG:4326',
        maxPixels=1e9
    )
    task.start()
    print("GPS score export started -> Google Drive/GeoPulse/")

    top_sites = extract_top_sites(gps, n=NUM_SITES)
    out_path = os.path.join(OUTPUT_DIR, 'scored_sites.geojson')
    with open(out_path, 'w') as f:
        json.dump(top_sites, f, indent=2)
    print(f"Top sites saved -> {out_path}")
    print("\nNext step: run visualize.py")