"""
lst_analysis.py

Geo Pulse - Land Surface Temperature Analysis

Uses Google Earth Engine (Landsat 8/9) to compute LST for the study region..
Exports results as a GEO TIFF to Google Drive for use in scoring.py

Study Region:
- Beaver County, Utah (validation)
- Millard County, Utah (exploration)
- Salt Lake County, Utah (urban demand center)

@author: Ejay Aguirre
@date: 2026-02-27
"""

from email.mime import image

import ee
import json
import os as _os

# Authenticate and initialize the Earth Engine client
_REGION_KEY = _os.environ.get("GEOPULSE_REGION", "southern_utah")
_REGION_BOXES = {
    "southern_utah":  [-114.0, 37.0, -111.5, 39.0],
    "central_utah":   [-114.0, 38.5, -111.0, 40.5],
    "northern_utah":  [-113.0, 39.5, -111.0, 42.0],
    "all_utah":       [-114.1, 36.9, -109.0, 42.1],
    "great_basin":    [-117.0, 36.0, -113.0, 40.0],
    "custom": [
        float(_os.environ.get("GEOPULSE_CUSTOM_LON_MIN", "-114.0")),
        float(_os.environ.get("GEOPULSE_CUSTOM_LAT_MIN",  "37.0")),
        float(_os.environ.get("GEOPULSE_CUSTOM_LON_MAX", "-109.0")),
        float(_os.environ.get("GEOPULSE_CUSTOM_LAT_MAX",  "42.0")),
    ],
}
_bbox = _REGION_BOXES.get(_REGION_KEY, _REGION_BOXES["southern_utah"])
STUDY_REGION = ee.Geometry.Rectangle(_bbox)
print(f"[lst_analysis] Region: {_REGION_KEY}  bbox: {_bbox}")

COUNTIES = {
    "Beaver County": ee.Geometry.Point(-112.641, 38.276),
    "Millard County": ee.Geometry.Point(-113.000, 39.000),
    "Salt Lake County": ee.Geometry.Point(-111.893, 40.760)
}

def compute_ndvi(image):
    """
    Compute NDVI for a given Landsat image.

    Formula: NDVI = (NIR - Red) / (NIR + Red)
    - NIR: Band 5 (SR_B5)
    - Red: Band 4 (SR_B4)

    Values:
    < 0.2 = Bare Soil / Rock
    0.2-0.5 = Mixed Vegetation
    > 0.5 = Dense Vegetation
    """
    nir = image.select('SR_B5')
    red = image.select('SR_B4')
    ndvi = nir.subtract(red).divide(nir.add(red)).rename('NDVI')
    return image.addBands(ndvi)


def compute_emissivity(image):
    """
    Estimate surface emissivity based on NDVI.

    Method: Sobrino et al. (2004), Linear relationship between NDVI and emissivity
    - Bare SoiL: Emissivity ~ 0.979
    - Vegetation: Emissivity ~ 0.986
    - Mixed: Emissivity ~ 0.977 + 0.119 * (NDVI fraction)
    - Formula: Emissivity = 0.004 * NDVI + 0.986
    """
    ndvi = image.select('NDVI')

    fv = ndvi.subtract(0.2).divide(0.3).pow(2).rename("FV") # Fractional Vegatation

    emissivity = (
        ee.Image(0.979)
        .where(ndvi.gt(0.5), 0.986)
        .where(ndvi.gte(0.2).And(ndvi.lte(0.5)),
            fv.multiply(0.119).add(0.977))
        .rename("emissivity")
        )

    return image.addBands(emissivity)

def compute_lst(image):
    """
    Derive Land Surface Temperature (LST) in Celsius from Landsat Band 10.

    Single-channel method using the formula:
    LST = TB / (1 + (lambda * TB / rho) * ln(epsilon)) - 273.15

    Where:
    - TB: Brightness Temperature (Kelvin) from Band 10
    - lambda: 10.895 um (Landsat 9 Band 10 central wavelength)
    - rho: 14388 um*K (Planck radiation constant)
    - epsilon: Surface emissivity (from compute_emissivity)
    """

    LAMBDA = 10.895 # micrometers
    RHO = 14388.0 # mu(meters) * K

    tb = image.select("ST_B10").multiply(0.00341803).add(149.0).rename("TB") # Convert to Kelvin

    emissivity = image.select("emissivity")
    
    # LST formula
    lst = tb.divide(
        ee.Image(1.0).add(
            tb.multiply(LAMBDA / RHO).multiply(emissivity.log())
        )
    ).subtract(273.15).rename("LST_Celsius")

    return image.addBands(lst)

def apply_scale_factors(image):
    """
    Apply offical USGS Landsat Collection 2 surface reflectance scale factors
    
    Raw Landsat digital numbers must be scaled before analysis:
    - Optical bands = value * 0.0000275 + (-0.2)
    - Thermal band = kept raw
    """

    optical = image.select("SR_B.").multiply(0.0000275).add(-0.2)
    thermal = image.select("ST_B10")
    return image.addBands(optical, None, True).addBands(thermal, None, True)

# Main Analysis Function
def run_lst_analysis(start_date = '2023-05-1', end_date = '2024-09-30'):
    """
    Runs the full LST analysis popeline over the study region.

    Uses summer motnsh to maximize thermal constrast between geothermally active and inactive areas
    """
    print(f"Running LST analysis for {start_date} to {end_date}...")

    landsat = (
        ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
        .filterBounds(STUDY_REGION)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt('CLOUD_COVER', 20)) # Less than 20% cloud cover
        .map(apply_scale_factors)
        .map(compute_ndvi)
        .map(compute_emissivity)
        .map(compute_lst)
    )

    image_count = landsat.size().getInfo()
    print(f"Images Found: {image_count}")

    if image_count == 0:
        raise ValueError("Error: No suitable Landsat images found for the specified date range and region.")
    
    lst_composite = landsat.select("LST_Celsius").median().clip(STUDY_REGION)

    stats = lst_composite.reduceRegion(
        reducer = ee.Reducer.minMax().combine(ee.Reducer.mean(), sharedInputs=True),
        geometry = STUDY_REGION,
        scale = 100,
        maxPixels = 1e9
    ).getInfo()

    print(f"LST Stats (raw): {stats}")
    return lst_composite

def export_to_drive(image, filename='GeoPulse_LST_Utah'):
    """
    Export LST image to Google Drive as GeoTIFF for use in scoring.py
    """
    task = ee.batch.Export.image.toDrive(
        image=image,
        description=filename,
        folder='GeoPulse',
        fileNamePrefix=filename,
        region=STUDY_REGION,
        scale=100,          # 100m resolution
        crs='EPSG:4326',
        maxPixels=1e9
    )
    task.start()
    print(f"\nExport started: '{filename}' → Google Drive/GeoPulse/")
    print("   Check progress at: https://code.earthengine.google.com/tasks")
    return task


# ── Run ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    lst_image = run_lst_analysis()
    export_to_drive(lst_image)
    print("\nDone! Wait for export to complete, then run scoring.py")
