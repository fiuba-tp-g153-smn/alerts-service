# Alerts Service Tests

## API Integration Test

### Quick Start

```bash
cd /path/to/alerts-service/tests
python3 test_alerts_api.py
```

### Requirements

```bash
pip install requests
```

### What It Does

The test script:

1. Loads the test polygon from `test_polygon.json`
2. Tests both endpoints (`/intersect-country` and `/intersect-departments`)
3. Tests both simplified and full resolution versions
4. Measures response times and compares performance
5. Saves all results to files

### Output Files

**Country Intersection:**

- `country_simplified.json` - GeoJSON with 1% simplification
- `country_full.json` - GeoJSON with full resolution

**Departments Intersection:**

- `departments_simplified.geojson` - GeoJSON FeatureCollection (1% simplification) ⭐
- `departments_full.geojson` - GeoJSON FeatureCollection (full resolution) ⭐
- `departments_simplified_raw.json` - Raw API response
- `departments_full_raw.json` - Raw API response

### Test Polygon

The `test_polygon.json` file contains a test polygon in the Misiones region of Argentina. You can replace it with your own polygon in GeoJSON format (FeatureCollection, Feature, or Geometry).

### Expected Results

- **Simplified version**: 25-56x faster, excellent quality for web/mobile apps
- **Full version**: Maximum detail for scientific/precision analysis
- **Departments found**: ~13 departments in the test region

### Customization

Edit the script to change:

- `API_BASE_URL`: Default is `http://localhost:8080`
- `TEST_POLYGON_PATH`: Default is `./test_polygon.json`
