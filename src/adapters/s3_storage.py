import os
from logging import Logger
from typing import TYPE_CHECKING

import aioboto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError

if TYPE_CHECKING:
    from settings import Settings


class S3ObjectStorage:
    def __init__(self, settings: "Settings", logger: Logger):
        self.settings = settings
        self.logger = logger
        self._session = aioboto3.Session()

    def _client_kwargs(self) -> dict:
        kwargs: dict = {
            "aws_access_key_id": self.settings.s3_access_key,
            "aws_secret_access_key": self.settings.s3_secret_key,
            "config": Config(
                connect_timeout=5, read_timeout=30, retries={"max_attempts": 1}
            ),
        }
        if self.settings.s3_endpoint:
            scheme = "https" if self.settings.s3_secure else "http"
            endpoint = self.settings.s3_endpoint
            if not endpoint.startswith("http"):
                endpoint = f"{scheme}://{endpoint}"
            kwargs["endpoint_url"] = endpoint
        return kwargs

    async def upload(self, local_path: str, key: str) -> None:
        self.logger.info(
            f"Uploading {local_path} → s3://{self.settings.s3_bucket_name}/{key}"
        )
        async with self._session.client("s3", **self._client_kwargs()) as s3:
            await s3.upload_file(local_path, self.settings.s3_bucket_name, key)

    async def list_keys(self, prefix: str) -> list[str]:
        async with self._session.client("s3", **self._client_kwargs()) as s3:
            resp = await s3.list_objects_v2(
                Bucket=self.settings.s3_bucket_name, Prefix=prefix
            )
            return [obj["Key"] for obj in resp.get("Contents", [])]

    async def delete(self, key: str) -> None:
        self.logger.info(f"Deleting s3://{self.settings.s3_bucket_name}/{key}")
        async with self._session.client("s3", **self._client_kwargs()) as s3:
            await s3.delete_object(Bucket=self.settings.s3_bucket_name, Key=key)

    async def download(self, key: str, local_path: str) -> bool:
        """Returns True if downloaded successfully, False if key does not exist."""
        try:
            async with self._session.client("s3", **self._client_kwargs()) as s3:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                await s3.download_file(self.settings.s3_bucket_name, key, local_path)
            self.logger.info(
                f"Restored s3://{self.settings.s3_bucket_name}/{key} → {local_path}"
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                self.logger.info(
                    f"s3://{self.settings.s3_bucket_name}/{key} not found."
                )
                return False
            raise
        except (EndpointConnectionError, BotoCoreError, Exception) as e:
            self.logger.warning(f"S3 unreachable, skipping restore for {key}: {e}")
            return False
