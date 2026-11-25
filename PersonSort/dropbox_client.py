"""Dropbox OAuth 2 PKCE authentication, keyring token storage, folder browsing, and I/O."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import logging
from pathlib import Path, PurePosixPath
import shutil
import threading
import time
from typing import Any, Callable, Optional

try:
    import dropbox
    import dropbox.common
    import dropbox.exceptions
    import dropbox.files

    _DROPBOX_AVAILABLE = True
except ImportError:
    dropbox = None  # type: ignore
    _DROPBOX_AVAILABLE = False

try:
    import keyring
    import keyring.errors

    _KEYRING_AVAILABLE = True
except ImportError:
    keyring = None  # type: ignore
    _KEYRING_AVAILABLE = False

from config import (
    DROPBOX_KEYRING_SERVICE,
    DROPBOX_KEYRING_USERNAME,
    DROPBOX_SETTINGS_PATH,
    SortConfig,
)
from image_loader import is_supported_image_name, resolve_local_dropbox_file
from organizer import natural_sort_key

logger = logging.getLogger(__name__)

NO_PERMISSION_MESSAGE = (
    "No permission to access the configured shared Dropbox folder. "
    "Please open Dropbox and accept the shared folder invite first, then click Refresh."
)


class DropboxPermissionError(RuntimeError):
    """Raised when the user does not have permission for the target Dropbox folder/root."""


class DropboxAuthError(RuntimeError):
    """Raised when Dropbox OAuth credentials or tokens are missing, expired, or invalid."""


@dataclass
class DropboxPhotoEntry:
    """Metadata for a remote photo stored in Dropbox (tracking immutable `id:...`)."""

    file_id: str
    name: str
    path_display: str
    path_lower: str
    rev: str
    size: int
    shared_folder_id: str = ""


def _normalize_dropbox_path(path: str) -> str:
    """Normalize a Dropbox path: '' for root, otherwise '/Folder/Sub' with no trailing slash."""
    cleaned = (path or "").strip().replace("\\", "/")
    if not cleaned or cleaned == "/":
        return ""
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")
    cleaned = cleaned.rstrip("/")
    return "" if cleaned == "" else cleaned


def _parent_dropbox_path(norm_path: str) -> Optional[str]:
    """Return parent Dropbox path, or None if already at root ('')."""
    if not norm_path:
        return None
    parent = str(PurePosixPath(norm_path).parent)
    if parent in ("/", "."):
        return ""
    return parent


def _is_no_permission_exception(exc: Exception) -> bool:
    """Return True if `exc` represents a Dropbox no_permission or PathRootError."""
    if _DROPBOX_AVAILABLE and isinstance(exc, dropbox.exceptions.PathRootError):
        return True

    err_obj = getattr(exc, "error", None)
    if err_obj is not None:
        is_no_perm = getattr(err_obj, "is_no_permission", None)
        if callable(is_no_perm) and is_no_perm():
            return True
        is_inv_root = getattr(err_obj, "is_invalid_root", None)
        if callable(is_inv_root) and is_inv_root():
            return True
        err_text = str(err_obj).lower()
        if "no_permission" in err_text or "invalid_root" in err_text:
            return True

    msg = str(exc).lower()
    return "no_permission" in msg or "invalid_root" in msg or "pathrooterror" in msg


def _is_auth_exception(exc: Exception) -> bool:
    """Return True if `exc` is an OAuth/token error."""
    if _DROPBOX_AVAILABLE and isinstance(exc, dropbox.exceptions.AuthError):
        return True
    msg = str(exc).lower()
    return (
        "expired_access_token" in msg
        or "invalid_access_token" in msg
        or "missing_scope" in msg
        or "invalid_grant" in msg
    )


def _translate_dropbox_error(exc: Exception) -> str:
    """Translate Dropbox SDK exceptions into plain-English user-facing error messages."""
    if isinstance(exc, (DropboxPermissionError, DropboxAuthError)):
        return str(exc)

    if _is_no_permission_exception(exc):
        return NO_PERMISSION_MESSAGE

    if _is_auth_exception(exc):
        err_obj = getattr(exc, "error", None)
        err_str = str(err_obj or exc).lower()
        if "missing_scope" in err_str:
            return (
                "Dropbox App is missing required permissions/scopes "
                "(enable files.metadata.read, files.content.read, files.content.write, "
                "and sharing.read in the Dropbox App Console, then reconnect)."
            )
        if "expired_access_token" in err_str or "invalid_access_token" in err_str or "invalid_grant" in err_str:
            return "Your Dropbox connection expired or was revoked. Please click Connect Dropbox to sign in again."
        return f"Dropbox authentication error: {exc}"

    err_str = str(exc)
    if "not_found" in err_str.lower():
        return "That Dropbox folder was not found. It may have been renamed or moved."
    if "conflict" in err_str.lower():
        return "A folder with that name already exists in Dropbox."
    return err_str or exc.__class__.__name__


class DropboxManager:
    """
    Manages Dropbox OAuth 2 PKCE sign-in, secure OS keyring refresh-token storage,
    shared folder namespace pinning, remote folder browsing/creation, downloads,
    and cloud-to-cloud copy/move operations.
    """

    def __init__(
        self,
        config: SortConfig,
        settings_path: Optional[Path] = None,
        keyring_service: str = DROPBOX_KEYRING_SERVICE,
        keyring_username: str = DROPBOX_KEYRING_USERNAME,
    ) -> None:
        self.config = config
        self.settings_path: Path = settings_path or DROPBOX_SETTINGS_PATH
        self.keyring_service: str = keyring_service
        self.keyring_username: str = keyring_username
        self._lock = threading.Lock()
        self._pending_flow: Any = None
        self._oauth_session: dict[str, Any] = {}

        self._settings: dict[str, str] = {
            "app_key": "",
            "shared_folder_id": "",
            "selected_input_folder": "",
            "selected_output_folder": "",
            "account_name": "",
            "account_email": "",
        }
        self._load_settings()

    @property
    def sdk_available(self) -> bool:
        """Return True if both `dropbox` and `keyring` packages are installed."""
        return _DROPBOX_AVAILABLE and _KEYRING_AVAILABLE

    @property
    def app_key(self) -> str:
        return (self._settings.get("app_key") or "").strip()

    @property
    def shared_folder_id(self) -> str:
        return (self._settings.get("shared_folder_id") or "").strip()

    def _load_settings(self) -> None:
        """Load non-secret Dropbox settings from local JSON, falling back to SortConfig defaults."""
        loaded: dict[str, Any] = {}
        if self.settings_path.is_file():
            try:
                loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Could not read Dropbox settings file %s: %s", self.settings_path, exc)

        for key in self._settings:
            val = loaded.get(key)
            if isinstance(val, str):
                self._settings[key] = val.strip()

        if not self._settings["app_key"] and getattr(self.config, "dropbox_app_key", ""):
            self._settings["app_key"] = self.config.dropbox_app_key.strip()
        if not self._settings["shared_folder_id"] and getattr(self.config, "dropbox_shared_folder_id", ""):
            self._settings["shared_folder_id"] = self.config.dropbox_shared_folder_id.strip()

    def _save_settings(self) -> None:
        """Persist non-secret Dropbox preferences locally (never writes tokens)."""
        safe_payload = {
            "app_key": self._settings.get("app_key", ""),
            "shared_folder_id": self._settings.get("shared_folder_id", ""),
            "selected_input_folder": self._settings.get("selected_input_folder", ""),
            "selected_output_folder": self._settings.get("selected_output_folder", ""),
            "account_name": self._settings.get("account_name", ""),
            "account_email": self._settings.get("account_email", ""),
        }
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(safe_payload, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # OS Keyring Token Storage (Windows Credential Manager / macOS Keychain)
    # ------------------------------------------------------------------
    def _load_refresh_token(self) -> Optional[str]:
        if not _KEYRING_AVAILABLE:
            return None
        try:
            token = keyring.get_password(self.keyring_service, self.keyring_username)
            return token.strip() if isinstance(token, str) and token.strip() else None
        except Exception as exc:
            logger.warning("Failed to read Dropbox refresh token from keyring: %s", exc)
            return None

    def _save_refresh_token(self, token: str) -> None:
        if not _KEYRING_AVAILABLE:
            raise RuntimeError(
                "The 'keyring' package is not installed. Run: pip install keyring"
            )
        keyring.set_password(self.keyring_service, self.keyring_username, token.strip())

    def _clear_refresh_token(self) -> None:
        if not _KEYRING_AVAILABLE:
            return
        try:
            keyring.delete_password(self.keyring_service, self.keyring_username)
        except Exception as exc:
            delete_err = getattr(getattr(keyring, "errors", None), "PasswordDeleteError", None)
            if delete_err and isinstance(exc, delete_err):
                return
            if "not found" in str(exc).lower() or "no such" in str(exc).lower():
                return
            logger.warning("Failed to clear Dropbox token from keyring: %s", exc)

    def _translate_and_raise(self, exc: Exception) -> None:
        msg = _translate_dropbox_error(exc)
        if _is_no_permission_exception(exc):
            raise DropboxPermissionError(msg) from exc
        if _is_auth_exception(exc):
            raise DropboxAuthError(msg) from exc
        raise RuntimeError(msg) from exc

    # ------------------------------------------------------------------
    # Client Construction & Account Metadata
    # ------------------------------------------------------------------
    def get_client(
        self,
        shared_folder_id: Optional[str] = None,
        pin_shared_folder: bool = True,
    ) -> Any:
        """
        Return a `dropbox.Dropbox` client initialized with the stored refresh token
        (for automatic 4-hour access token refreshes) and optionally pinned to the
        shared folder namespace ID via `with_path_root`.
        """
        if not _DROPBOX_AVAILABLE:
            raise RuntimeError(
                "The 'dropbox' SDK is not installed. Run: pip install dropbox"
            )
        app_key = self.app_key
        if not app_key:
            raise DropboxAuthError(
                "Dropbox App Key is not configured. Open Dropbox Settings to enter your App Key."
            )
        refresh_token = self._load_refresh_token()
        if not refresh_token:
            raise DropboxAuthError(
                "Dropbox is not connected yet. Click Connect Dropbox to authorize access."
            )

        dbx = dropbox.Dropbox(oauth2_refresh_token=refresh_token, app_key=app_key)
        ns_id = (
            shared_folder_id.strip()
            if shared_folder_id is not None
            else self.shared_folder_id
        )
        if pin_shared_folder and ns_id:
            dbx = dbx.with_path_root(dropbox.common.PathRoot.namespace_id(ns_id))
        return dbx

    def _refresh_account_profile(self, dbx: Any) -> tuple[str, str]:
        """Try fetching the user's Dropbox display name and email cleanly."""
        try:
            acct = dbx.users_get_current_account()
            name_obj = getattr(acct, "name", None)
            display_name = (
                getattr(name_obj, "display_name", "")
                or getattr(acct, "email", "")
                or "Dropbox User"
            )
            email = getattr(acct, "email", "") or ""
            return str(display_name), str(email)
        except Exception:
            return "Dropbox User", ""

    def _check_shared_folder_permission(self) -> Optional[str]:
        """Verify access to the pinned shared folder namespace, returning a warning if denied."""
        if not self.shared_folder_id:
            return None
        try:
            dbx = self.get_client(pin_shared_folder=True)
            dbx.files_list_folder("", limit=1)
            return None
        except Exception as exc:
            if _is_no_permission_exception(exc) or _is_auth_exception(exc):
                return _translate_dropbox_error(exc)
            logger.debug("Shared folder root check skipped/non-fatal: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Public Status & Settings API
    # ------------------------------------------------------------------
    def get_status(
        self,
        redirect_uri: str = "",
        verify_shared_access: bool = True,
    ) -> dict[str, Any]:
        """Return current Dropbox setup, login, and folder selection state."""
        with self._lock:
            app_key = self.app_key
            shared_folder_id = self.shared_folder_id
            refresh_token = self._load_refresh_token() if _KEYRING_AVAILABLE else None
            connected = bool(self.sdk_available and app_key and refresh_token)
            account_name = self._settings.get("account_name", "")
            account_email = self._settings.get("account_email", "")
            permission_warning: Optional[str] = None
            error_msg: Optional[str] = None

        if not _DROPBOX_AVAILABLE or not _KEYRING_AVAILABLE:
            missing = []
            if not _DROPBOX_AVAILABLE:
                missing.append("dropbox")
            if not _KEYRING_AVAILABLE:
                missing.append("keyring")
            error_msg = (
                f"Missing Python package(s): {', '.join(missing)}. "
                "Install requirements.txt to enable Dropbox integration."
            )

        if connected:
            if not account_name:
                try:
                    dbx = self.get_client(pin_shared_folder=False)
                    account_name, account_email = self._refresh_account_profile(dbx)
                    with self._lock:
                        self._settings["account_name"] = account_name
                        self._settings["account_email"] = account_email
                        self._save_settings()
                except Exception:
                    account_name = "Dropbox User"

            if verify_shared_access and shared_folder_id:
                permission_warning = self._check_shared_folder_permission()

        return {
            "sdk_available": self.sdk_available,
            "configured": bool(app_key),
            "app_key": app_key,
            "shared_folder_id": shared_folder_id,
            "connected": connected,
            "account_name": account_name if connected else "",
            "account_email": account_email if connected else "",
            "redirect_uri": redirect_uri,
            "selected_input_folder": self._settings.get("selected_input_folder", ""),
            "selected_output_folder": self._settings.get("selected_output_folder", ""),
            "permission_warning": permission_warning,
            "error": error_msg,
        }

    def update_settings(
        self,
        *,
        app_key: Optional[str] = None,
        shared_folder_id: Optional[str] = None,
        selected_input_folder: Optional[str] = None,
        selected_output_folder: Optional[str] = None,
        redirect_uri: str = "",
    ) -> dict[str, Any]:
        """Update and persist non-secret Dropbox configuration settings."""
        with self._lock:
            if app_key is not None:
                cleaned_key = app_key.strip()
                if cleaned_key != self._settings.get("app_key", ""):
                    self._pending_flow = None
                self._settings["app_key"] = cleaned_key
            if shared_folder_id is not None:
                self._settings["shared_folder_id"] = shared_folder_id.strip()
            if selected_input_folder is not None:
                self._settings["selected_input_folder"] = _normalize_dropbox_path(
                    selected_input_folder
                ) or "/"
                if selected_input_folder.strip() == "":
                    self._settings["selected_input_folder"] = ""
            if selected_output_folder is not None:
                self._settings["selected_output_folder"] = _normalize_dropbox_path(
                    selected_output_folder
                ) or "/"
                if selected_output_folder.strip() == "":
                    self._settings["selected_output_folder"] = ""
            self._save_settings()

        return self.get_status(redirect_uri=redirect_uri)

    # ------------------------------------------------------------------
    # OAuth 2 PKCE Sign-In & Persistent Refresh Token
    # ------------------------------------------------------------------
    def start_oauth_flow(self, redirect_uri: str) -> str:
        """
        Start a Dropbox OAuth 2 PKCE flow (`token_access_type='offline'`) and return
        the Dropbox authorization URL to open in the user's browser.
        """
        if not _DROPBOX_AVAILABLE:
            raise RuntimeError("Dropbox SDK is not installed.")
        if not _KEYRING_AVAILABLE:
            raise RuntimeError("Keyring package is not installed.")

        with self._lock:
            app_key = self.app_key
            if not app_key:
                raise ValueError(
                    "Please enter and save your Dropbox App Key before connecting."
                )
            self._oauth_session = {}
            auth_flow = dropbox.DropboxOAuth2Flow(
                consumer_key=app_key,
                redirect_uri=redirect_uri,
                session=self._oauth_session,
                csrf_token_session_key="dropbox-auth-csrf-token",
                token_access_type="offline",
                use_pkce=True,
            )
            self._pending_flow = auth_flow
            return str(auth_flow.start())

    def finish_oauth_flow(
        self,
        query_params: dict[str, str],
        redirect_uri: str = "",
    ) -> dict[str, Any]:
        """
        Complete the OAuth 2 PKCE callback, store `refresh_token` securely in OS keyring,
        fetch user account metadata, and verify shared folder permission if configured.
        """
        with self._lock:
            flow = self._pending_flow
            self._pending_flow = None

        if flow is None:
            raise DropboxAuthError(
                "No active Dropbox sign-in session was found. Please click Connect Dropbox again."
            )

        try:
            oauth_result = flow.finish(query_params)
        except Exception as exc:
            raise DropboxAuthError(f"Dropbox authorization failed: {exc}") from exc

        refresh_token = getattr(oauth_result, "refresh_token", None)
        if not refresh_token:
            raise DropboxAuthError(
                "Dropbox did not return an offline refresh token. "
                "Verify 'token_access_type=offline' and PKCE public client settings."
            )

        self._save_refresh_token(refresh_token)

        try:
            dbx = self.get_client(pin_shared_folder=False)
            account_name, account_email = self._refresh_account_profile(dbx)
        except Exception:
            account_name, account_email = "Dropbox User", ""

        with self._lock:
            self._settings["account_name"] = account_name
            self._settings["account_email"] = account_email
            self._save_settings()

        return self.get_status(redirect_uri=redirect_uri, verify_shared_access=True)

    def disconnect(self, redirect_uri: str = "") -> dict[str, Any]:
        """Remove stored Dropbox refresh token from keyring and clear cached user info."""
        with self._lock:
            self._pending_flow = None
            self._clear_refresh_token()
            self._settings["account_name"] = ""
            self._settings["account_email"] = ""
            self._save_settings()
        return self.get_status(redirect_uri=redirect_uri, verify_shared_access=False)

    # ------------------------------------------------------------------
    # Shared Folders & Directory Browser
    # ------------------------------------------------------------------
    def list_shared_folders(self) -> list[dict[str, Any]]:
        """Return shared folders available to the signed-in user (helps locate namespace IDs)."""
        dbx = self.get_client(pin_shared_folder=False)
        items: list[dict[str, Any]] = []
        try:
            res = dbx.sharing_list_folders()
            while True:
                for entry in getattr(res, "entries", []) or []:
                    sf_id = getattr(entry, "shared_folder_id", "") or ""
                    name = getattr(entry, "name", "") or sf_id
                    path_lower = getattr(entry, "path_lower", "") or ""
                    items.append(
                        {
                            "shared_folder_id": sf_id,
                            "name": name,
                            "path_lower": path_lower,
                            "is_pinned": sf_id == self.shared_folder_id,
                        }
                    )
                cursor = getattr(res, "cursor", None)
                if cursor:
                    res = dbx.sharing_list_folders_continue(cursor)
                else:
                    break
        except Exception as exc:
            self._translate_and_raise(exc)

        items.sort(key=lambda item: natural_sort_key(item["name"]))
        return items

    def list_folder(
        self,
        path: str = "",
        shared_folder_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        List subfolders and files inside `path`, flagging which files are supported photos.
        Uses `shared_folder_id` pinning if configured unless explicitly overridden.
        """
        norm_path = _normalize_dropbox_path(path)
        active_ns_id = (
            shared_folder_id.strip()
            if shared_folder_id is not None
            else self.shared_folder_id
        )
        dbx = self.get_client(
            shared_folder_id=active_ns_id,
            pin_shared_folder=bool(active_ns_id),
        )

        folders: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        image_count = 0
        total_files = 0

        try:
            res = dbx.files_list_folder(norm_path)
            while True:
                for entry in getattr(res, "entries", []) or []:
                    is_folder = False
                    is_file = False
                    if _DROPBOX_AVAILABLE:
                        is_folder = isinstance(entry, dropbox.files.FolderMetadata)
                        is_file = isinstance(entry, dropbox.files.FileMetadata)
                    if not is_folder and not is_file:
                        type_name = type(entry).__name__
                        if type_name == "FolderMetadata":
                            is_folder = True
                        elif type_name == "FileMetadata":
                            is_file = True

                    if is_folder:
                        name = getattr(entry, "name", "")
                        path_display = (
                            getattr(entry, "path_display", "")
                            or getattr(entry, "path_lower", "")
                            or f"{norm_path}/{name}"
                        )
                        folders.append(
                            {
                                "name": name,
                                "path_display": path_display,
                                "path_lower": getattr(entry, "path_lower", "") or "",
                                "id": getattr(entry, "id", "") or "",
                            }
                        )
                    elif is_file:
                        total_files += 1
                        name = getattr(entry, "name", "")
                        supported = is_supported_image_name(name)
                        if supported:
                            image_count += 1
                        path_display = (
                            getattr(entry, "path_display", "")
                            or getattr(entry, "path_lower", "")
                            or f"{norm_path}/{name}"
                        )
                        files.append(
                            {
                                "name": name,
                                "path_display": path_display,
                                "path_lower": getattr(entry, "path_lower", "") or "",
                                "id": getattr(entry, "id", "") or "",
                                "rev": getattr(entry, "rev", "") or "",
                                "size": int(getattr(entry, "size", 0) or 0),
                                "is_supported_image": supported,
                            }
                        )

                if getattr(res, "has_more", False):
                    res = dbx.files_list_folder_continue(res.cursor)
                else:
                    break
        except Exception as exc:
            self._translate_and_raise(exc)

        folders.sort(key=lambda f: natural_sort_key(f["name"]))
        files.sort(key=lambda f: natural_sort_key(f["name"]))
        return {
            "current_path": norm_path,
            "parent_path": _parent_dropbox_path(norm_path),
            "shared_folder_id": active_ns_id,
            "folders": folders,
            "files": files,
            "image_count": image_count,
            "total_files": total_files,
        }

    def create_folder(
        self,
        path: str,
        shared_folder_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a folder inside Dropbox (under the pinned shared folder namespace if configured)."""
        norm_path = _normalize_dropbox_path(path)
        if not norm_path:
            raise ValueError("Folder path cannot be empty or root.")

        active_ns_id = (
            shared_folder_id.strip()
            if shared_folder_id is not None
            else self.shared_folder_id
        )
        dbx = self.get_client(
            shared_folder_id=active_ns_id,
            pin_shared_folder=bool(active_ns_id),
        )

        try:
            res = dbx.files_create_folder_v2(norm_path, autorename=False)
            meta = getattr(res, "metadata", res)
            name = getattr(meta, "name", PurePosixPath(norm_path).name)
            path_display = getattr(meta, "path_display", "") or norm_path
            return {
                "name": name,
                "path_display": path_display,
                "path_lower": getattr(meta, "path_lower", "") or path_display.lower(),
                "id": getattr(meta, "id", "") or "",
            }
        except Exception as exc:
            self._translate_and_raise(exc)

    # ------------------------------------------------------------------
    # Step 3: Dropbox Photo Listing & Download (Thumbnails / Full-Res)
    # ------------------------------------------------------------------
    def list_images_in_folder(
        self,
        path: str,
        shared_folder_id: Optional[str] = None,
        recursive: bool = False,
    ) -> list[DropboxPhotoEntry]:
        """List all supported images in a Dropbox folder, storing immutable `id:...` references."""
        norm_path = _normalize_dropbox_path(path)
        active_ns_id = (
            shared_folder_id.strip()
            if shared_folder_id is not None
            else self.shared_folder_id
        )
        dbx = self.get_client(
            shared_folder_id=active_ns_id,
            pin_shared_folder=bool(active_ns_id),
        )

        entries: list[DropboxPhotoEntry] = []
        try:
            res = dbx.files_list_folder(norm_path, recursive=recursive)
            while True:
                for entry in getattr(res, "entries", []) or []:
                    is_file = (
                        _DROPBOX_AVAILABLE
                        and isinstance(entry, dropbox.files.FileMetadata)
                    ) or type(entry).__name__ == "FileMetadata"
                    if not is_file:
                        continue
                    name = getattr(entry, "name", "")
                    if not is_supported_image_name(name):
                        continue
                    path_display = (
                        getattr(entry, "path_display", "")
                        or getattr(entry, "path_lower", "")
                        or f"{norm_path}/{name}"
                    )
                    entries.append(
                        DropboxPhotoEntry(
                            file_id=getattr(entry, "id", "") or "",
                            name=name,
                            path_display=path_display,
                            path_lower=getattr(entry, "path_lower", "") or path_display.lower(),
                            rev=getattr(entry, "rev", "") or "",
                            size=int(getattr(entry, "size", 0) or 0),
                            shared_folder_id=active_ns_id,
                        )
                    )

                if getattr(res, "has_more", False):
                    res = dbx.files_list_folder_continue(res.cursor)
                else:
                    break
        except Exception as exc:
            self._translate_and_raise(exc)

        entries.sort(key=lambda e: natural_sort_key(e.name))
        return entries

    def _allocate_target_path(self, dest_dir: Path, filename: str, used: set[str]) -> Path:
        base = Path(filename).stem
        ext = Path(filename).suffix or ".jpg"
        candidate_name = f"{base}{ext}"
        counter = 2
        while candidate_name.lower() in used or (dest_dir / candidate_name).exists():
            candidate_name = f"{base}_{counter}{ext}"
            counter += 1
        used.add(candidate_name.lower())
        return dest_dir / candidate_name

    def download_images_to_dir(
        self,
        entries: list[DropboxPhotoEntry],
        dest_dir: Path,
        *,
        use_thumbnails: bool = True,
        thumbnail_size: str = "w2048h1536",
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> list[tuple[Path, DropboxPhotoEntry]]:
        """
        Download Dropbox images into `dest_dir`:
        - Copies from local Dropbox Desktop sync folder when present (for non-shared roots).
        - Otherwise uses `files_get_thumbnail_batch` (chunks of 25) when `use_thumbnails=True`,
          falling back cleanly to `files_download_to_file` for any entry whose thumbnail fails.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        total = len(entries)
        if total == 0:
            return []

        used_names: set[str] = set()
        planned: list[tuple[int, Path, DropboxPhotoEntry]] = [
            (idx, self._allocate_target_path(dest_dir, entry.name, used_names), entry)
            for idx, entry in enumerate(entries)
        ]

        results_by_idx: dict[int, tuple[Path, DropboxPhotoEntry]] = {}
        remote_items: list[tuple[int, Path, DropboxPhotoEntry]] = []
        completed = 0

        # 1. Local Dropbox Desktop shortcut (only when not pinned to an external shared namespace)
        for idx, target_path, entry in planned:
            if not entry.shared_folder_id:
                local_src = resolve_local_dropbox_file(
                    entry.path_display, expected_size=entry.size
                )
                if local_src is not None:
                    shutil.copy2(local_src, target_path)
                    results_by_idx[idx] = (target_path, entry)
                    completed += 1
                    if progress_callback:
                        progress_callback(completed, total, entry.name)
                    continue
            remote_items.append((idx, target_path, entry))

        # 2. Remote download (thumbnail batch or direct full-res)
        if remote_items:
            ns_id = remote_items[0][2].shared_folder_id
            dbx = self.get_client(
                shared_folder_id=ns_id,
                pin_shared_folder=bool(ns_id),
            )

            try:
                if use_thumbnails:
                    size_enum = getattr(
                        dropbox.files.ThumbnailSize,
                        thumbnail_size,
                        dropbox.files.ThumbnailSize.w2048h1536,
                    )
                    for start in range(0, len(remote_items), 25):
                        chunk = remote_items[start : start + 25]
                        args = [
                            dropbox.files.ThumbnailArg(
                                path=entry.file_id or entry.path_lower,
                                format=dropbox.files.ThumbnailFormat.jpeg,
                                size=size_enum,
                                mode=dropbox.files.ThumbnailMode.bestfit,
                            )
                            for _, _, entry in chunk
                        ]
                        batch_res = dbx.files_get_thumbnail_batch(args)
                        res_entries = getattr(batch_res, "entries", []) or []

                        for pos, (idx, target_path, entry) in enumerate(chunk):
                            item = res_entries[pos] if pos < len(res_entries) else None
                            wrote_thumb = False
                            if item is not None and item.is_success():
                                raw_thumb = item.get_success().thumbnail
                                thumb_bytes = (
                                    base64.b64decode(raw_thumb)
                                    if isinstance(raw_thumb, (str, bytes))
                                    else b""
                                )
                                if thumb_bytes:
                                    target_path.write_bytes(thumb_bytes)
                                    wrote_thumb = True

                            if not wrote_thumb:
                                dbx.files_download_to_file(
                                    str(target_path),
                                    entry.file_id or entry.path_lower,
                                )

                            results_by_idx[idx] = (target_path, entry)
                            completed += 1
                            if progress_callback:
                                progress_callback(completed, total, entry.name)
                else:
                    for idx, target_path, entry in remote_items:
                        dbx.files_download_to_file(
                            str(target_path),
                            entry.file_id or entry.path_lower,
                        )
                        results_by_idx[idx] = (target_path, entry)
                        completed += 1
                        if progress_callback:
                            progress_callback(completed, total, entry.name)
            except Exception as exc:
                self._translate_and_raise(exc)

        return [results_by_idx[i] for i in range(total)]

    def download_full_file(
        self,
        entry: DropboxPhotoEntry,
        dest_path: Path,
    ) -> Path:
        """Download full-resolution file from Dropbox (using immutable `file_id` when present)."""
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if not entry.shared_folder_id:
            local_src = resolve_local_dropbox_file(
                entry.path_display, expected_size=entry.size
            )
            if local_src is not None:
                shutil.copy2(local_src, dest_path)
                return dest_path

        dbx = self.get_client(
            shared_folder_id=entry.shared_folder_id,
            pin_shared_folder=bool(entry.shared_folder_id),
        )
        try:
            dbx.files_download_to_file(
                str(dest_path),
                entry.file_id or entry.path_lower,
            )
        except Exception as exc:
            self._translate_and_raise(exc)
        return dest_path

    # ------------------------------------------------------------------
    # Step 4: Dropbox Output (Cloud Batch Copy/Move & Uploads)
    # ------------------------------------------------------------------
    def ensure_folder(
        self,
        path: str,
        shared_folder_id: Optional[str] = None,
    ) -> None:
        """Ensure `path` exists in Dropbox, ignoring 'folder already exists' conflicts."""
        norm_path = _normalize_dropbox_path(path)
        if not norm_path:
            return
        active_ns_id = (
            shared_folder_id.strip()
            if shared_folder_id is not None
            else self.shared_folder_id
        )
        dbx = self.get_client(
            shared_folder_id=active_ns_id,
            pin_shared_folder=bool(active_ns_id),
        )
        try:
            dbx.files_create_folder_v2(norm_path, autorename=False)
        except Exception as exc:
            if "conflict" in str(exc).lower():
                return
            self._translate_and_raise(exc)

    def _run_relocation_chunks(
        self,
        dbx: Any,
        relocations: list[tuple[str, str]],
        *,
        is_move: bool,
        completed_offset: int,
        total_count: int,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> int:
        completed = completed_offset
        label = "Moving in Dropbox" if is_move else "Copying in Dropbox"
        batch_fn = dbx.files_move_batch_v2 if is_move else dbx.files_copy_batch_v2
        check_fn = dbx.files_move_batch_check_v2 if is_move else dbx.files_copy_batch_check_v2

        for start in range(0, len(relocations), 500):
            chunk = relocations[start : start + 500]
            entries = [
                dropbox.files.RelocationPath(from_path=src, to_path=dst)
                for src, dst in chunk
            ]
            res = batch_fn(entries, autorename=True)
            if hasattr(res, "is_async_job_id") and res.is_async_job_id():
                job_id = res.get_async_job_id()
                while True:
                    status = check_fn(job_id)
                    if status.is_complete():
                        break
                    time.sleep(0.35)

            completed += len(chunk)
            if progress_callback:
                progress_callback(completed, total_count, label)

        return completed

    def copy_or_move_batch(
        self,
        pairs: list[tuple[str, str]],
        *,
        mode: str = "copy",
        shared_folder_id: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> int:
        """
        Server-side batch copy or move in Dropbox (`autorename=True`).
        When `mode == 'move'` and the same source photo is placed in multiple folders,
        copies earlier occurrences first and moves only the final occurrence.
        """
        if not pairs:
            return 0

        active_ns_id = (
            shared_folder_id.strip()
            if shared_folder_id is not None
            else self.shared_folder_id
        )
        dbx = self.get_client(
            shared_folder_id=active_ns_id,
            pin_shared_folder=bool(active_ns_id),
        )

        normalized_pairs = [
            (src, _normalize_dropbox_path(dst)) for src, dst in pairs
        ]

        copy_pairs: list[tuple[str, str]] = []
        move_pairs: list[tuple[str, str]] = []
        if mode == "move":
            remaining: dict[str, int] = {}
            for src, _ in normalized_pairs:
                remaining[src] = remaining.get(src, 0) + 1
            for src, dst in normalized_pairs:
                if remaining[src] > 1:
                    copy_pairs.append((src, dst))
                    remaining[src] -= 1
                else:
                    move_pairs.append((src, dst))
        else:
            copy_pairs = normalized_pairs

        total = len(normalized_pairs)
        completed = 0
        try:
            if copy_pairs:
                completed = self._run_relocation_chunks(
                    dbx,
                    copy_pairs,
                    is_move=False,
                    completed_offset=completed,
                    total_count=total,
                    progress_callback=progress_callback,
                )
            if move_pairs:
                completed = self._run_relocation_chunks(
                    dbx,
                    move_pairs,
                    is_move=True,
                    completed_offset=completed,
                    total_count=total,
                    progress_callback=progress_callback,
                )
        except Exception as exc:
            self._translate_and_raise(exc)

        return total

    def upload_file(
        self,
        local_path: Path,
        dropbox_dest_path: str,
        *,
        shared_folder_id: Optional[str] = None,
        autorename: bool = True,
    ) -> None:
        """Upload a local file into Dropbox (handles >100 MB files via chunked upload sessions)."""
        norm_dest = _normalize_dropbox_path(dropbox_dest_path)
        if not norm_dest:
            raise ValueError("Destination Dropbox path cannot be root.")

        active_ns_id = (
            shared_folder_id.strip()
            if shared_folder_id is not None
            else self.shared_folder_id
        )
        dbx = self.get_client(
            shared_folder_id=active_ns_id,
            pin_shared_folder=bool(active_ns_id),
        )

        file_size = local_path.stat().st_size
        simple_limit = 100 * 1024 * 1024
        chunk_size = 8 * 1024 * 1024

        try:
            with local_path.open("rb") as f:
                if file_size <= simple_limit:
                    dbx.files_upload(
                        f.read(),
                        norm_dest,
                        mode=dropbox.files.WriteMode.add,
                        autorename=autorename,
                    )
                else:
                    start_chunk = f.read(chunk_size)
                    session_res = dbx.files_upload_session_start(start_chunk)
                    cursor = dropbox.files.UploadSessionCursor(
                        session_id=session_res.session_id,
                        offset=len(start_chunk),
                    )
                    commit = dropbox.files.CommitInfo(
                        path=norm_dest,
                        mode=dropbox.files.WriteMode.add,
                        autorename=autorename,
                    )
                    while cursor.offset < file_size:
                        if (file_size - cursor.offset) <= chunk_size:
                            last_chunk = f.read(chunk_size)
                            dbx.files_upload_session_finish(
                                last_chunk, cursor, commit
                            )
                            break
                        else:
                            next_chunk = f.read(chunk_size)
                            dbx.files_upload_session_append_v2(
                                next_chunk, cursor
                            )
                            cursor.offset += len(next_chunk)
                    else:
                        dbx.files_upload_session_finish(b"", cursor, commit)
        except Exception as exc:
            self._translate_and_raise(exc)

    def upload_bytes(
        self,
        data: bytes,
        dropbox_dest_path: str,
        *,
        shared_folder_id: Optional[str] = None,
        autorename: bool = True,
    ) -> None:
        """Upload raw bytes (e.g. reference crop JPEG or summary JSON) directly to Dropbox."""
        norm_dest = _normalize_dropbox_path(dropbox_dest_path)
        if not norm_dest:
            raise ValueError("Destination Dropbox path cannot be root.")

        active_ns_id = (
            shared_folder_id.strip()
            if shared_folder_id is not None
            else self.shared_folder_id
        )
        dbx = self.get_client(
            shared_folder_id=active_ns_id,
            pin_shared_folder=bool(active_ns_id),
        )
        try:
            dbx.files_upload(
                data,
                norm_dest,
                mode=dropbox.files.WriteMode.add,
                autorename=autorename,
            )
        except Exception as exc:
            self._translate_and_raise(exc)
