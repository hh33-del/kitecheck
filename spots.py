"""
Spotprofielen en riderprofielen.

DIT IS HET ENIGE BESTAND DAT JE NORMAAL AANPAST.

Let op de regels met  # CHECK  -- die windrichtingen heb ik uit spotgidsen
gehaald en niet zelf op het water geverifieerd. Corrigeer ze na je eerste
paar sessies; de rest van het script hoef je dan niet aan te raken.
"""

# ---------------------------------------------------------------------------
# Riderprofielen
# ---------------------------------------------------------------------------
# min_kn / max_kn : bruikbaar windbereik in knopen
# max_delta       : maximaal verschil tussen gemiddelde wind en windvlagen
# needs_shallow   : True = alleen spots waar je kunt staan
# ---------------------------------------------------------------------------

RIDERS = {
    "hester": {
        "label": "Hester",
        "min_kn": 15,
        "max_kn": 22,
        "max_delta": 10,
        "needs_shallow": True,
    },
    "vriendin": {
        "label": "Vriendin",
        "min_kn": 12,          # CHECK -- gok, pas aan
        "max_kn": 28,          # CHECK
        "max_delta": 14,       # CHECK
        "needs_shallow": False,
    },
}

# ---------------------------------------------------------------------------
# Spots
# ---------------------------------------------------------------------------
# dirs      : lijst van (van_graden, tot_graden) sectoren die werken.
#             Sectoren mogen over 0 heen lopen, bv (315, 45) = NW via N naar NO.
# shallow   : True = je kunt er staan
# tide      : None, of de naam van een Rijkswaterstaat-getijdestation
# drive_min : reistijd vanuit Utrecht in minuten, buiten de spits
# primary   : True = staat in de hoofdtabel, False = in de sectie eronder
# ---------------------------------------------------------------------------

SPOTS = [
    {
        "name": "Strand Horst",
        "lat": 52.3103,
        "lon": 5.5593,
        "dirs": [(200, 50)],              # CHECK -- ZW/W/NW/N/NO, beste met noord erin
        "shallow": True,
        "tide": None,
        "drive_min": 40,
        "primary": True,
        "note": "Vlagerig door bebouwing; met noord in de wind het rustigst.",
    },
    {
        "name": "Muiderberg",
        "lat": 52.3258,
        "lon": 5.1208,
        "dirs": [(315, 90)],              # NW via N en NO naar O -- opgegeven door Hester
        "shallow": True,
        "tide": None,
        "drive_min": 35,
        "primary": True,
        "note": "Groot ondiep vlak deel, veel lesgebied.",
    },
    {
        "name": "Schellinkhout",
        "lat": 52.6180,
        "lon": 5.1250,                    # CHECK -- coordinaat bij benadering
        "dirs": [(135, 270)],             # ZO via Z en ZW naar W -- opgegeven door Hester
        "shallow": True,
        "tide": None,
        "drive_min": 60,
        "primary": True,
        "note": "Ondiep Markermeer, gunstig bij noordoost tot noord.",
    },
    {
        "name": "Zandmotor (Kijkduin)",
        "lat": 52.0533,
        "lon": 4.1867,
        "dirs": [(180, 360)],             # CHECK -- Z via W naar N, aanlandig bij W
        "shallow": True,
        "tide": "SCHEVENINGEN",
        "drive_min": 55,
        "primary": True,
        "note": "Zee, maar ondiepe lagune bij de Zandmotor.",
    },
    {
        "name": "Brouwersdam meerzijde",
        "lat": 51.7580,
        "lon": 3.8560,                    # CHECK -- coordinaat bij benadering
        "dirs": [(200, 290)],             # ZW tot W, zone 1 Grevelingen
        "shallow": True,
        "tide": None,                     # Grevelingen: verwaarloosbaar getij
        "drive_min": 105,
        "primary": True,
        "note": "Vlakwater, ondiep tot ~100 m. Geen getij aan deze kant.",
    },
    # --- hieronder: kan wel, maar niet staandiep of technischer ---------------
    {
        "name": "Wijk aan Zee",
        "lat": 52.4700,
        "lon": 4.5700,
        "dirs": [(200, 360)],             # ZW, W, NW, N
        "shallow": False,
        "tide": "IJMUIDEN",
        "drive_min": 50,
        "primary": False,
        "note": "Water >2 m, staan kan niet: waterstart vereist. Alleen Zone 2.",
    },
    {
        "name": "Brouwersdam zeezijde",
        "lat": 51.7602,
        "lon": 3.8471,
        "dirs": [(300, 250)],             # N/NW/W/ZW
        "shallow": True,
        "tide": "BROUWERSHAVENSCHE GAT 08",
        "drive_min": 105,
        "primary": False,
        "note": "Alleen rond laagwater ondiep. Stroming bij opkomend water; "
                "niet alleen kiten in verband met de uitwateringssluis.",
    },
]
