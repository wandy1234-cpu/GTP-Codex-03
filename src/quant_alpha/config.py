"""Runtime configuration for Quant Alpha."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class AkshareConfig:
    """Configuration used by the AkShare adapter."""

    token: str | None = None

    @classmethod
    def from_env(cls) -> "AkshareConfig":
        """Load configuration from environment variables.

        Environment variables:
            AKSHARE_TOKEN: Preferred token value.
            AKSHARE_API_KEY: Fallback token variable.
        """
        token = os.getenv("AKSHARE_TOKEN") or os.getenv("AKSHARE_API_KEY")
        if token:
            token = token.strip()
        return cls(token=token or None)
