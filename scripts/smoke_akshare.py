"""Simple smoke test for AkshareAdapter."""

from datetime import date, timedelta
from pathlib import Path
import sys

try:
    from quant_alpha.data.akshare_adapter import AkshareAdapter
except ModuleNotFoundError:
    src_root = Path(__file__).resolve().parents[1] / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from quant_alpha.data.akshare_adapter import AkshareAdapter


if __name__ == "__main__":
    adapter = AkshareAdapter.from_env()

    a_spot = adapter.fetch_spot("A")
    hk_spot = adapter.fetch_spot("HK")

    print("A spot rows:", len(a_spot), "columns:", list(a_spot.columns[:8]))
    print("HK spot rows:", len(hk_spot), "columns:", list(hk_spot.columns[:8]))

    end = date.today()
    start = end - timedelta(days=60)
    a_hist = adapter.fetch_history("000001", "A", start=start, end=end)
    hk_hist = adapter.fetch_history("00700", "HK", start=start, end=end)

    print("A hist rows:", len(a_hist), "range:", a_hist["date"].min(), "->", a_hist["date"].max())
    print("HK hist rows:", len(hk_hist), "range:", hk_hist["date"].min(), "->", hk_hist["date"].max())
