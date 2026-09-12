"""Storage backends: Azure Blob Storage in Azure, local disk for dev."""
from __future__ import annotations

import mimetypes
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import BinaryIO, Iterator

from . import config

_UNSAFE = re.compile(r"[^A-Za-z0-9._ \-()\[\]]+")
CHUNK = 4 * 1024 * 1024


@dataclass
class StoredFile:
    name: str
    size: int
    modified: datetime


def safe_name(filename: str) -> str:
    """Strip any path and anything that isn't a plain, printable filename char."""
    base = os.path.basename(filename.replace("\\", "/")).strip()
    base = _UNSAFE.sub("_", base).strip(". ")
    return base[:200] or "file"


def _dedupe(name: str, taken) -> str:
    """Append -1, -2, ... until the name is free. `taken` is a callable."""
    if not taken(name):
        return name
    stem, ext = os.path.splitext(name)
    for i in range(1, 1000):
        candidate = f"{stem}-{i}{ext}"
        if not taken(candidate):
            return candidate
    raise RuntimeError("could not find a free filename")


class Backend:
    kind = "unknown"

    def list(self) -> list[StoredFile]: ...
    def save(self, name: str, stream: BinaryIO) -> str: ...
    def read(self, name: str) -> tuple[Iterator[bytes], int, str]: ...
    def delete(self, name: str) -> bool: ...
    def exists(self, name: str) -> bool: ...


class LocalBackend(Backend):
    """Dev-only. Container Apps replicas have ephemeral disks."""

    kind = "local"

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _path(self, name: str) -> str:
        path = os.path.abspath(os.path.join(self.root, safe_name(name)))
        if os.path.commonpath([path, self.root]) != self.root:
            raise ValueError("invalid path")
        return path

    def list(self) -> list[StoredFile]:
        out = []
        for entry in os.scandir(self.root):
            if entry.is_file():
                st = entry.stat()
                out.append(
                    StoredFile(
                        entry.name,
                        st.st_size,
                        datetime.fromtimestamp(st.st_mtime, timezone.utc),
                    )
                )
        return sorted(out, key=lambda f: f.modified, reverse=True)

    def exists(self, name: str) -> bool:
        return os.path.isfile(self._path(name))

    def save(self, name: str, stream: BinaryIO) -> str:
        name = _dedupe(safe_name(name), self.exists)
        with open(self._path(name), "wb") as fh:
            shutil.copyfileobj(stream, fh, CHUNK)
        return name

    def read(self, name: str):
        path = self._path(name)
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"

        def gen():
            with open(path, "rb") as fh:
                while chunk := fh.read(CHUNK):
                    yield chunk

        return gen(), size, ctype

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if os.path.isfile(path):
            os.remove(path)
            return True
        return False


class BlobBackend(Backend):
    kind = "blob"

    def __init__(self):
        from azure.storage.blob import BlobServiceClient

        if config.AZURE_STORAGE_CONNECTION_STRING:
            svc = BlobServiceClient.from_connection_string(
                config.AZURE_STORAGE_CONNECTION_STRING
            )
        else:
            from azure.identity import DefaultAzureCredential

            svc = BlobServiceClient(
                f"https://{config.AZURE_STORAGE_ACCOUNT}.blob.core.windows.net",
                credential=DefaultAzureCredential(),
            )
        self.container = svc.get_container_client(config.BLOB_CONTAINER)
        try:
            self.container.create_container()
        except Exception:
            pass  # already exists, or the identity may only have data-plane rights

    def list(self) -> list[StoredFile]:
        out = [
            StoredFile(
                b.name,
                b.size or 0,
                b.last_modified or datetime.now(timezone.utc),
            )
            for b in self.container.list_blobs()
        ]
        return sorted(out, key=lambda f: f.modified, reverse=True)

    def exists(self, name: str) -> bool:
        return self.container.get_blob_client(safe_name(name)).exists()

    def save(self, name: str, stream: BinaryIO) -> str:
        from azure.storage.blob import ContentSettings

        name = _dedupe(safe_name(name), self.exists)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.container.get_blob_client(name).upload_blob(
            stream,
            overwrite=False,
            max_concurrency=4,
            content_settings=ContentSettings(content_type=ctype),
        )
        return name

    def read(self, name: str):
        client = self.container.get_blob_client(safe_name(name))
        downloader = client.download_blob(max_concurrency=2)
        props = downloader.properties
        ctype = (
            props.content_settings.content_type
            if props.content_settings
            else "application/octet-stream"
        )
        return downloader.chunks(), props.size, ctype or "application/octet-stream"

    def delete(self, name: str) -> bool:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self.container.get_blob_client(safe_name(name)).delete_blob()
            return True
        except ResourceNotFoundError:
            return False


_backend: Backend | None = None


def get_backend() -> Backend:
    global _backend
    if _backend is None:
        if config.AZURE_STORAGE_CONNECTION_STRING or config.AZURE_STORAGE_ACCOUNT:
            _backend = BlobBackend()
        else:
            _backend = LocalBackend(config.LOCAL_STORAGE_DIR)
    return _backend
