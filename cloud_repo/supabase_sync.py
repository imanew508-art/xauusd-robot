"""Push signals to Supabase (PostgREST upsert). Standard library only, never raises.

Every push sends the WHOLE row (status + full event log), so a failed push heals itself on the next one.
"""
import json
import urllib.error
import urllib.request

TABLE = "robot_signals"


def enabled(cfg):
    return bool(cfg.get("supabase_url") and cfg.get("supabase_service_key"))


def status_and_r(events):
    """Status shown on the site + banked result in R.

    Trade plan: 50% closed at TP1 (1R), stop of the rest moves to break-even, runner targets TP3 (3R).
      sl -> -1R | tp1 then back to BE -> +0.5R | tp3 -> 0.5*1 + 0.5*3 = +2R
    Open trades and expired trades with an unknown floating result have result_r = None.
    """
    ev = set(events)
    if "invalid" in ev:
        return "invalid", None
    if "expired" in ev:
        return "expired", (0.5 if "tp1" in ev else None)
    if "sl" in ev:
        return "sl", -1.0
    if "be" in ev:
        return "be", 0.5
    if "tp3" in ev:
        return "tp3", 2.0
    if "tp2" in ev:
        return "tp2", None
    if "tp1" in ev:
        return "tp1", None
    if "entry" in ev:
        return "active", None
    return "pending", None


def row(sig):
    status, r = status_and_r(sig["events"])
    return {
        "id": sig["id"],
        "signal_time": sig["mss_time"].replace(" ", "T") + ":00+00:00",
        "side": sig["side"],
        "level": sig["level"] + "".join("+" + x for x in sig.get("also_swept", [])),
        "entry": sig["entry"], "stop": sig["stop"],
        "tp1": sig["tp1"], "tp2": sig["tp2"], "tp3": sig["tp3"],
        "risk": sig["risk"], "trend": sig.get("trend"),
        "status": status, "result_r": r,
        "log": sig.get("log", []),
        "updated_at": None,          # replaced below
    }


def _post(cfg, rows, timeout=15):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        r["updated_at"] = now
    key = cfg["supabase_service_key"]
    headers = {"apikey": key, "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    if key.startswith("eyJ"):                       # legacy JWT keys also go in Authorization
        headers["Authorization"] = "Bearer " + key
    url = f"{cfg['supabase_url'].rstrip('/')}/rest/v1/{TABLE}?on_conflict=id"
    req = urllib.request.Request(url, data=json.dumps(rows).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        hint = {401: "check supabase_service_key", 403: "check supabase_service_key (service key, not anon)",
                404: "table missing: run supabase_setup.sql in the Supabase SQL editor"}.get(e.code, "")
        print(f"[supabase] HTTP {e.code} {hint} {body}", flush=True)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        print(f"[supabase] {type(e).__name__}: {e}", flush=True)
    return False


def push(cfg, sig):
    return enabled(cfg) and _post(cfg, [row(sig)])


def push_all(cfg, signals):
    return enabled(cfg) and bool(signals) and _post(cfg, [row(s) for s in signals])
