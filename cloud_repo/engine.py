"""Signal engine for the XAUUSD robot. Standard library only, all times naive UTC.

Pipeline (same logic as the gold-trader pack):
  liquidity levels (PDH/PDL, Asia, London)  ->  sweep + reclaim + MSS  ->  entry at the FVG midpoint
  ->  stop beyond the sweep  ->  lifecycle tracking (entry touched, TP1/TP2/TP3, SL, BE, expired).

The engine only produces notifications. It never places an order.
"""
import json
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

SESSIONS = (("00:00", "07:00", "ASIA"), ("07:00", "12:00", "LON"))
TS = "%Y-%m-%d %H:%M"


def fmt(dt):
    return dt.strftime(TS)


def parse(s):
    return datetime.strptime(s, TS)


def _mins(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _in(t, start, end):
    m = t.hour * 60 + t.minute
    return _mins(start) <= m < _mins(end)


def bar_minutes(bars):
    diffs = sorted((b["t"] - a["t"]).total_seconds() / 60.0
                   for a, b in zip(bars, bars[1:]) if b["t"] > a["t"])
    return diffs[len(diffs) // 2] if diffs else 5.0


# --------------------------------------------------------------------------- levels
def build_levels(bars, day):
    """Liquidity levels for `day`: previous-day high/low and the Asia / London ranges."""
    today = [b for b in bars if b["t"].date() == day]
    d0 = datetime.combine(day, dtime.min)
    liq = []
    prev_dates = sorted({b["t"].date() for b in bars if b["t"].date() < day})
    if prev_dates:
        pb = [b for b in bars if b["t"].date() == prev_dates[-1]]
        liq.append({"name": "PDH", "side": "high", "price": max(b["h"] for b in pb), "from_time": fmt(d0)})
        liq.append({"name": "PDL", "side": "low", "price": min(b["l"] for b in pb), "from_time": fmt(d0)})
    for start, end, tag in SESSIONS:
        sb = [b for b in today if _in(b["t"], start, end)]
        if not sb:
            continue
        h, m = end.split(":")
        avail = fmt(d0 + timedelta(hours=int(h), minutes=int(m)))   # a range is only liquidity once it is complete
        liq.append({"name": f"{tag}_H", "side": "high", "price": max(b["h"] for b in sb), "from_time": avail})
        liq.append({"name": f"{tag}_L", "side": "low", "price": min(b["l"] for b in sb), "from_time": avail})
    return liq


# --------------------------------------------------------------------------- setups
def find_setups(bars, liquidity, buffer, lookback=12, reclaim=6, mss_win=10):
    """Sweep of a level, close back inside within `reclaim` bars, MSS within `mss_win` bars."""
    found = []
    n = len(bars)
    for lv in liquidity:
        side = "sell" if lv["side"] == "high" else "buy"
        p = lv["price"]
        start = parse(lv["from_time"])
        i = 0
        while i < n:
            b = bars[i]
            if b["t"] < start:
                i += 1
                continue
            swept = b["h"] > p if side == "sell" else b["l"] < p
            if not swept:
                i += 1
                continue
            j = None
            for jj in range(i, min(i + reclaim + 1, n)):
                c = bars[jj]["c"]
                if (side == "sell" and c < p) or (side == "buy" and c > p):
                    j = jj
                    break
            if j is None:
                i += 1
                continue
            seg = bars[i:j + 1]
            ext = max(x["h"] for x in seg) if side == "sell" else min(x["l"] for x in seg)
            prior = bars[max(0, i - lookback):i]
            if not prior:
                i += 1
                continue
            ref = min(x["l"] for x in prior) if side == "sell" else max(x["h"] for x in prior)
            k = None
            for kk in range(j, min(j + mss_win + 1, n)):
                c = bars[kk]["c"]
                if (side == "sell" and c < ref) or (side == "buy" and c > ref):
                    k = kk
                    break
            if k is None:
                i += 1
                continue

            best = None
            for m in range(max(i, 1), min(k, n - 2) + 1):
                a, c = bars[m - 1], bars[m + 1]
                if side == "sell" and a["l"] > c["h"]:
                    gap = (c["h"], a["l"])
                elif side == "buy" and a["h"] < c["l"]:
                    gap = (a["h"], c["l"])
                else:
                    continue
                size = gap[1] - gap[0]
                if best is None or size > best[0]:
                    best = (size, gap, m)
            if best:
                lo, hi = best[1]
                entry, etype, scan_from = (lo + hi) / 2, "FVG midpoint", best[2] + 2
            else:
                disp = bars[k]["l"] if side == "sell" else bars[k]["h"]
                entry, etype, scan_from = (ext + disp) / 2, "50% of displacement", k + 1
            stop = ext + buffer if side == "sell" else ext - buffer

            status = "pending"
            for q in range(scan_from, n):
                bq = bars[q]
                if side == "sell":
                    if bq["h"] >= stop:
                        status = "invalidated"
                        break
                    if bq["h"] >= entry:
                        status = "triggered"
                        break
                else:
                    if bq["l"] <= stop:
                        status = "invalidated"
                        break
                    if bq["l"] <= entry:
                        status = "triggered"
                        break

            found.append({
                "level": lv["name"], "also_swept": [], "side": side,
                "sweep_time": fmt(bars[i]["t"]), "mss_time": fmt(bars[k]["t"]),
                "entry": round(entry, 2), "entry_type": etype, "stop": round(stop, 2),
                "status": status, "scan_after": fmt(bars[scan_from - 1]["t"]),
            })
            i = k + 1

    uniq = {}
    for s in sorted(found, key=lambda x: x["sweep_time"]):
        key = (s["side"], s["mss_time"])
        if key in uniq:
            uniq[key]["also_swept"].append(s["level"])
        else:
            uniq[key] = s
    return sorted(uniq.values(), key=lambda x: x["sweep_time"])


def ema_by_time(bars, length):
    k, ema, out = 2.0 / (length + 1), None, {}
    for b in bars:
        ema = b["c"] if ema is None else ema + k * (b["c"] - ema)
        out[fmt(b["t"])] = (ema, b["c"])
    return out


# --------------------------------------------------------------------------- lifecycle
def track(sig, bars):
    """Replay the bars after the setup and return every milestone reached so far.

    Returns [(event, time_str, price)] with events: invalid, entry, tp1, tp2, tp3, sl, be.
    Same-bar conflicts are resolved against the trader (stop first). After TP1 the stop is
    moved to break-even, as in the trade plan.
    """
    side, entry, stop = sig["side"], sig["entry"], sig["stop"]
    d = 1 if side == "buy" else -1
    after = parse(sig["scan_after"])
    day = sig["date"]
    out = []
    phase = "pending"
    cur_stop = stop
    levels = [("tp1", sig["tp1"]), ("tp2", sig["tp2"]), ("tp3", sig["tp3"])]
    nxt = 0
    for b in bars:
        if b["t"] <= after or str(b["t"].date()) != day:
            continue
        t = fmt(b["t"])
        hi, lo = b["h"], b["l"]
        if phase == "pending":
            if (side == "buy" and lo <= stop) or (side == "sell" and hi >= stop):
                out.append(("invalid", t, stop))
                return out
            if (side == "buy" and lo <= entry) or (side == "sell" and hi >= entry):
                out.append(("entry", t, entry))
                phase = "open"
            continue                                   # the trigger bar never counts towards a target
        hit_stop = lo <= cur_stop if side == "buy" else hi >= cur_stop
        if hit_stop:
            out.append(("be" if nxt >= 1 else "sl", t, cur_stop))
            return out
        while nxt < 3:
            name, px = levels[nxt]
            if (side == "buy" and hi >= px) or (side == "sell" and lo <= px):
                out.append((name, t, px))
                nxt += 1
            else:
                break
        if nxt >= 1:
            cur_stop = entry                           # break-even applies from the next bar on
        if nxt == 3:
            return out
    return out


FINAL = {"invalid", "sl", "be", "tp3", "expired"}


# --------------------------------------------------------------------------- engine
class Engine:
    def __init__(self, cfg, state_path):
        self.cfg = cfg
        self.path = Path(state_path)
        self.state = {"signals": {}, "skipped": {}}
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def _skip(self, sid, why):
        self.state["skipped"].setdefault(sid, why)

    def process(self, bars):
        """Feed closed bars (ascending). Returns a list of {event, sig, time, price} to notify."""
        cfg, out = self.cfg, []
        if len(bars) < 40:
            return out
        last_t = bars[-1]["t"]
        D = last_t.date()
        step = bar_minutes(bars)
        day_bars = [b for b in bars if b["t"].date() == D]
        liq = build_levels(bars, D)
        setups = find_setups(day_bars, liq, cfg["buffer"])
        emas = ema_by_time(bars, cfg["ema_bars"]) if cfg["bias"] == "ema" else {}

        for s in setups:
            sid = f'{s["side"]}|{s["mss_time"]}'
            if sid in self.state["signals"] or sid in self.state["skipped"]:
                continue
            mss_t = parse(s["mss_time"])
            if mss_t >= last_t:
                continue                    # wait one more bar: the FVG that sets the entry may still change
            risk = abs(s["entry"] - s["stop"])
            if s["status"] != "pending":
                self._skip(sid, "missed" if s["status"] == "triggered" else "invalidated")
                continue
            if (last_t - mss_t).total_seconds() / 60.0 > cfg["max_signal_age_bars"] * step:
                self._skip(sid, "stale")
                continue
            if not (cfg["min_stop"] <= risk <= cfg["max_stop"]):
                self._skip(sid, "stop size")
                continue
            trend = None
            if cfg["bias"] == "ema":
                e = emas.get(s["mss_time"])
                if e:
                    trend = "up" if e[1] > e[0] else "down"
                    if (s["side"] == "buy") != (trend == "up"):
                        self._skip(sid, "against trend")
                        continue
            d = 1 if s["side"] == "buy" else -1
            sig = dict(s, id=sid, date=str(D), risk=round(risk, 2), trend=trend, events=["signal"],
                       log=[["signal", fmt(last_t), s["entry"]]],
                       tp1=round(s["entry"] + d * risk, 2), tp2=round(s["entry"] + d * 2 * risk, 2),
                       tp3=round(s["entry"] + d * 3 * risk, 2))
            sig.pop("status", None)
            self.state["signals"][sid] = sig
            out.append({"event": "signal", "sig": sig, "time": fmt(last_t), "price": s["entry"]})

        for sid, sig in self.state["signals"].items():
            if any(e in FINAL for e in sig["events"]):
                continue
            for name, t, px in track(sig, bars):
                if name not in sig["events"]:
                    sig["events"].append(name)
                    sig.setdefault("log", []).append([name, t, px])
                    out.append({"event": name, "sig": sig, "time": t, "price": px})
                    if name in FINAL:
                        break
            if not any(e in FINAL for e in sig["events"]) and sig["date"] < str(D):
                same = [b for b in bars if str(b["t"].date()) == sig["date"]]
                px = same[-1]["c"] if same else bars[-1]["c"]
                sig["events"].append("expired")
                sig.setdefault("log", []).append(["expired", fmt(last_t), px])
                out.append({"event": "expired", "sig": sig, "time": fmt(last_t), "price": px})
        self.save()
        return out
