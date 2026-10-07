#!/usr/bin/env python3
"""Kitecheck -- dagelijkse mail met waar het de komende 7 dagen kan."""

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
MARINE_API = "https://marine-api.open-meteo.com/v1/marine"
WF = "https://nl.windfinder.com/weatherforecast/"

MODELS_NEAR = ["knmi_harmonie_arome_netherlands", "icon_d2"]
MODELS_FAR = ["ecmwf_ifs025", "icon_global"]
ALL_MODELS = MODELS_NEAR + MODELS_FAR

DAY_START, DAY_END = 8, 22
SUNSET_MARGIN_H = 1

FORECAST_DAYS = 7
THUNDER_CODES = {95, 96, 99}

GREEN, AMBER, RED = "green", "amber", "red"
RANK = {RED: 0, AMBER: 1, GREEN: 2}
DOT = {GREEN: "\U0001F7E2", AMBER: "\U0001F7E1", RED: "\U0001F534"}
DAYNAMES = ["ma", "di", "wo", "do", "vr", "za", "zo"]


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


def fetch_waves(spot: dict) -> dict:
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
    except Exception as exc:
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
    for key in (f"{field}_{model}", field):
        if key in hourly:
            return hourly[key]
    return []


def dir_ok(deg: float, sectors: list) -> bool:
    if deg is None:
        return False
    for lo, hi in sectors:
        if lo <= hi:
            if lo <= deg <= hi:
                return True
        else:
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
    if (lo - 2) <= speed <= (hi + 3) and delta <= (max_delta + 3) and rain < 2.5:
        return AMBER
    return RED


def score_model_day(hours: list, spot: dict, rider: dict,
                    daylight: tuple | None = None) -> dict:
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
    return {d: hs for d, hs in days.items()
            if any(h["speed"] is not None for h in hs)}


def daylight_map(data: dict) -> dict:
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
    near = [m for m in MODELS_NEAR if day in available.get(m, {})]
    far = [m for m in MODELS_FAR if day in available.get(m, {})]
    return (near + far)[:2]


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
        results = []
        for m in models_for_day(available, day):
            r = score_model_day(available[m][day], spot, rider,
                                daylight.get(day))
            r["model"] = m
            results.append(r)
        rows.append({"day": day, "results": results})
    return rows


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
        out.append("<table><tr><th>Spot</th>")
        for d in days:
            out.append(f"<th>{DAYNAMES[d.weekday()]} {d.day}</th>")
        out.append("</tr>")

        for spot, rows in spot_tables:
            out.append(f"<tr><td class='spot'>{spot['name']}</td>")
            for row in rows:
                dots = "".join(DOT[r["verdict"]] for r in row["results"]) or "&mdash;"
                texts = [cell_text(r) or "&mdash;" for r in row["results"]]
                body = "" if all(t == "&mdash;" for t in texts) \
                    else "<br>".join(texts)
                out.append(f"<td><span class='dots'>{dots}</span>"
                           f"<div class='cell'>{body}</div></td>")
            out.append("</tr>")
        out.append("</table>")
        for spot, _ in spot_tables:
            if spot.get("note"):
                out.append(f"<div class='note'><b>{spot['name']}:</b> "
                           f"{spot['note']}</div>")

    out.append("<div class='legend'>Twee bolletjes per dag = twee modellen. "
               "Dag 1-2 draaien op HARMONIE (KNMI, 2 km) en ICON-D2 (2 km), "
               "daarna op ECMWF en ICON global, die grover zijn.<br>"
               "Tijden lopen tot een uur voor zonsondergang.<br>"
               "&Delta; is het verschil tussen gemiddelde wind en de vlagen."
               "</div>")
    return "\n".join(out)


def wind_windows(spot: dict, rider: dict, available: dict, day,
                 daylight: dict) -> list:
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


def digest(cache: dict, light: dict, waves: dict, days: list) -> tuple[str, str]:
    blocks, headline = [], []

    for rider_key, rider in RIDERS.items():
        lines, first = [], None
        for day in days:
            label = f"{DAYNAMES[day.weekday()]} {day.day}"
            entries = []
            for spot in SPOTS:
                if spot["name"] not in cache:
                    continue
                if rider["needs_shallow"] and not spot["shallow"]:
                    continue
                found = wind_windows(spot, rider, cache[spot["name"]], day,
                                     light.get(spot["name"], {}))
                if not found:
                    continue
                agree = "beide modellen" if len(found) > 1 else "1 van 2 modellen"
                best = min(found, key=lambda r: r["delta"])
                a, b = best["window"]
                lo, hi = best["speed"]
                kn = f"{lo}" if lo == hi else f"{lo}-{hi}"
                entries.append(
                    f"    {spot['name']} {a}-{b}u, {kn} kn, "
                    f"delta {best['delta']} ({gust_mark(best['delta'], rider)})"
                    f"{wave_note(waves.get(spot['name'], {}), day, best['window'])}"
                    f" [{agree}]\n      {WF}{spot.get('wf', '')}")
                if first is None:
                    first = (label, spot["name"], f"{a}-{b}u", kn,
                             best["delta"], rider)
            lines.append(f"  {label}:" + ("" if entries else " geen wind"))
            lines.extend(entries)

        if first:
            lab, sp, win, kn, dl, rd = first
            headline.append(f"{rd['label']}: {lab} {sp} {win} ({kn} kn, "
                            f"{gust_mark(dl, rd)})")
        else:
            headline.append(f"{rider['label']}: niets met genoeg wind")

        blocks.append(f"{rider['label']} ({rider['min_kn']}-{rider['max_kn']} kn)\n"
                      + "\n".join(lines))

    subject = "Kitecheck -- " + " | ".join(headline)
    body = "KOMENDE 7 DAGEN\n" + "\n".join(headline) + "\n\n" + \
           "\n\n".join(blocks) + "\n"
    return subject[:150], body


def send_mail(subject: str, body: str, url: str | None) -> None:
    host = os.environ.get("SMTP_HOST")
    if not host:
        print("Geen SMTP_HOST -- mail overgeslagen.")
        return
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg["Subject"] = subject
    msg.set_content(body + (f"\nHele tabel: {url}\n" if url else ""))

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
        except Exception as exc:
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
            tables[rider_key].append(
                (spot, build_rows(spot, rider, cache[spot["name"]], days,
                                  light.get(spot["name"], {}))))

    html = render_html(tables, days, stamp)
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as fh:
        fh.write(html)
    print("docs/index.html geschreven")

    subject, body = digest(cache, light, waves, days)
    print(body)
    send_mail(subject, body, os.environ.get("PAGES_URL"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
