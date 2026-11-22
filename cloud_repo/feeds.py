"""Price feeds: Twelve Data (XAU/USD spot, free API key) and Yahoo Finance (no key). Standard library only."""
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "Mozilla/5.0 (xauusd-robot)"}


class FeedError(Exception):
    pass


def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise FeedError(f"HTTP {e.code} from {urllib.parse.urlparse(url).netloc}") from None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        raise FeedError(f"{type(e).__name__}: {e}") from None


def parse_twelvedata(obj):
    if obj.get("status") == "error" or "values" not in obj:
        raise FeedError(f"Twelve Data: {obj.get('message', 'no values in response')}")
    bars = []
    for v in obj["values"]:
        try:
            bars.append({"t": datetime.strptime(v["datetime"][:19].replace("T", " "), "%Y-%m-%d %H:%M:%S"),
                         "o": float(v["open"]), "h": float(v["high"]),
                         "l": float(v["low"]), "c": float(v["close"])})
        except (KeyError, ValueError):
            continue
    return bars


def parse_yahoo(obj):
    try:
        res = obj["chart"]["result"][0]
        ts, q = res["timestamp"], res["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        err = (obj.get("chart") or {}).get("error")
        raise FeedError(f"Yahoo: {err or 'unexpected response'}") from None
    bars = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        bars.append({"t": datetime.fromtimestamp(t, tz=timezone.utc).replace(tzinfo=None),
                     "o": float(o), "h": float(h), "l": float(l), "c": float(c)})
    return bars


def fetch(cfg):
    src = cfg["data_source"]
    if src == "twelvedata":
        if not cfg.get("twelvedata_key"):
            raise FeedError("twelvedata_key is empty (config.json or env TWELVEDATA_KEY)")
        q = urllib.parse.urlencode({
            "symbol": cfg["twelvedata_symbol"], "interval": "5min", "outputsize": cfg["bars_to_fetch"],
            "timezone": "UTC", "order": "asc", "apikey": cfg["twelvedata_key"]})
        bars = parse_twelvedata(_get_json("https://api.twelvedata.com/time_series?" + q))
    elif src == "yahoo":
        sym = urllib.parse.quote(cfg["yahoo_symbol"])
        bars = parse_yahoo(_get_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=5m&range=5d"))
    else:
        raise FeedError(f"unknown data_source: {src}")
    uniq = {b["t"]: b for b in bars}
    bars = [uniq[k] for k in sorted(uniq)]
    if not bars:
        raise FeedError("feed returned no bars")
    return bars


def closed_only(bars, now, minutes=5):
    """Drop the bar that is still forming."""
    return [b for b in bars if b["t"] + timedelta(minutes=minutes) <= now]
