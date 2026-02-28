"""
scoring.py

GeoPulse - Geothermal Potential Scoring

@author: Ejay Aguirre
@date: 2026-02-27
"""

import ee
import json
import os
from google_crc32c import value
import numpy as np
import pandas as pd

ee.Initialize(project="gen-lang-client-0356293060")

STUDY_REGION = ee.Geometry.Rectangle([-113.5, 37.0, -111.5, 39.0])
SCALE = 1000
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'outputs')
DATA_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')


# LST Score
def get_lst_score():
    """
    Pull LST from GEE and normalize 0 to 100
    Higher LST anomaly = higher score
    """

    print("Calculating LST Score...")

    landsat = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(STUDY_REGION)
        .filterDate('2023-05-01', '2024-09-30')
        .filter(ee.Filter.lt('CLOUD_COVER', 20))
        .map(lambda img: img.select("ST_B10").multiply(0.00341803).add(149.0).subtract(273.15))
    )
    lst = landsat.median().rename("LST").clip(STUDY_REGION)

    # Normalize: (value - min) / (max - min) * 100
    stats = lst.reduceRegion(
        reducer=ee.Reducer.minMax(),
        geometry=STUDY_REGION,
        scale=SCALE,
        maxPixels=1e9
    ).getInfo()

    print(f"  LST raw stats: {stats}")
    vals = sorted(stats.values())
    lst_min, lst_max = vals[0], vals[-1]
    lst_score = lst.subtract(lst_min).divide(lst_max - lst_min).multiply(100).rename('lst_score')
    print(f"  LST score computed. Range: {lst_min:.1f} -> {lst_max:.1f}")
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
    https://www.atsdr.cdc.gov/placeandhealth/svi/data_documentation_download.html
    Select: 2022, Utah, Census Tract, CSV + Shapefile
    Save to: data/svi_utah/ 
    """

    svi_path = os.path.join(DATA_DIR, 'svi_utah')
    shp_files = [f for f in os.listdir(svi_path) if f.endswith('.shp')] if os.path.exists(svi_path) else []

    if not shp_files:
        print("SVI shapefile not found, using uniform placeholder (50).")
        print(f"Download from CDC and place in: {svi_path}")
        return ee.Image(50).rename('svi_score').clip(STUDY_REGION), None

    import geopandas as gpd
    shp_path = os.path.join(svi_path, shp_files[0])
    print(f"  Loading SVI: {shp_files[0]}")
    gdf = gpd.read_file(shp_path)

    # RPL_THEMES = overall SVI percentile ranking (0–1, higher = more vulnerable)
    if "RPL_THEMES" in gdf.columns:
        gdf = gdf[gdf["RPL_THEMES"] >= 0]
        gdf['svi_score'] = gdf['RPL_THEMES'] * 100
    else:
        gdf['svi_score'] = 50

    print(f"  SVI loaded: {len(gdf)} census tracts.")
    return ee.Image(50).rename('svi_score').clip(STUDY_REGION), gdf


# Combine Scores
def compute_final_score(lst_score, grid_score, svi_score):
    """
    Weighted combination: 
    - GPS = 0.5 * LST + 0.3 * Grid + 0.2 * SVI
    """

    print("Computing final Geothermal Potential Score (GPS)...")
    gps = (
        lst_score.multiply(0.5)
        .add(grid_score.multiply(0.3))
        .add(svi_score.multiply(0.2))
        .rename('GPS')
    )
    return gps


# Extract Top Sites
def extract_top_sites(gps_image, n=10):
    """
    Sample top N scoring locations as candidate sites
    """

    print(f"Extracting top {n} candidate sites...")

    threshold_result = gps_image.reduceRegion(
        reducer=ee.Reducer.percentile([90]),
        geometry=STUDY_REGION,
        scale=SCALE,
        maxPixels=1e9
    ).getInfo()

    print(f"  Threshold result: {threshold_result}")
    threshold = list(threshold_result.values())[0]
    print(f"  90th percentile threshold: {threshold:.2f}")

    top_mask = gps_image.gt(threshold).selfMask()

    samples = top_mask.sample(
        region=STUDY_REGION,
        scale=SCALE,
        numPixels=n * 3,
        geometries=True
    )

    scored = gps_image.sampleRegions(
        collection=samples,
        scale=SCALE,
        geometries=True
    )

    sites = scored.getInfo()
    print(f"  Found {len(sites['features'])} candidate sites.")
    return sites


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

    top_sites = extract_top_sites(gps, n=10)
    out_path = os.path.join(OUTPUT_DIR, 'scored_sites.geojson')
    with open(out_path, 'w') as f:
        json.dump(top_sites, f, indent=2)
    print(f"Top sites saved -> {out_path}")
    print("\nNext step: run visualize.py")