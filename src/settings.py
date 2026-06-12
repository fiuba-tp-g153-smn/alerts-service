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
    detail_levels: dict = {}

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
    provinces_geojson_url: str = (
        "https://wms.ign.gob.ar/geoserver/ows"
        "?service=WFS&version=1.0.0&request=GetFeature"
        "&typeName=ign:provincia&outputFormat=application/json"
    )

    # MySQL Database (primary)
    mysql_host: str = ""
    mysql_port: int = 3306
    mysql_database: str = ""
    mysql_user: str = ""
    mysql_password: str = ""

    # MySQL Database (external taviso, read-only)
    mysql_taviso_host: str = ""
    mysql_taviso_port: int = 3306
    mysql_taviso_database: str = ""
    mysql_taviso_user: str = ""
    mysql_taviso_password: str = ""

    # Alert Generation
    output_dir: str = ""
    alert_cache_dir: str = ""
    alert_detail_level: int = 7

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
        self.layer_cache_ttl_minutes: int = int(data.get("layer_cache_ttl_minutes", 30))
        raw = data.get("detail_levels", {})
        self.detail_levels = {int(k): float(v) for k, v in raw.items()}
        self.alert_detail_level = int(data.get("alert_detail_level", 7))

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
        self.provinces_geojson_url = os.getenv(
            "PROVINCES_GEOJSON_URL", self.provinces_geojson_url
        )

        self.mysql_host = os.getenv("MYSQL_HOST", self.mysql_host)
        self.mysql_port = int(os.getenv("MYSQL_PORT", str(self.mysql_port)))
        self.mysql_database = os.getenv("MYSQL_DATABASE", self.mysql_database)
        self.mysql_user = os.getenv("MYSQL_USER", self.mysql_user)
        self.mysql_password = os.getenv("MYSQL_PASSWORD", self.mysql_password)

        self.mysql_taviso_host = os.getenv("MYSQL_TAVISO_HOST", self.mysql_taviso_host)
        self.mysql_taviso_port = int(
            os.getenv("MYSQL_TAVISO_PORT", str(self.mysql_taviso_port))
        )
        self.mysql_taviso_database = os.getenv(
            "MYSQL_TAVISO_DATABASE", self.mysql_taviso_database
        )
        self.mysql_taviso_user = os.getenv("MYSQL_TAVISO_USER", self.mysql_taviso_user)
        self.mysql_taviso_password = os.getenv(
            "MYSQL_TAVISO_PASSWORD", self.mysql_taviso_password
        )

        self.output_dir = os.getenv("OUTPUT_DIR", self.output_dir)
        self.alert_cache_dir = os.getenv("ALERT_CACHE_DIR", self.alert_cache_dir)

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
        logger.info("LAYER_CACHE_TTL_MINUTES: %s", self.layer_cache_ttl_minutes)

        for level, tolerance in self.detail_levels.items():
            logger.info("DETAIL_LEVEL_%s: %s", level, tolerance)

        logger.info("ALERT_DETAIL_LEVEL: %s", self.alert_detail_level)
        logger.info("MYSQL_TAVISO_HOST: %s", self.mysql_taviso_host)
        logger.info("MYSQL_TAVISO_DATABASE: %s", self.mysql_taviso_database)
        logger.info("S3_ENDPOINT: %s", self.s3_endpoint)
        logger.info("S3_BUCKET_NAME: %s", self.s3_bucket_name)
        logger.info("S3_SECURE: %s", self.s3_secure)
        logger.info("=====================")

    @staticmethod
    def get_settings() -> "Settings":
        """Instantiate and return a Settings object from the environment."""
        return Settings()
