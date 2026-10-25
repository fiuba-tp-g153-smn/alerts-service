"""Tests for S3ObjectStorage — transport failures surface as StorageUnavailableError."""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

from adapters.s3_storage import S3ObjectStorage
from ports.object_storage import StorageUnavailableError


class _FailingClient:
    """Async context manager standing in for an aioboto3 S3 client."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def list_objects_v2(self, **_kwargs):
        raise self._exc

    async def upload_file(self, *_args):
        raise self._exc

    async def delete_object(self, **_kwargs):
        raise self._exc


def _storage_raising(exc: Exception) -> S3ObjectStorage:
    settings = MagicMock()
    settings.s3_endpoint = "localhost:9000"
    settings.s3_bucket_name = "bucket"
    storage = S3ObjectStorage(settings, MagicMock())
    storage._session = MagicMock()
    storage._session.client.return_value = _FailingClient(exc)
    return storage


@pytest.mark.parametrize(
    "exc",
    [
        EndpointConnectionError(endpoint_url="http://localhost:9000"),
        ReadTimeoutError(endpoint_url="http://localhost:9000"),
    ],
)
@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.list_keys("prefix"),
        lambda s: s.upload("/tmp/f", "key"),
        lambda s: s.delete("key"),
    ],
)
async def test_transport_errors_raise_storage_unavailable(exc, call):
    with pytest.raises(StorageUnavailableError):
        await call(_storage_raising(exc))


async def test_client_error_propagates_unchanged():
    exc = ClientError({"Error": {"Code": "AccessDenied"}}, "ListObjectsV2")

    with pytest.raises(ClientError):
        await _storage_raising(exc).list_keys("prefix")
