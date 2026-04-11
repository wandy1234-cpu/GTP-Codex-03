"""Runtime configuration for Quant Alpha."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

DEFAULT_AKSHARE_TOKEN = "4de5bc6ef18cbd032999b72d3245c4566c0be59b00d70839db24bc23"


@dataclass(frozen=True)
class AkshareConfig:
    """Configuration used by the AkShare adapter."""

    token: str | None = None

    @classmethod
    def from_env(cls) -> "AkshareConfig":
        token = (
            os.getenv("AKSHARE_TOKEN")
            or os.getenv("AKSHARE_API_KEY")
            or DEFAULT_AKSHARE_TOKEN
        )
        if token:
            token = token.strip().strip('"').strip("'")
        return cls(token=token or None)


@dataclass(frozen=True)
class ProjectPaths:
    """Filesystem layout for data and model artifacts."""

    root: Path = Path.cwd()

    @property
    def data_raw(self) -> Path:
        return self.root / "data" / "raw"

    @property
    def data_feature(self) -> Path:
        return self.root / "data" / "feature"

    @property
    def model_dir(self) -> Path:
        return self.root / "models"

    @property
    def report_dir(self) -> Path:
        return self.root / "reports"

    def ensure(self) -> None:
        for path in [self.data_raw, self.data_feature, self.model_dir, self.report_dir]:
            path.mkdir(parents=True, exist_ok=True)
