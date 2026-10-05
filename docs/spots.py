"""
Spotprofielen en riderprofielen.

DIT IS HET ENIGE BESTAND DAT JE NORMAAL AANPAST.
"""

RIDERS = {
    "hester": {
        "label": "Hester",
        "min_kn": 15,
        "max_kn": 22,
        "max_delta": 10,
        "needs_shallow": True,
    },
    "vriendin": {
        "label": "Marina",
        "min_kn": 12,          # CHECK -- gok, pas aan
        "max_kn": 28,          # CHECK
        "max_delta": 14,       # CHECK
        "needs_shallow": False,
    },
}

# dirs      : (van_graden, tot_graden); mag over 0 heen lopen, bv (315, 45)
# shallow   : True = je kunt er staan
# tide      : None, of naam van een Rijkswaterstaat-getijdestation
# drive_min : reistijd vanuit Utrecht in minuten, buiten de spits
# primary   : True = h
