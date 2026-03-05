#!/usr/bin/env python3
"""
Test script for alerts-service geo intersection endpoints
Usage: python3 test_alerts_api.py
"""

import json
import time
import requests
from pathlib import Path
import os

# Configuration
API_BASE_URL = "http://localhost:8080"
SCRIPT_DIR = Path(__file__).parent
TEST_POLYGON_PATH = SCRIPT_DIR / "test_polygon.json"

# Colors for terminal output
GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def load_test_polygon():
    """Load the test polygon from file"""
    with open(TEST_POLYGON_PATH, "r") as f:
        return json.load(f)


def convert_departments_to_geojson(departments_result):
    """Convert departments API response to GeoJSON FeatureCollection"""
    if not departments_result or "departments" not in departments_result:
        return None

    features = []
    for dept in departments_result["departments"]:
        feature = {
            "type": "Feature",
            "properties": dept["properties"],
            "geometry": dept["intersection"],  # Use intersection geometry
        }
        features.append(feature)

    return {"type": "FeatureCollection", "features": features}


def test_endpoint(endpoint, payload, use_simplified=True):
    """Test an endpoint and return timing + result"""
    url = f"{API_BASE_URL}/{endpoint}"
    params = {"use_simplified": str(use_simplified).lower()}

    print(f"\n{BLUE}Testing: {endpoint} (simplified={use_simplified}){RESET}")
    print("-" * 70)

    start = time.time()
    try:
        response = requests.post(url, json=payload, params=params, timeout=30)
        elapsed = time.time() - start

        if response.status_code == 200:
            result = response.json()
            print(f"{GREEN}✓ Success{RESET}")
            print(f"  Response time: {elapsed:.3f}s")

            # Show result details
            if endpoint == "intersect-country":
                if "features" in result:
                    points = len(result["features"][0]["geometry"]["coordinates"][0])
                    print(f"  Result points: {points}")
            elif endpoint == "intersect-departments":
                if "departments" in result:
                    count = len(result["departments"])
                    print(f"  Departments found: {count}")
                    for dept in result["departments"]:
                        name = dept["properties"].get("nam", "Unknown")
                        print(f"    - {name}")

            return elapsed, result
        else:
            print(f"{YELLOW}✗ Error: {response.status_code}{RESET}")
            print(f"  {response.text}")
            return elapsed, None
    except Exception as e:
        elapsed = time.time() - start
        print(f"{YELLOW}✗ Exception: {e}{RESET}")
        return elapsed, None


def main():
    print("=" * 70)
    print("ALERTS-SERVICE API TEST")
    print("=" * 70)

    # Load test polygon
    print(f"\nLoading test polygon from: {TEST_POLYGON_PATH}")
    try:
        test_polygon = load_test_polygon()
        print(f"{GREEN}✓ Polygon loaded{RESET}")
    except Exception as e:
        print(f"{YELLOW}✗ Failed to load polygon: {e}{RESET}")
        return

    # Test country intersection - Simplified
    elapsed_country_simp, result_country_simp = test_endpoint(
        "intersect-country", test_polygon, use_simplified=True
    )

    # Test country intersection - Full
    elapsed_country_full, result_country_full = test_endpoint(
        "intersect-country", test_polygon, use_simplified=False
    )

    # Test departments intersection - Simplified
    elapsed_dept_simp, result_dept_simp = test_endpoint(
        "intersect-departments", test_polygon, use_simplified=True
    )

    # Test departments intersection - Full
    elapsed_dept_full, result_dept_full = test_endpoint(
        "intersect-departments", test_polygon, use_simplified=False
    )

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    if result_country_simp and result_country_full:
        speedup = elapsed_country_full / elapsed_country_simp
        print(f"\nCountry Intersection:")
        print(f"  Simplified: {elapsed_country_simp:.3f}s")
        print(f"  Full:       {elapsed_country_full:.3f}s")
        print(f"  Speedup:    {speedup:.1f}x faster")

    if result_dept_simp and result_dept_full:
        speedup = elapsed_dept_full / elapsed_dept_simp
        print(f"\nDepartments Intersection:")
        print(f"  Simplified: {elapsed_dept_simp:.3f}s")
        print(f"  Full:       {elapsed_dept_full:.3f}s")
        print(f"  Speedup:    {speedup:.1f}x faster")

    # Save results
    output_dir = SCRIPT_DIR
    if result_country_simp:
        with open(output_dir / "country_simplified.json", "w") as f:
            json.dump(result_country_simp, f, indent=2)
    if result_country_full:
        with open(output_dir / "country_full.json", "w") as f:
            json.dump(result_country_full, f, indent=2)

    # Save departments as GeoJSON FeatureCollection
    if result_dept_simp:
        # Save raw API response
        with open(output_dir / "departments_simplified_raw.json", "w") as f:
            json.dump(result_dept_simp, f, indent=2)
        # Save as GeoJSON FeatureCollection
        geojson = convert_departments_to_geojson(result_dept_simp)
        if geojson:
            with open(output_dir / "departments_simplified.geojson", "w") as f:
                json.dump(geojson, f, indent=2)

    if result_dept_full:
        # Save raw API response
        with open(output_dir / "departments_full_raw.json", "w") as f:
            json.dump(result_dept_full, f, indent=2)
        # Save as GeoJSON FeatureCollection
        geojson = convert_departments_to_geojson(result_dept_full)
        if geojson:
            with open(output_dir / "departments_full.geojson", "w") as f:
                json.dump(geojson, f, indent=2)

    print(f"\n{GREEN}✓ Results saved to {output_dir}{RESET}")
    print("  - Country: country_simplified.json, country_full.json")
    print(
        "  - Departments (GeoJSON): departments_simplified.geojson, departments_full.geojson"
    )
    print(
        "  - Departments (raw): departments_simplified_raw.json, departments_full_raw.json"
    )
    print("=" * 70)


if __name__ == "__main__":
    main()
