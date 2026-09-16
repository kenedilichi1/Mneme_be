import uuid

from app.core.config import settings
from app.core.storage.b2_storage import B2Storage


class StorageService:
    def __init__(self, b2_storage: B2Storage) -> None:
        self.b2_storage = b2_storage

    def generate_upload_url(self, user_id: uuid.UUID, file_type: str) -> str:
        file_name = f"{user_id}/{uuid.uuid4()}.{file_type.split('/')[-1]}"
        return self.b2_storage.generate_upload_url(file_name)

    def get_download_url(self, storage_key: str) -> str:
        return self.b2_storage.get_download_url(storage_key)

    def delete_file(self, storage_key: str) -> None:
        self.b2_storage.delete_file(storage_key)


storage_service = StorageService(b2_storage=B2Storage())
