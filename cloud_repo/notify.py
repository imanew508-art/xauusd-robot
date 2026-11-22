"""Notifications: Telegram + console, and the message texts. Standard library only."""
import json
import urllib.error
import urllib.request

DISCLAIMER = "Signal educatif / paper — machi nasiha maliya. L'robot ma kaydir hta order."


def _p(x):
    return f"{x:.2f}"


def message(ev):
    s, name, px, t = ev["sig"], ev["event"], ev["price"], ev["time"]
    side = s["side"].upper()
    icon = "🟢" if s["side"] == "buy" else "🔴"
    head = f"{icon} {side} XAUUSD"
    swept = s["level"] + ("".join(f" +{x}" for x in s["also_swept"]))
    if name == "signal":
        trend = f"\nTrend (EMA): {s['trend']}" if s.get("trend") else ""
        return (f"{head} — LIMIT ORDER\n"
                f"Sweep {swept} -> MSS {s['mss_time'][11:]} UTC{trend}\n\n"
                f"Entry: {_p(s['entry'])}\n"
                f"SL: {_p(s['stop'])}  ({_p(s['risk'])}$)\n"
                f"TP1: {_p(s['tp1'])}  (1R)\n"
                f"TP2: {_p(s['tp2'])}  (2R)\n"
                f"TP3: {_p(s['tp3'])}  (3R)\n\n"
                f"L-limit kat-t-activa ghir ila l-prix rje3 l Entry qbel ma ylmess SL.\n"
                f"{DISCLAIMER}")
    lines = {
        "entry": f"✅ Entry touché {_p(px)} — l-trade tfetah ({t[11:]} UTC).\nSL {_p(s['stop'])} | TP1 {_p(s['tp1'])}",
        "tp1": f"🎯 TP1 touché {_p(px)} (1R) — sedd 50% w 7ered SL l BE ({_p(s['entry'])}).",
        "tp2": f"🎯🎯 TP2 touché {_p(px)} (2R).",
        "tp3": f"🏆 TP3 touché {_p(px)} (3R) — trade safi.",
        "sl": f"❌ SL touché {_p(px)} (-1R).",
        "be": f"➖ Rje3 l BE {_p(px)} mn be3d TP1 — trade safi (TP1 deja mkhrej).",
        "invalid": f"⚠️ Setup tlgha: l-prix ṭla3 l SL ({_p(px)}) qbel Entry. Ma tdkhelch.",
        "expired": f"⏱ Signal tsala (nhar tsala) — akhir prix {_p(px)}.",
    }
    return f"{head} | {swept} @ {s['entry']:.2f}\n{lines[name]}"


class Console:
    def send(self, text):
        print(text + "\n" + "-" * 40, flush=True)
        return True


class Telegram:
    def __init__(self, token, chat_id):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def send(self, text):
        body = json.dumps({"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.loads(r.read().decode()).get("ok", False)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:200]
                print(f"[telegram] HTTP {e.code}: {detail}", flush=True)
                if e.code in (400, 401, 403, 404):
                    return False                     # wrong token / chat id: retrying will not help
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                print(f"[telegram] {type(e).__name__}: {e}", flush=True)
        return False


class Multi:
    def __init__(self, *targets):
        self.targets = targets

    def send(self, text):
        ok = False
        for t in self.targets:
            ok = t.send(text) or ok
        return ok
