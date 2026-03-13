"""Shared geospatial utility functions."""


def build_department_features(intersecting) -> list[dict]:
    """Build a list of department feature dicts from an intersecting GeoDataFrame."""
    features = []
    for _, row in intersecting.iterrows():
        features.append(
            {
                "properties": {
                    k: row[k]
                    for k in row.index
                    if k not in ("geometry", "intersection")
                },
                "geometry": row["geometry"].__geo_interface__,
                "intersection": row["intersection"].__geo_interface__,
            }
        )
    return features
