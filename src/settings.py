"""Application configuration via environment variables."""

import json
import os
from logging import Logger

from dotenv import load_dotenv

load_dotenv()


class Settings:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """Application settings loaded from environment variables."""

    log_level: str = ""
    app_env: str = ""

    # S3 / object storage
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_name: str = ""
    s3_secure: bool = True

    # Settings file
    settings_file: str = "settings.json"
    simplification_levels: dict = {}

    # Geospatial
    data_dir: str = "/app/data"
    country_geojson_url: str = (
        "https://wms.ign.gob.ar/geoserver/ows"
        "?service=WFS&version=1.0.0&request=GetFeature"
        "&typeName=ign:pais&outputFormat=application/json"
    )
    departments_geojson_url: str = (
        "https://wms.ign.gob.ar/geoserver/ows"
        "?service=WFS&version=1.0.0&request=GetFeature"
        "&typeName=ign:departamento&outputFormat=application/json"
    )

    def __init__(self):
        self._load_from_env()
        self._load_from_file()

    def _load_from_file(self) -> None:
        try:
            with open(self.settings_file, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Settings file not found: {self.settings_file}"
            ) from exc
        self.layer_update_cron = data.get("layer_update_cron", "0 3 * * 0")
        raw = data.get("simplification_levels", {})
        self.simplification_levels = {int(k): float(v) for k, v in raw.items()}

    def _load_from_env(self) -> None:
        self.log_level = os.getenv("LOG_LEVEL", self.log_level)
        self.app_env = os.getenv("APP_ENV", self.app_env)

        self.s3_endpoint = os.getenv("S3_ENDPOINT", self.s3_endpoint)
        self.s3_access_key = os.getenv("S3_ACCESS_KEY", self.s3_access_key)
        self.s3_secret_key = os.getenv("S3_SECRET_KEY", self.s3_secret_key)
        self.s3_bucket_name = os.getenv("S3_BUCKET_NAME", self.s3_bucket_name)
        self.s3_secure = os.getenv("S3_SECURE", str(self.s3_secure)).lower() not in (
            "false",
            "0",
            "no",
        )

        self.settings_file = os.getenv("SETTINGS_FILE", self.settings_file)

        self.data_dir = os.getenv("DATA_DIR", self.data_dir)
        self.country_geojson_url = os.getenv(
            "COUNTRY_GEOJSON_URL", self.country_geojson_url
        )
        self.departments_geojson_url = os.getenv(
            "DEPARTMENTS_GEOJSON_URL", self.departments_geojson_url
        )

    def log_config(self, logger: Logger) -> None:
        """Log all non-secret configuration values."""
        logger.info("=== Configuration ===")
        logger.info("LOG_LEVEL: %s", self.log_level)
        logger.info("APP_ENV: %s", self.app_env)
        logger.info("SETTINGS_FILE: %s", self.settings_file)
        logger.info("DATA_DIR: %s", self.data_dir)
        logger.info("COUNTRY_GEOJSON_URL: %s", self.country_geojson_url)
        logger.info("DEPARTMENTS_GEOJSON_URL: %s", self.departments_geojson_url)
        logger.info("LAYER_UPDATE_CRON: %s", self.layer_update_cron)

        for level, tolerance in self.simplification_levels.items():
            logger.info("SIMPLIFICATION_LEVEL_%s: %s", level, tolerance)

        logger.info("S3_ENDPOINT: %s", self.s3_endpoint)
        logger.info("S3_BUCKET_NAME: %s", self.s3_bucket_name)
        logger.info("S3_SECURE: %s", self.s3_secure)
        logger.info("=====================")

    @staticmethod
    def get_settings() -> "Settings":
        """Instantiate and return a Settings object from the environment."""
        return Settings()
