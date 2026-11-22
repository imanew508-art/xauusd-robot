#!/usr/bin/env python3
"""XAUUSD signals robot — notifications only (never places an order).

  python robot.py live                 run forever, one check after every closed M5 bar
  python robot.py once                 one fetch + one check (for a scheduler / quick test)
  python robot.py replay data_folder   replay CSV candles bar by bar (no network, no Telegram)
  python robot.py test-telegram        send a test message
"""
import argparse
import csv
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import engine
import feeds
import notify
import supabase_sync

HERE = Path(__file__).resolve().parent

DEFAULTS = {
    "data_source": "twelvedata",        # "twelvedata" (needs a free key) or "yahoo" (no key)
    "twelvedata_key": "",
    "twelvedata_symbol": "XAU/USD",
    "yahoo_symbol": "GC=F",             # gold futures: a few dollars above spot, levels shift with it
    "telegram_token": "",
    "telegram_chat_id": "",
    "supabase_url": "",                 # https://xxxx.supabase.co  (optional: feeds the website)
    "supabase_service_key": "",         # secret / service_role key, stays on your PC only
    "bias": "ema",                      # "ema" = only trade with the 1-day EMA trend, "none" = everything
    "ema_bars": 288,
    "buffer": 0.30,
    "min_stop": 1.5,
    "max_stop": 25.0,
    "max_signal_age_bars": 12,
    "bars_to_fetch": 1500,
    "poll_delay_seconds": 10,
}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def load_config(path):
    cfg = dict(DEFAULTS)
    p = Path(path)
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8-sig")))
        except ValueError as e:
            sys.exit(f"config error in {p}: {e}")
    for env, key in (("TELEGRAM_TOKEN", "telegram_token"), ("TELEGRAM_CHAT_ID", "telegram_chat_id"),
                     ("TWELVEDATA_KEY", "twelvedata_key"), ("SUPABASE_URL", "supabase_url"),
                     ("SUPABASE_SERVICE_KEY", "supabase_service_key")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


def make_notifier(cfg, quiet_console=False):
    targets = [] if quiet_console else [notify.Console()]
    if cfg["telegram_token"] and cfg["telegram_chat_id"]:
        targets.append(notify.Telegram(cfg["telegram_token"], str(cfg["telegram_chat_id"])))
    return notify.Multi(*targets)


def market_closed(now):
    wd, hm = now.weekday(), now.hour * 60 + now.minute
    if wd == 4 and hm >= 22 * 60 or wd == 5 or (wd == 6 and hm < 23 * 60):
        return True                                   # weekend
    return 22 * 60 <= hm < 23 * 60                    # daily break


def log_event(data_dir, ev):
    p = Path(data_dir) / "signals_log.csv"
    new = not p.exists()
    with open(p, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time_utc", "event", "side", "level", "entry", "stop", "tp1", "tp2", "tp3", "price", "id"])
        s = ev["sig"]
        w.writerow([ev["time"], ev["event"], s["side"], s["level"], s["entry"], s["stop"],
                    s["tp1"], s["tp2"], s["tp3"], ev["price"], s["id"]])


def run_cycle(cfg, eng, notifier, data_dir):
    now = utcnow()
    bars = feeds.closed_only(feeds.fetch(cfg), now)
    if not bars:
        raise feeds.FeedError("no closed bars")
    if not market_closed(now) and now - bars[-1]["t"] > timedelta(minutes=20):
        raise feeds.FeedError(f"stale feed: last closed bar is {engine.fmt(bars[-1]['t'])} UTC")
    events = eng.process(bars)
    for ev in events:
        notifier.send(notify.message(ev))
        log_event(data_dir, ev)
        supabase_sync.push(cfg, ev["sig"])
    return bars, events


def cmd_live(cfg, once=False, skip_closed=False):
    if once and skip_closed and market_closed(utcnow()):
        print("market closed, nothing to do")
        return
    data_dir = HERE / "data"
    data_dir.mkdir(exist_ok=True)
    eng = engine.Engine(cfg, data_dir / "state.json")
    notifier = make_notifier(cfg)
    if not once:
        notifier.send(f"🤖 XAUUSD robot khddam — source {cfg['data_source']}, bias {cfg['bias']}.\n{notify.DISCLAIMER}")
    if supabase_sync.enabled(cfg):
        ok = supabase_sync.push_all(cfg, list(eng.state["signals"].values()))
        print("[supabase] " + ("synced existing signals" if ok else "connected, nothing to sync yet"), flush=True)
    fails, alerted = 0, False
    while True:
        try:
            if once or not market_closed(utcnow()):
                run_cycle(cfg, eng, notifier, data_dir)
                if alerted:
                    notifier.send("✅ Feed rj3 khddam.")
                fails, alerted = 0, False
        except feeds.FeedError as e:
            fails += 1
            print(f"[{engine.fmt(utcnow())}] feed error ({fails}): {e}", flush=True)
            if fails >= 5 and not alerted:
                notifier.send(f"⚠️ Robot ma9derch ygeb l-prix 5 merrat mtatabi3in: {e}")
                alerted = True
            if once:
                sys.exit(1)
        if once:
            return
        secs = (utcnow() - datetime(1970, 1, 1)).total_seconds()
        nxt = (secs // 300 + 1) * 300 + cfg["poll_delay_seconds"]
        time.sleep(max(1.0, nxt - secs))


def cmd_replay(cfg, spec, quiet):
    paths = []
    for part in spec.split(","):
        p = Path(part.strip())
        paths += sorted(p.glob("*.csv")) if p.is_dir() else [p]
    seen = {}
    for p in paths:
        with open(p, newline="", encoding="utf-8-sig") as f:
            rd = csv.reader(f)
            head = [h.strip().lower() for h in next(rd)]
            ix = {k: head.index(k) for k in ("open", "high", "low", "close")}
            for r in rd:
                if not r:
                    continue
                t = datetime.fromisoformat(r[0].strip().replace("Z", "+00:00"))
                if t.tzinfo:
                    t = t.astimezone(timezone.utc).replace(tzinfo=None)
                seen[t] = {"t": t, "o": float(r[ix["open"]]), "h": float(r[ix["high"]]),
                           "l": float(r[ix["low"]]), "c": float(r[ix["close"]])}
    bars = [seen[k] for k in sorted(seen)]
    if not bars:
        sys.exit("no candles found")
    tmp = tempfile.mkdtemp(prefix="robot_replay_")
    eng = engine.Engine(cfg, Path(tmp) / "state.json")
    out = notify.Console() if not quiet else None
    for i in range(len(bars)):
        window = bars[max(0, i + 1 - cfg["bars_to_fetch"]):i + 1]
        for ev in eng.process(window):
            if out:
                print(f"[{ev['time']} UTC]")
                out.send(notify.message(ev))
    sigs = list(eng.state["signals"].values())
    print(f"\nReplay {engine.fmt(bars[0]['t'])} -> {engine.fmt(bars[-1]['t'])} | bias={cfg['bias']}")
    print(f"{len(sigs)} signal(s) announced, skipped: "
          f"{ {k: list(eng.state['skipped'].values()).count(k) for k in set(eng.state['skipped'].values())} }")
    for s in sigs:
        print(f"  {s['mss_time']} {s['side']:4s} {s['level']:6s} entry {s['entry']:.2f} stop {s['stop']:.2f} -> "
              + " > ".join(s["events"]))


def cmd_test_telegram(cfg):
    if not (cfg["telegram_token"] and cfg["telegram_chat_id"]):
        sys.exit("telegram_token / telegram_chat_id missing (config.json or env)")
    ok = notify.Telegram(cfg["telegram_token"], str(cfg["telegram_chat_id"])).send(
        "✅ Test mn robot XAUUSD — Telegram khddam.")
    print("sent" if ok else "FAILED (see message above)")
    sys.exit(0 if ok else 1)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows consoles default to cp1252
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["live", "once", "replay", "test-telegram"])
    ap.add_argument("path", nargs="?", help="replay: CSV folder or comma separated CSV files")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--bias", choices=["none", "ema"], help="override config bias")
    ap.add_argument("--skip-closed", action="store_true", help="once: do nothing when the market is closed (used by the cloud runner)")
    ap.add_argument("--quiet", action="store_true", help="replay: only print the summary")
    a = ap.parse_args()
    cfg = load_config(a.config)
    if a.bias:
        cfg["bias"] = a.bias
    if a.command == "replay":
        if not a.path:
            sys.exit("replay needs a CSV folder")
        cmd_replay(cfg, a.path, a.quiet)
    elif a.command == "test-telegram":
        cmd_test_telegram(cfg)
    else:
        try:
            cmd_live(cfg, once=(a.command == "once"), skip_closed=a.skip_closed)
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()
