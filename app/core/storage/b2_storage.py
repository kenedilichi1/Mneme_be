from b2sdk.v2 import Auth, Bucket, InMemoryAccountInfo

from app.core.config import settings


class B2Storage:
    def __init__(self) -> None:
        self.info = InMemoryAccountInfo()
        self.auth = Auth()
        self.bucket: Bucket | None = None

    def _ensure_bucket(self) -> Bucket:
        if self.bucket is None:
            self.auth.authorize_account(
                "production", settings.B2_KEY_ID, settings.B2_APPLICATION_KEY
            )
            bucket_name = settings.B2_BUCKET_NAME
            self.bucket = self.auth.get_bucket_by_name(bucket_name)
        return self.bucket

    def generate_upload_url(self, file_name: str) -> str:
        bucket = self._ensure_bucket()
        file_info = bucket.get_file_info_by_name(file_name)
        if file_info is not None:
            return bucket.get_download_url(file_name)

        upload_url = bucket.get_upload_url()
        return upload_url

    def get_download_url(self, file_name: str) -> str:
        bucket = self._ensure_bucket()
        return bucket.get_download_url(file_name)

    def delete_file(self, file_name: str) -> None:
        bucket = self._ensure_bucket()
        file_info = bucket.get_file_info_by_name(file_name)
        if file_info is not None:
            bucket.delete_file_version(file_info.id_, file_name)


storage = B2Storage()
