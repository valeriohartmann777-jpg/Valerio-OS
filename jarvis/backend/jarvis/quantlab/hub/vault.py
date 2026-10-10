"""Provider credentials live in the operating system's keystore, nowhere else.

macOS Keychain, Windows Credential Manager or the Linux Secret Service, through
``keyring``. When no such keystore is reachable the vault says so and refuses:
there is deliberately no plaintext fallback (no file, no SQLite column, no
environment variable). The secret is read for the duration of one provider
call and never cached, logged, emitted or returned by the API.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import keyring
from keyring.backend import KeyringBackend
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE = "JARVIS QuantLab"

# Backends that don't protect the secret with the OS (or don't store it at all).
_INSECURE = ("keyring.backends.fail", "keyring.backends.null", "keyrings.alt")
_NAMES = {
    "keyring.backends.macOS": "macOS Keychain",
    "keyring.backends.Windows": "Windows Credential Manager",
    "keyring.backends.SecretService": "Secret Service",
    "keyring.backends.libsecret": "libsecret",
    "keyring.backends.kwallet": "KWallet",
}


class VaultError(Exception):
    def __init__(self, code: str, message: str, remedy: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remedy = remedy


@dataclass(frozen=True)
class VaultStatus:
    available: bool
    backend: str
    reason: str | None
    test_only: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "backend": self.backend,
            "reason": self.reason,
            "test_only": self.test_only,
        }


class MemoryKeyring(KeyringBackend):
    """Process memory only — for automated tests and the E2E run, labelled as such."""

    priority = 0  # never chosen automatically

    def __init__(self) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self._items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self._items.pop((service, username), None) is None:
            raise PasswordDeleteError("not found")


def _secure(backend: KeyringBackend) -> tuple[bool, str]:
    module = type(backend).__module__
    if module == "keyring.backends.chainer":
        inner = list(getattr(backend, "backends", []))
        if not inner:
            return False, "no keystore backend"
        results = [_secure(b) for b in inner]
        names = ", ".join(name for _, name in results)
        return all(ok for ok, _ in results), names
    if module.startswith(_INSECURE):
        return False, module
    return True, _NAMES.get(module, f"{type(backend).__name__} ({module})")


class CredentialVault:
    def __init__(self, backend: KeyringBackend | None = None, service: str = SERVICE) -> None:
        self._backend = backend
        self._service = service
        self._lock = threading.Lock()

    def _resolve(self) -> KeyringBackend:
        return self._backend if self._backend is not None else keyring.get_keyring()

    def status(self) -> VaultStatus:
        backend = self._resolve()
        if isinstance(backend, MemoryKeyring):
            return VaultStatus(True, "Test keystore (memory, not persisted)", None, True)
        ok, name = _secure(backend)
        if ok:
            return VaultStatus(True, name, None, False)
        return VaultStatus(
            False,
            name,
            "No OS-protected keystore is reachable (macOS Keychain, Windows Credential "
            "Manager or Secret Service). Credentials are never stored in plain text, so "
            "connecting is disabled here.",
            False,
        )

    def _require(self) -> KeyringBackend:
        status = self.status()
        if not status.available:
            raise VaultError(
                "NO_SECURE_KEYSTORE",
                status.reason or "No secure keystore.",
                "Run JARVIS on your Mac (Keychain) or a desktop with Secret Service.",
            )
        return self._resolve()

    def get(self, account: str) -> str | None:
        backend = self._require()
        with self._lock:
            try:
                return backend.get_password(self._service, account)
            except KeyringError as exc:
                raise VaultError(
                    "KEYSTORE_ERROR",
                    f"The keystore refused to read the credential ({type(exc).__name__}).",
                    "Unlock the keychain and allow JARVIS access, then try again.",
                ) from None

    def put(self, account: str, secret: str) -> None:
        backend = self._require()
        with self._lock:
            try:
                backend.set_password(self._service, account, secret)
            except KeyringError as exc:
                raise VaultError(
                    "KEYSTORE_ERROR",
                    f"The keystore refused to store the credential ({type(exc).__name__}).",
                    "Unlock the keychain and allow JARVIS access, then try again.",
                ) from None

    def delete(self, account: str) -> bool:
        """Remove the credential; False when there was none."""
        backend = self._require()
        with self._lock:
            try:
                backend.delete_password(self._service, account)
            except PasswordDeleteError:
                return False
            except KeyringError as exc:
                raise VaultError(
                    "KEYSTORE_ERROR",
                    f"The keystore refused to delete the credential ({type(exc).__name__}).",
                    "Unlock the keychain and try again.",
                ) from None
        return True
