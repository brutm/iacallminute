"""Errori di provider AI condivisi (Gemini e endpoint OpenAI-compat)."""
from __future__ import annotations

import re


def net_error_text(exc: Exception) -> str:
    """Traduce un'eccezione di rete in un messaggio leggibile.

    requests avvolge cosi' i reset: "('Connection aborted.',
    ConnectionResetError(10054, \"...\"))" — inutile per l'utente.
    """
    raw = str(exc)
    name = type(exc).__name__
    code = re.search(r"\((\d{3,5}),", raw)
    if ("Connection aborted" in raw or "ConnectionReset" in name
            or "ConnectionResetError" in raw):
        return (f"connessione interrotta dal provider "
                f"(errore {code.group(1) if code else '10054'})")
    if "Read timed out" in raw or "ReadTimeout" in name:
        return "timeout: il provider non ha risposto in tempo"
    if "timed out" in raw.lower():
        return "timeout verso il provider"
    if "SSLError" in name or "SSL" in name:
        return "errore di connessione sicura (SSL)"
    return f"errore di rete: {name}"


class ProviderError(Exception):
    def __init__(self, message: str, status: int = 0, retryable: bool = False,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after
