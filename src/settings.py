"""Application configuration via environment variables."""

import json
import os
from logging import Logger, getLogger

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
    detail_level_tolerances: dict = {}
    departments_simplify_tolerance: float = 0.005

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
    alerts_detail_level: int = 7

    # Asynchronous alert generation (background worker pool)
    alerts_job_workers: int = 2
    alerts_job_queue_maxsize: int = 16
    alerts_job_timeout_seconds: float = 150.0
    alerts_supervisor_interval_seconds: float = 30.0
    alerts_job_shutdown_seconds: float = 160.0

    # Job-history store (local SQLite, always-on durable job records).
    jobs_db_path: str = ""

    # Metrics store (local SQLite, optional dashboard observability / telemetry)
    metrics_enabled: bool = True
    metrics_db_path: str = ""
    metrics_sample_interval_seconds: float = 60.0
    metrics_retention_days: int = 30
    metrics_max_rows: int = 100000

    # settings.json keys accepted after nested groups (layer/alert/metrics) are
    # flattened. `detail_level_tolerances` / `departments_simplify_tolerance`
    # are value maps read separately. Unknown keys are warned, not dropped.
    _JSON_KEYS = frozenset(
        {
            "layer_update_cron",
            "layer_cache_ttl_minutes",
            "alerts_detail_level",
            "alerts_job_workers",
            "alerts_job_queue_maxsize",
            "alerts_job_timeout_seconds",
            "alerts_job_shutdown_seconds",
            "alerts_supervisor_interval_seconds",
            "metrics_enabled",
            "metrics_sample_interval_seconds",
            "metrics_retention_days",
            "metrics_max_rows",
        }
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
        # Geometry-simplify tolerances are value maps keyed by detail level, not
        # config namespaces — read them before flattening so their entries stay
        # intact (a generic flatten would explode them into per-level keys).
        raw_tolerances = data.pop("detail_level_tolerances", {})
        self.detail_level_tolerances = {
            int(k): float(v) for k, v in raw_tolerances.items()
        }
        self.departments_simplify_tolerance = float(
            data.pop("departments_simplify_tolerance", 0.005)
        )

        # Everything else nests under namespace objects (layer, alert, metrics);
        # flatten to underscore-joined keys so nesting is purely cosmetic and the
        # flat attribute names below stay unchanged.
        flat = self._flatten(data)
        unknown = sorted(set(flat) - self._JSON_KEYS)
        if unknown:
            getLogger(__name__).warning(
                "Ignoring unrecognized settings.json keys: %s", ", ".join(unknown)
            )

        self.layer_update_cron = flat.get("layer_update_cron", "0 3 * * 0")
        self.layer_cache_ttl_minutes = int(flat.get("layer_cache_ttl_minutes", 30))
        self.alerts_detail_level = int(flat.get("alerts_detail_level", 7))
        self.alerts_job_workers = int(flat.get("alerts_job_workers", 2))
        self.alerts_job_queue_maxsize = int(flat.get("alerts_job_queue_maxsize", 16))
        self.alerts_job_timeout_seconds = float(
            flat.get("alerts_job_timeout_seconds", 150.0)
        )
        self.alerts_supervisor_interval_seconds = float(
            flat.get("alerts_supervisor_interval_seconds", 30.0)
        )
        self.alerts_job_shutdown_seconds = float(
            flat.get("alerts_job_shutdown_seconds", 160.0)
        )
        self.metrics_enabled = bool(flat.get("metrics_enabled", True))
        self.metrics_sample_interval_seconds = float(
            flat.get("metrics_sample_interval_seconds", 60.0)
        )
        self.metrics_retention_days = int(flat.get("metrics_retention_days", 30))
        self.metrics_max_rows = int(flat.get("metrics_max_rows", 100000))

    @staticmethod
    def _flatten(data: dict) -> dict:
        """Flatten nested settings objects to underscore-joined flat keys, so
        settings.json can nest while attribute names stay flat
        (`alerts.job.workers` -> `alerts_job_workers`). Recurses into dicts only;
        a nested object wins over a flat key of the same resulting name."""
        flat: dict = {}

        def _walk(prefix: str, section: dict) -> None:
            for key, value in section.items():
                if not isinstance(value, dict):
                    flat[f"{prefix}{key}"] = value
            for key, value in section.items():
                if isinstance(value, dict):
                    _walk(f"{prefix}{key}_", value)

        _walk("", data)
        return flat

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
        self.jobs_db_path = os.getenv(
            "JOBS_DB_PATH", os.path.join(self.data_dir, "jobs.sqlite")
        )
        self.metrics_db_path = os.getenv(
            "METRICS_DB_PATH", os.path.join(self.data_dir, "metrics.sqlite")
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
        logger.info("LAYER_CACHE_TTL_MINUTES: %s", self.layer_cache_ttl_minutes)

        for level, tolerance in self.detail_level_tolerances.items():
            logger.info("DETAIL_LEVEL_TOLERANCE_%s: %s", level, tolerance)

        logger.info(
            "DEPARTMENTS_SIMPLIFY_TOLERANCE: %s", self.departments_simplify_tolerance
        )
        logger.info("ALERTS_DETAIL_LEVEL: %s", self.alerts_detail_level)
        logger.info("ALERTS_JOB_WORKERS: %s", self.alerts_job_workers)
        logger.info("ALERTS_JOB_QUEUE_MAXSIZE: %s", self.alerts_job_queue_maxsize)
        logger.info("ALERTS_JOB_TIMEOUT_SECONDS: %s", self.alerts_job_timeout_seconds)
        logger.info(
            "ALERTS_SUPERVISOR_INTERVAL_SECONDS: %s",
            self.alerts_supervisor_interval_seconds,
        )
        logger.info("ALERTS_JOB_SHUTDOWN_SECONDS: %s", self.alerts_job_shutdown_seconds)
        logger.info("JOBS_DB_PATH: %s", self.jobs_db_path)
        logger.info("METRICS_ENABLED: %s", self.metrics_enabled)
        logger.info("METRICS_DB_PATH: %s", self.metrics_db_path)
        logger.info(
            "METRICS_SAMPLE_INTERVAL_SECONDS: %s",
            self.metrics_sample_interval_seconds,
        )
        logger.info("METRICS_RETENTION_DAYS: %s", self.metrics_retention_days)
        logger.info("METRICS_MAX_ROWS: %s", self.metrics_max_rows)
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
