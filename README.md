# Kitecheck

Draait twee keer per dag, kijkt per spot of het die week kan, en mailt je
alleen de dagen waarop twee onafhankelijke weermodellen het eens zijn.
De volledige tabel staat op een eigen webpagina die je kunt bookmarken en
delen.

Geen API-key nodig. Geen dependencies buiten de Python-standaardbibliotheek.

## Opzetten (ongeveer een half uur)

**1. Repo aanmaken.** Maak op GitHub een nieuwe repo en zet deze bestanden
erin. Maak hem **publiek** — dan zijn de Actions-minuten onbeperkt en, wat
belangrijker is, worden geplande workflows niet na 60 dagen inactiviteit
uitgezet. Er staat niets gevoeligs in; wachtwoorden gaan in Secrets.

**2. Testen.** Ga naar het tabblad Actions, kies "Kitecheck" en klik
"Run workflow". Kijk of hij groen wordt en of `docs/index.html` verschijnt.

**3. Pagina aanzetten.** Settings → Pages → Source: "Deploy from a branch",
branch `main`, map `/docs`. Na een minuut staat je tabel op
`https://<jouwnaam>.github.io/<repo>/`. Die link geef je aan je vriendin.

**4. Mail instellen.** Settings → Secrets and variables → Actions:

| Secret | Waarde |
|---|---|
| `SMTP_HOST` | bv. `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | je mailadres |
| `SMTP_PASS` | een app-wachtwoord, niet je gewone wachtwoord |
| `MAIL_TO` | ontvangers, komma-gescheiden |

Onder het tabblad Variables zet je `PAGES_URL` op de link uit stap 3.

Gmail vereist een app-wachtwoord met tweestapsverificatie aan. Wil je dat
liever niet, gebruik dan een gratis SMTP-dienst zoals Resend of Brevo.

## Aanpassen

Alles wat je normaal wilt wijzigen staat in `spots.py`: je windbereik, de
maximale delta tussen gemiddelde wind en vlagen, en per spot de
windrichtingen die werken.

De regels met `# CHECK` zijn windrichtingen en coördinaten die ik uit
spotgidsen heb overgenomen en die je zelf moet verifiëren. Vooral Muiderberg
en Schellinkhout: die heb ik afgeleid uit de ligging van het strand, niet uit
ervaring.

## Hoe de score werkt

Per uur tussen 09:00 en 21:00:

- windrichting binnen een sector die voor die spot werkt
- gemiddelde wind binnen jouw bereik
- vlagen min gemiddelde onder je maximale delta
- minder dan 1 mm regen per uur, geen onweer

Alles goed is groen. Net buiten de marges is geel. De rest is rood. Per dag
zie je het langste aaneengesloten blok op het beste niveau.

Twee bolletjes per dag zijn twee modellen. De eerste twee dagen zijn dat
HARMONIE (KNMI, 2 km) en ICON-D2 (2 km). Daarna reiken die niet verder en
schuift het script door naar ECMWF en ICON global, die grover zijn — dus
neem donderdag en vrijdag met meer korrels zout dan morgen.

## Wat er nog niet in zit

**Getijden.** De velden staan klaar in `spots.py`, maar de koppeling met
Rijkswaterstaat heb ik niet af gekregen zonder hem te kunnen testen. Voor de
spots die je nu het meest gebruikt maakt dat niet uit: Strand Horst,
Muiderberg, Schellinkhout en de Grevelingenkant van de Brouwersdam hebben
geen noemenswaardig getij. Het speelt alleen bij de Zandmotor, Wijk aan Zee
en de zeezijde van de Brouwersdam.

Ik heb bewust geen halve oplossing ingebouwd. Een verkeerd laagwatertijdstip
bij de Brouwersdam is gevaarlijker dan geen tijdstip.

**Reistijd in de spits.** Staat als vast getal in `spots.py`, geen live
verkeersdata.
