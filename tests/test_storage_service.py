import sys
from unittest.mock import MagicMock

from app.core.storage.storage_service import (
    _LazyStorageService,
    storage_service,
)

# The package attribute `app.core.storage.storage_service` is the proxy object
# (shadowing the submodule name), so grab the real module from sys.modules.
storage_module = sys.modules["app.core.storage.storage_service"]


def test_module_level_storage_service_is_lazy() -> None:
    # Importing the module must not construct a B2 client (MNE-29).
    assert isinstance(storage_service, _LazyStorageService)


def test_proxy_constructs_single_backend_on_first_use(monkeypatch) -> None:
    backend = MagicMock()
    spy = MagicMock(return_value=backend)
    monkeypatch.setattr(storage_module, "B2Storage", spy)

    proxy = _LazyStorageService()
    spy.assert_not_called()

    proxy.get_file_size("k")
    spy.assert_called_once()
    proxy.delete_file("k")
    spy.assert_called_once()  # still a single underlying B2 instance

    backend.get_file_size.assert_called_once_with("k")
    backend.delete_file.assert_called_once_with("k")
