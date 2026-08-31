#!/usr/bin/env python3
"""
Kitecheck -- twee keer per dag een overzicht van waar het deze week kan.

Haalt per spot vier weermodellen op bij Open-Meteo, scoort elk uur tegen je
eigen criteria, en zet er een tabel van in HTML plus een mailtje.

Draait zonder API-key. Zie README.md voor de setup.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, date
from email.message import EmailMessage

from spots import SPOTS, RIDERS

API = "https://api.open-meteo.com/v1/forecast"

# Hoge resolutie, maar korte horizon (~2 dagen).
MODELS_NEAR = ["knmi_harmonie_arome_netherlands", "icon_d2"]
# Grover, maar reikt de hele week.
MODELS_FAR = ["ecmwf_ifs025", "icon_global"]
ALL_MODELS = MODELS_NEAR + MODELS_FAR

MODEL_LABELS = {
    "knmi_harmonie_arome_netherlands": "HARMONIE 2km",
    "icon_d2": "ICON-D2 2km",
    "ecmwf_ifs025": "ECMWF 25km",
    "icon_global": "ICON 11km",
}

# Alleen deze uren tellen mee -- 's nachts kiten doe je toch niet.
DAY_START, DAY_END = 9, 21

FORECAST_DAYS = 7
THUNDER_CODES = {95, 96, 99}

GREEN, AMBER, RED = "green", "amber", "red"
RANK = {RED: 0, AMBER: 1, GREEN: 2}
DOT = {GREEN: "\U0001F7E2", AMBER: "\U0001F7E1", RED: "\U0001F534"}


# ---------------------------------------------------------------------------
# Ophalen
# ---------------------------------------------------------------------------

def fetch(spot: dict) -> dict:
    params = {
        "latitude": spot["lat"],
        "longitude": spot["lon"],
        "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
                  "precipitation,weather_code",
        "wind_speed_unit": "kn",
        "timezone": "Europe/Amsterdam",
        "forecast_days": FORECAST_DAYS,
        "models": ",".join(ALL_MODELS),
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=45) as resp:
        return json.loads(resp.read().decode())


def series(hourly: dict, field: str, model: str) -> list:
    """Open-Meteo hangt de modelnaam achter de veldnaam als je er meer opvraagt."""
    for key in (f"{field}_{model}", field):
        if key in hourly:
            return hourly[key]
    return []


# ---------------------------------------------------------------------------
# Scoren
# ---------------------------------------------------------------------------

def dir_ok(deg: float, sectors: list) -> bool:
    if deg is None:
        return False
    for lo, hi in sectors:
        if lo <= hi:
            if lo <= deg <= hi:
                return True
        else:  # sector loopt over 0 graden heen, bv (315, 45)
            if deg >= lo or deg <= hi:
                return True
    return False


def score_hour(speed, gust, direction, precip, code, spot, rider) -> str:
    if None in (speed, gust, direction):
        return RED
    if code in THUNDER_CODES:
        return RED
    if not dir_ok(direction, spot["dirs"]):
        return RED

    delta = gust - speed
    lo, hi, max_delta = rider["min_kn"], rider["max_kn"], rider["max_delta"]
    rain = precip or 0.0

    if lo <= speed <= hi and delta <= max_delta and rain < 1.0:
        return GREEN
    # Randgevallen: net te slap, net te hard, net te vlagerig, of nat.
    if (lo - 2) <= speed <= (hi + 3) and delta <= (max_delta + 3) and rain < 2.5:
        return AMBER
    return RED


def score_model_day(hours: list, spot: dict, rider: dict) -> dict:
    """hours = lijst van dicts voor een enkele dag, enkel model."""
    graded = []
    for h in hours:
        if not (DAY_START <= h["hour"] <= DAY_END):
            continue
        graded.append((h, score_hour(h["speed"], h["gust"], h["direction"],
                                     h["precip"], h["code"], spot, rider)))
    if not graded:
        return {"verdict": RED, "window": None, "speed": None, "delta": None}

    best = max(RANK[g] for _, g in graded)
    verdict = [k for k, v in RANK.items() if v == best][0]

    # Langste aaneengesloten blok op het beste niveau.
    run, longest = [], []
    for h, g in graded:
        if RANK[g] == best:
            run.append(h)
            if len(run) > len(longest):
                longest = list(run)
        else:
            run = []

    if not longest or verdict == RED:
        return {"verdict": verdict, "window": None, "speed": None, "delta": None}

    speeds = [h["speed"] for h in longest]
    deltas = [h["gust"] - h["speed"] for h in longest]
    return {
        "verdict": verdict,
        "window": (longest[0]["hour"], longest[-1]["hour"] + 1),
        "speed": (round(min(speeds)), round(max(speeds))),
        "delta": round(max(deltas)),
    }


def regroup(data: dict, model: str) -> dict:
    """Zet de platte Open-Meteo-reeksen om naar {datum: [uur-dicts]}."""
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    speeds = series(hourly, "wind_speed_10m", model)
    gusts = series(hourly, "wind_gusts_10m", model)
    dirs = series(hourly, "wind_direction_10m", model)
    precs = series(hourly, "precipitation", model)
    codes = series(hourly, "weather_code", model)

    if not speeds:
        return {}

    days = defaultdict(list)
    for i, t in enumerate(times):
        stamp = datetime.fromisoformat(t)
        days[stamp.date()].append({
            "hour": stamp.hour,
            "speed": speeds[i] if i < len(speeds) else None,
            "gust": gusts[i] if i < len(gusts) else None,
            "direction": dirs[i] if i < len(dirs) else None,
            "precip": precs[i] if i < len(precs) else None,
            "code": codes[i] if i < len(codes) else None,
        })
    # Dagen zonder bruikbare data (model reikt niet zo ver) eruit gooien.
    return {d: hs for d, hs in days.items()
            if any(h["speed"] is not None for h in hs)}


def models_for_day(available: dict, day: date) -> list:
    """Kies per dag de twee beste modellen die die dag nog dekken."""
    near = [m for m in MODELS_NEAR if day in available.get(m, {})]
    far = [m for m in MODELS_FAR if day in available.get(m, {})]
    chosen = near + far
    return chosen[:2]


# ---------------------------------------------------------------------------
# Uitvoer
# ---------------------------------------------------------------------------

def cell_text(res: dict) -> str:
    if res["verdict"] == RED or not res["window"]:
        return ""
    a, b = res["window"]
    lo, hi = res["speed"]
    speed = f"{lo}" if lo == hi else f"{lo}-{hi}"
    return f"{a}-{b}u &middot; {speed} kn &middot; \u0394{res['delta']}"


def build_rows(spot: dict, rider: dict, available: dict, days: list) -> list:
    rows = []
    for day in days:
        chosen = models_for_day(available, day)
        results = []
        for m in chosen:
            hours = available[m][day]
            r = score_model_day(hours, spot, rider)
            r["model"] = m
            results.append(r)
        rows.append({"day": day, "results": results})
    return rows


DAYNAMES = ["ma", "di", "wo", "do", "vr", "za", "zo"]


def render_html(tables: dict, days: list, stamp: str) -> str:
    css = """
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         margin:0;padding:20px;background:#fbfbf9;color:#1c1c1a;font-size:15px}
    h1{font-size:20px;margin:0 0 2px}
    .stamp{color:#77776f;font-size:13px;margin-bottom:22px}
    h2{font-size:16px;margin:26px 0 8px}
    table{border-collapse:collapse;width:100%;margin-bottom:6px}
    th,td{text-align:left;padding:7px 8px;border-bottom:1px solid #e6e6e0;
          vertical-align:top}
    th{font-weight:600;font-size:13px;color:#55554f}
    td.spot{font-weight:600;white-space:nowrap}
    .cell{font-size:12px;color:#55554f;line-height:1.45}
    .dots{font-size:14px;letter-spacing:1px}
    .note{color:#77776f;font-size:12px;margin:2px 0 18px}
    .legend{margin-top:26px;color:#77776f;font-size:12px;line-height:1.6}
    """
    out = [f"<!doctype html><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Kitecheck</title><style>{css}</style>",
           "<h1>Kitecheck</h1>",
           f"<div class='stamp'>Bijgewerkt {stamp}</div>"]

    for rider_key, spot_tables in tables.items():
        rider = RIDERS[rider_key]
        out.append(f"<h2>{rider['label']} &middot; {rider['min_kn']}-"
                   f"{rider['max_kn']} kn, delta max {rider['max_delta']}</h2>")

        for primary in (True, False):
            group = [(s, rows) for s, rows in spot_tables if s["primary"] == primary]
            if not group:
                continue
            if not primary:
                out.append("<h2 style='font-size:14px;color:#77776f'>"
                           "Overige spots</h2>")
            out.append("<table><tr><th>Spot</th>")
            for d in days:
                out.append(f"<th>{DAYNAMES[d.weekday()]} {d.day}</th>")
            out.append("</tr>")

            for spot, rows in group:
                if rider["needs_shallow"] and not spot["shallow"]:
                    continue
                out.append(f"<tr><td class='spot'>{spot['name']}<br>"
                           f"<span class='cell'>{spot['drive_min']} min</span></td>")
                for row in rows:
                    dots = "".join(DOT[r["verdict"]] for r in row["results"]) or "&mdash;"
                    texts = [cell_text(r) for r in row["results"]]
                    body = "<br>".join(t for t in texts if t)
                    out.append(f"<td><span class='dots'>{dots}</span>"
                               f"<div class='cell'>{body}</div></td>")
                out.append("</tr>")
            out.append("</table>")
            for spot, _ in group:
                if spot.get("note"):
                    out.append(f"<div class='note'><b>{spot['name']}:</b> "
                               f"{spot['note']}</div>")

    out.append("<div class='legend'>Twee bolletjes per dag = twee onafhankelijke "
               "weermodellen. Twee keer groen betekent dat ze het eens zijn. "
               "Groen naast rood betekent: nog onzeker, morgen opnieuw kijken.<br>"
               "Dag 1-2 draaien op HARMONIE (KNMI, 2 km) en ICON-D2 (2 km). "
               "Verder vooruit op ECMWF en ICON global, die grover zijn.<br>"
               "&Delta; is het verschil tussen gemiddelde wind en de vlagen."
               "</div>")
    return "\n".join(out)


def summarise(tables: dict, days: list) -> tuple[str, str]:
    """Korte tekstsamenvatting voor in de mail."""
    hits = []
    for rider_key, spot_tables in tables.items():
        for spot, rows in spot_tables:
            if RIDERS[rider_key]["needs_shallow"] and not spot["shallow"]:
                continue
            for row in rows:
                greens = [r for r in row["results"] if r["verdict"] == GREEN]
                if len(greens) == len(row["results"]) and greens:
                    d = row["day"]
                    a, b = greens[0]["window"]
                    hits.append((RIDERS[rider_key]["label"], spot["name"],
                                 f"{DAYNAMES[d.weekday()]} {d.day}", f"{a}-{b}u"))

    if not hits:
        return "Kitecheck: niks groens deze week", \
               "Geen enkele dag waarop beide modellen het eens zijn.\n"

    lines = [f"{r} - {s}, {d} {w}" for r, s, d, w in hits]
    subject = f"Kitecheck: {len(hits)} kans" + ("en" if len(hits) > 1 else "")
    return subject, "\n".join(lines) + "\n"


def send_mail(subject: str, body: str, url: str | None) -> None:
    host = os.environ.get("SMTP_HOST")
    if not host:
        print("Geen SMTP_HOST -- mail overgeslagen.")
        return
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg["Subject"] = subject
    full = body + (f"\nHele tabel: {url}\n" if url else "")
    msg.set_content(full)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls(context=ctx)
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)
    print(f"Mail verstuurd naar {os.environ['MAIL_TO']}")


# ---------------------------------------------------------------------------

def main() -> int:
    stamp = datetime.now().strftime("%d-%m-%Y %H:%M")
    tables: dict = {k: [] for k in RIDERS}
    all_days: set = set()
    cache: dict = {}

    for spot in SPOTS:
        try:
            data = fetch(spot)
        except Exception as exc:                      # noqa: BLE001
            print(f"FOUT bij {spot['name']}: {exc}", file=sys.stderr)
            continue
        available = {m: regroup(data, m) for m in ALL_MODELS}
        available = {m: d for m, d in available.items() if d}
        if not available:
            print(f"Geen data voor {spot['name']}", file=sys.stderr)
            continue
        cache[spot["name"]] = available
        for d in available.values():
            all_days.update(d.keys())

    if not cache:
        print("Geen enkele spot opgehaald.", file=sys.stderr)
        return 1

    days = sorted(all_days)[:FORECAST_DAYS]

    for rider_key, rider in RIDERS.items():
        for spot in SPOTS:
            if spot["name"] not in cache:
                continue
            rows = build_rows(spot, rider, cache[spot["name"]], days)
            tables[rider_key].append((spot, rows))

    html = render_html(tables, days, stamp)
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as fh:
        fh.write(html)
    print("docs/index.html geschreven")

    subject, body = summarise(tables, days)
    send_mail(subject, body, os.environ.get("PAGES_URL"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
