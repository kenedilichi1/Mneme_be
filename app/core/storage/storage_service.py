from app.core.config import settings
from app.core.storage.b2_storage import B2Storage


class StorageService:
    def __init__(self, b2_storage: B2Storage) -> None:
        self.b2_storage = b2_storage

    def generate_upload_url(self, storage_key: str, content_type: str) -> str:
        return self.b2_storage.generate_upload_url(
            storage_key, content_type, settings.UPLOAD_URL_EXPIRE_SECONDS
        )

    def get_file_size(self, storage_key: str) -> int | None:
        return self.b2_storage.get_file_size(storage_key)

    def get_download_url(self, storage_key: str) -> str:
        return self.b2_storage.get_download_url(storage_key)

    def delete_file(self, storage_key: str) -> None:
        self.b2_storage.delete_file(storage_key)

    def download_file_to_path(self, storage_key: str, destination_path: str) -> None:
        self.b2_storage.download_file_to_path(storage_key, destination_path)


class _LazyStorageService:
    """Module-level `storage_service` that builds its B2 client on first use.

    Constructing B2Storage at import time (MNE-29) meant merely importing the
    app built a boto3 client — and crashed the whole app on a bad endpoint
    before any request was served. The proxy defers that to first use while
    keeping a single underlying B2 client for the process.
    """

    def __init__(self) -> None:
        self._impl: StorageService | None = None

    def _get(self) -> StorageService:
        if self._impl is None:
            self._impl = StorageService(b2_storage=B2Storage())
        return self._impl

    def __getattr__(self, name: str):
        return getattr(self._get(), name)


storage_service = _LazyStorageService()
