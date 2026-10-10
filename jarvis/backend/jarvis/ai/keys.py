"""The Claude API key: OS keystore first (macOS Keychain, Windows Credential Manager,
Secret Service); a key left in ``jarvis/.env`` from earlier versions is moved there once.

The key is read for the duration of building a client and never logged, emitted,
returned by the API or put into a prompt. Only its last four characters are shown.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from keyring.backend import KeyringBackend

from jarvis.quantlab.hub.vault import CredentialVault, MemoryKeyring, VaultError
from jarvis.settings import read_dotenv

log = logging.getLogger("jarvis.ai.keys")

SERVICE = "JARVIS AI"
ACCOUNT = "anthropic-api-key"
ENV_NAME = "ANTHROPIC_API_KEY"


class ApiKeyStore:
    def __init__(
        self,
        *,
        memory: bool,
        env_file: Path,
        env_key: str | None,
        backend: KeyringBackend | None = None,
    ) -> None:
        chosen = backend if backend is not None else (MemoryKeyring() if memory else None)
        self._vault = CredentialVault(chosen, service=SERVICE)
        self._memory = memory
        self._env_file = env_file
        self._env_key = env_key  # from the process environment or .env at startup

    @property
    def vault(self) -> CredentialVault:
        return self._vault

    def keystore(self) -> dict[str, object]:
        return self._vault.status().as_dict()

    def _stored(self) -> str | None:
        if not self._vault.status().available:
            return None
        try:
            return self._vault.get(ACCOUNT)
        except VaultError:
            return None

    def get(self) -> str | None:
        return self._stored() or self._env_key

    def source(self) -> str | None:
        if self._stored():
            return "keystore"
        if self._env_key:
            return "env_file" if read_dotenv(self._env_file).get(ENV_NAME) else "environment"
        return None

    def hint(self) -> str | None:
        key = self.get()
        return key[-4:] if key else None

    def put(self, key: str) -> None:
        """Store in the OS keystore (raises ``VaultError`` when none is reachable)."""
        self._vault.put(ACCOUNT, key)
        if self._vault.get(ACCOUNT) != key:
            raise VaultError("KEYSTORE_ERROR", "The keystore didn't keep the key.")
        self._env_key = None if self._env_key == key else self._env_key

    def delete(self) -> None:
        if self._vault.status().available:
            self._vault.delete(ACCOUNT)
        if self._env_key and read_dotenv(self._env_file).get(ENV_NAME) == self._env_key:
            _remove_line(self._env_file, ENV_NAME)
        self._env_key = None

    def migrate(self) -> bool:
        """Move a key from ``.env`` into the OS keystore once (never with the test store)."""
        if self._memory or not self._env_key or not self._vault.status().available:
            return False
        if read_dotenv(self._env_file).get(ENV_NAME) != self._env_key:
            return False  # set in the shell environment: not ours to move
        try:
            if self._vault.get(ACCOUNT) is None:
                self.put(self._env_key)
            elif self._vault.get(ACCOUNT) != self._env_key:
                return False  # a different key is already stored; leave both alone
            _remove_line(self._env_file, ENV_NAME)
        except (VaultError, OSError) as exc:
            log.warning("could not move the API key into the keystore: %s", type(exc).__name__)
            return False
        self._env_key = None
        log.info("API key moved from .env into the OS keystore")
        return True


def _remove_line(path: Path, name: str) -> None:
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    kept = [
        line
        for line in lines
        if line.strip().removeprefix("export ").split("=", 1)[0].strip() != name
        or line.lstrip().startswith("#")
    ]
    if len(kept) == len(lines):
        return
    kept.append(f"# {name} moved to the OS keystore by JARVIS (Settings → AI & Billing).")
    tmp = path.with_name(f"{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(kept) + "\n")
    os.replace(tmp, path)
