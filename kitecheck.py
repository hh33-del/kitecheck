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

# Buitengrenzen; de echte grens per dag is zonsopkomst/zonsondergang.
DAY_START, DAY_END = 8, 22
# Laatste uur voor zonsondergang niet meer aanbieden: je wil niet in het
# donker je kite nog uit het water halen.
SUNSET_MARGIN_H = 1

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
        "daily": "sunrise,sunset",
        "wind_speed_unit": "kn",
        "timezone": "Europe/Amsterdam",
        "forecast_days": FORECAST_DAYS,
        "models": ",".join(ALL_MODELS),
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=45) as resp:
        return json.loads(resp.read().decode())


MARINE_API = "https://marine-api.open-meteo.com/v1/marine"
WF = "https://nl.windfinder.com/weatherforecast/"
PHOTO = "foto.jpg"


def fetch_waves(spot: dict) -> dict:
    """{datum: {uur: golfhoogte in m}} voor zeespots; leeg voor binnenwater."""
    if "sea" not in spot:
        return {}
    lat, lon = spot["sea"]
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "wave_height",
        "timezone": "Europe/Amsterdam",
        "forecast_days": FORECAST_DAYS,
    }
    url = f"{MARINE_API}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=45) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:                              # noqa: BLE001
        print(f"Golfdata mislukt voor {spot['name']}: {exc}", file=sys.stderr)
        return {}

    hourly = data.get("hourly", {})
    out: dict = defaultdict(dict)
    for t, h in zip(hourly.get("time", []), hourly.get("wave_height", [])):
        stamp = datetime.fromisoformat(t)
        if h is not None:
            out[stamp.date()][stamp.hour] = h
    return dict(out)


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


def score_model_day(hours: list, spot: dict, rider: dict,
                    daylight: tuple | None = None) -> dict:
    """hours = lijst van dicts voor een enkele dag, enkel model."""
    lo_h, hi_h = DAY_START, DAY_END
    if daylight:
        lo_h = max(lo_h, daylight[0])
        hi_h = min(hi_h, daylight[1] - SUNSET_MARGIN_H)

    graded = []
    for h in hours:
        if not (lo_h <= h["hour"] <= hi_h):
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


def daylight_map(data: dict) -> dict:
    """{datum: (eerste bruikbare uur, laatste bruikbare uur)} uit sunrise/sunset."""
    daily = data.get("daily", {})
    days = daily.get("time", [])
    if not days:
        for key in daily:
            if key.startswith("time_"):
                days = daily[key]
                break
    rises = next((v for k, v in daily.items() if k.startswith("sunrise")), [])
    sets_ = next((v for k, v in daily.items() if k.startswith("sunset")), [])

    out = {}
    for i, d in enumerate(days):
        if i >= len(rises) or i >= len(sets_):
            continue
        try:
            r = datetime.fromisoformat(rises[i])
            s_ = datetime.fromisoformat(sets_[i])
        except (TypeError, ValueError):
            continue
        out[r.date()] = (r.hour + 1, s_.hour)
    return out


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


def build_rows(spot: dict, rider: dict, available: dict, days: list,
               daylight: dict) -> list:
    rows = []
    for day in days:
        chosen = models_for_day(available, day)
        results = []
        for m in chosen:
            hours = available[m][day]
            r = score_model_day(hours, spot, rider, daylight.get(day))
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
                    texts = [cell_text(r) or "&mdash;" for r in row["results"]]
                    body = "" if all(t == "&mdash;" for t in texts) \
                        else "<br>".join(texts)
                    out.append(f"<td><span class='dots'>{dots}</span>"
                               f"<div class='cell'>{body}</div></td>")
                out.append("</tr>")
            out.append("</table>")
            for spot, _ in group:
                if rider["needs_shallow"] and not spot["shallow"]:
                    continue
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



# ---------------------------------------------------------------------------
# Mail: tabel per dag, spots als rijen, dagdelen als kolommen
# ---------------------------------------------------------------------------

PARTS = [("ochtend", 9, 12), ("vroege middag", 12, 14),
         ("late middag", 14, 17), ("avond", 17, 20)]
PART_SHORT = ["Ocht", "Vr mid", "Lt mid", "Avond"]

# Kleuren die zowel op wit als op zwart leesbaar zijn.
CLR = {
    "green":  ("#1b7f4b", "#ffffff"),
    "yellow": ("#b8860b", "#ffffff"),
    "orange": ("#b4531f", "#ffffff"),
    "none":   ("#9b9b94", "#ffffff"),
    "dark":   ("#3a3a38", "#8a8a84"),
}


def part_wave(waves, spot, day, part):
    """Hoogste golf binnen dit dagdeel, of None voor binnenwater."""
    hours = waves.get(spot["name"], {}).get(day, {})
    vals = [v for h, v in hours.items() if part[1] <= h < part[2]]
    return max(vals) if vals else None


def part_grade(spot, rider, available, day, daylight, part):
    """Beoordeel een dagdeel: green / yellow / orange / none / dark."""
    lo_h, hi_h = part[1], part[2]
    dl = daylight.get(day)
    if dl:
        lo_h = max(lo_h, dl[0])
        hi_h = min(hi_h, dl[1] - SUNSET_MARGIN_H)
    if lo_h >= hi_h:
        return "dark", None          # buiten daglicht

    loose = dict(rider, max_delta=999)
    out = []
    for m in models_for_day(available, day):
        hours = [h for h in available[m][day] if lo_h <= h["hour"] < hi_h]
        res = score_model_day(hours, spot, loose, None)
        if res["verdict"] == GREEN and res["window"]:
            d = res["delta"]
            g = ("green" if d <= rider["max_delta"]
                 else "yellow" if d <= rider["max_delta"] + 4 else "orange")
            out.append((g, res))
        else:
            out.append(("none", None))
    if not out:
        return ["none", "none"], None
    grades = [g for g, _ in out]
    # toon de getallen van het voorzichtigste model dat nog wind ziet
    scored = [r for g, r in out if r is not None]
    worst = max(scored, key=lambda r: r["delta"]) if scored else None
    return grades, worst


ORDER = ["green", "yellow", "orange", "none", "dark"]


def cell_html(grades, res, wave=None):
    if isinstance(grades, str):
        grades = [grades]
    uniq = [g for g in grades if g]
    worst = max(uniq, key=lambda g: ORDER.index(g)) if uniq else "none"
    bg, fg = CLR[worst]
    style = f"background:{bg};"
    if len(set(uniq)) > 1:
        a = CLR[uniq[0]][0]
        b = CLR[uniq[1]][0]
        style += (f"background-image:linear-gradient(135deg,{a} 0%,{a} 50%,"
                  f"{b} 50%,{b} 100%);")
    grade = worst
    if grade in ("none", "dark"):
        txt = "&middot;"
    else:
        lo, hi = res["speed"]
        kn = f"{lo}" if lo == hi else f"{lo}-{hi}"
        txt = f"{kn}<br><span style='font-size:11px'>&Delta;{res['delta']}</span>"
        if wave is not None:
            txt += (f"<br><span style='font-size:11px'>"
                    f"{wave:.1f}m</span>")
    return (f"<td style='{style}color:{fg};padding:7px 4px;"
            f"text-align:center;font-size:13px;line-height:1.25;"
            f"border:1px solid #00000022'>{txt}</td>")


def build_mail(cache, light, waves, days, rider):
    main = [s for s in SPOTS if s.get("rank", 9) < 9 and s["name"] in cache]
    back = [s for s in SPOTS if s.get("rank", 9) >= 9 and s["name"] in cache]
    main.sort(key=lambda s: s["rank"])

    html = ["<div style='font-family:-apple-system,Segoe UI,sans-serif;"
            "max-width:520px;margin:0 auto;padding:12px'>"]
    text, kansen = [], []

    for day in days:
        label = f"{DAYNAMES[day.weekday()]} {day.day}"
        rows, hit = [], False
        for spot in main:
            cells = []
            for part in PARTS:
                g, r = part_grade(spot, rider, cache[spot["name"]], day,
                                  light.get(spot["name"], {}), part)
                if any(x in ("green", "yellow") for x in g):
                    hit = True
                cells.append(cell_html(g, r, part_wave(waves, spot, day, part)))
            rows.append((spot, cells))

        if not hit:                      # pas dan Brouwersdam erbij
            for spot in back:
                cells = []
                for part in PARTS:
                    g, r = part_grade(spot, rider, cache[spot["name"]], day,
                                      light.get(spot["name"], {}), part)
                    cells.append(cell_html(g, r,
                                           part_wave(waves, spot, day, part)))
                rows.append((spot, cells))

        if hit:
            kansen.append(label)

        html.append(f"<div style='font-weight:600;font-size:15px;"
                    f"margin:16px 0 4px'>{label}</div>")
        html.append("<table style='border-collapse:collapse;width:100%'>")
        html.append("<tr><td style='width:34%'></td>" + "".join(
            f"<td style='text-align:center;font-size:11px;padding:2px;"
            f"color:#8a8a84'>{p}</td>" for p in PART_SHORT) + "</tr>")
        for spot, cells in rows:
            link = f"{WF}{spot.get('wf','')}"
            html.append(f"<tr><td style='font-size:13px;padding:7px 8px;"
                        f"background:#2c2c2a;border:1px solid #00000022'>"
                        f"<a href='{link}' style='color:#ffffff;"
                        f"text-decoration:none'>{spot['name']}</a></td>"
                        + "".join(cells) + "</tr>")
        html.append("</table>")

        text.append(f"{label}: " + ", ".join(
            f"{s['name']}" for s, c in rows
            if "1b7f4b" in "".join(c) or "b8860b" in "".join(c)) or
            f"{label}: niets")

    kop = ("Kans op " + ", ".join(kansen)) if kansen else "Deze week geen wind"
    html.insert(1, f"<div style='font-size:17px;font-weight:600;"
                   f"margin-bottom:2px'>{kop}</div>"
                   f"<div style='font-size:12px;color:#8a8a84'>"
                   f"groen rustig &middot; geel vlagerig &middot; "
                   f"oranje erg vlagerig</div>")
    if os.path.exists(PHOTO):
        html.append("<div style='margin-top:22px;text-align:center'>"
                    "<img src='cid:kitefoto' style='max-width:100%;"
                    "border-radius:8px' alt=''></div>")
    html.append("</div>")
    return kop, "\n".join(text), "\n".join(html)


def wind_windows(spot: dict, rider: dict, available: dict, day,
                 daylight: dict) -> list:
    """Vensters met genoeg wind, ongeacht de vlagen. Delta komt mee als info."""
    loose = dict(rider, max_delta=999)
    found = []
    for m in models_for_day(available, day):
        res = score_model_day(available[m][day], spot, loose, daylight.get(day))
        if res["verdict"] == GREEN and res["window"]:
            found.append(res)
    return found


def gust_mark(delta: int, rider: dict) -> str:
    if delta <= rider["max_delta"]:
        return "rustig"
    if delta <= rider["max_delta"] + 4:
        return "schokkerig"
    return "erg vlagerig"


def wave_note(waves: dict, day, window) -> str:
    hours = waves.get(day, {})
    vals = [v for h, v in hours.items() if window[0] <= h < window[1]]
    if not vals:
        return ""
    return f", golven tot {max(vals):.1f} m"


def send_mail(subject: str, text: str, html: str) -> None:
    host = os.environ.get("SMTP_HOST")
    if not host:
        print("Geen SMTP_HOST -- mail overgeslagen.")
        return
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(
        "<!doctype html><html><head><meta name='color-scheme' "
        "content='light dark'><meta name='supported-color-schemes' "
        "content='light dark'></head><body style='margin:0'>"
        + html + "</body></html>", subtype="html")

    if os.path.exists(PHOTO):
        with open(PHOTO, "rb") as fh:
            msg.get_payload()[1].add_related(
                fh.read(), maintype="image", subtype="jpeg", cid="kitefoto")

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls(context=ctx)
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)
    print(f"Mail verstuurd naar {os.environ['MAIL_TO']}")


def main() -> int:
    stamp = datetime.now().strftime("%d-%m-%Y %H:%M")
    tables: dict = {k: [] for k in RIDERS}
    all_days: set = set()
    cache: dict = {}
    light: dict = {}
    waves: dict = {}

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
        light[spot["name"]] = daylight_map(data)
        waves[spot["name"]] = fetch_waves(spot)
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
            rows = build_rows(spot, rider, cache[spot["name"]], days,
                              light.get(spot["name"], {}))
            tables[rider_key].append((spot, rows))

    html = render_html(tables, days, stamp)
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as fh:
        fh.write(html)
    print("docs/index.html geschreven")

    rider = list(RIDERS.values())[0]
    kop, text, html = build_mail(cache, light, waves, days, rider)
    print(text)
    send_mail(f"Kitecheck -- {kop}", text, html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
