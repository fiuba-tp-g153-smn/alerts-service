"""Application configuration via environment variables."""

import os

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

    # Scheduler
    layer_update_cron: str = "0 3 * * 0"

    # Geospatial
    data_dir: str = "/app/data"
    simplify_tolerance: float = 0.01
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

    # MySQL Database
    mysql_host: str = ""
    mysql_port: int = 3306
    mysql_database: str = ""
    mysql_user: str = ""
    mysql_password: str = ""

    # Alert Generation
    output_dir: str = ""
    alert_cache_dir: str = ""

    def __init__(self):
        self._load_from_env()

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

        self.layer_update_cron = os.getenv("LAYER_UPDATE_CRON", self.layer_update_cron)

        self.data_dir = os.getenv("DATA_DIR", self.data_dir)
        self.simplify_tolerance = float(
            os.getenv("SIMPLIFY_TOLERANCE", str(self.simplify_tolerance))
        )
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

        self.output_dir = os.getenv("OUTPUT_DIR", self.output_dir)
        self.alert_cache_dir = os.getenv("ALERT_CACHE_DIR", self.alert_cache_dir)

    @staticmethod
    def get_settings() -> "Settings":
        """Instantiate and return a Settings object from the environment."""
        return Settings()
