import logging
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)


def _region_from_endpoint(endpoint: str) -> str:
    # https://s3.us-west-004.backblazeb2.com -> us-west-004
    # Scheme-less endpoints ("s3.us-west-004.backblazeb2.com") are tolerated
    if "://" not in endpoint:
        endpoint = f"https://{endpoint}"
    host = urlparse(endpoint).hostname or ""
    parts = host.split(".")
    if len(parts) < 3 or parts[0] != "s3":
        raise ValueError(f"Unrecognised B2 S3 endpoint: {endpoint}")
    return parts[1]


class B2Storage:
    """Backblaze B2 via its S3-compatible API."""

    def __init__(self) -> None:
        self.bucket_name = settings.B2_BUCKET_NAME
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.B2_S3_ENDPOINT,
            region_name=_region_from_endpoint(settings.B2_S3_ENDPOINT),
            aws_access_key_id=settings.B2_KEY_ID,
            aws_secret_access_key=settings.B2_APPLICATION_KEY,
            config=Config(signature_version="s3v4"),
        )

    def generate_upload_url(self, file_name: str, content_type: str, expires_in: int) -> str:
        """Presigned PUT valid only for this key and content type."""
        return self.client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket_name,
                "Key": file_name,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )

    def get_file_size(self, file_name: str) -> int | None:
        """Size in bytes, or None if the object doesn't exist."""
        try:
            head = self.client.head_object(Bucket=self.bucket_name, Key=file_name)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return int(head["ContentLength"])

    def get_download_url(self, file_name: str, expires_in: int = 900) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": file_name},
            ExpiresIn=expires_in,
        )

    def delete_file(self, file_name: str) -> None:
        # B2 keeps old versions by default; a plain DeleteObject only hides the file,
        # so delete every version of this exact key.
        try:
            paginator = self.client.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=file_name):
                for version in page.get("Versions", []) + page.get("DeleteMarkers", []):
                    if version["Key"] != file_name:
                        continue
                    self.client.delete_object(
                        Bucket=self.bucket_name,
                        Key=file_name,
                        VersionId=version["VersionId"],
                    )
        except ClientError as e:
            logger.warning("B2 error while deleting file %s: %s", file_name, e)

    def download_file_to_path(self, file_name: str, destination_path: str) -> None:
        self.client.download_file(self.bucket_name, file_name, destination_path)
