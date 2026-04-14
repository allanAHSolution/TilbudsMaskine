"""
Dinero API-klient.

Loader credentials fra dinero_config.json (gitignored). Håndterer OAuth-token
(grant_type=password med api_key som username+password), slår organisations-id
op første gang, og tilbyder hjælpefunktioner til fakturaer og kontakter.

Dokumentation: https://api.dinero.dk/docs/
"""

import base64
import json
import os
import time
from urllib.parse import urlencode, quote

import requests

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE    = os.path.join(BASE_DIR, 'dinero_config.json')
CACHE_FILE     = os.path.join(BASE_DIR, 'dinero_cache.json')

AUTH_URL       = "https://authz.dinero.dk/dineroapi/oauth/token"
API_BASE       = "https://api.dinero.dk/v1"

_token_cache   = {"access_token": None, "expires_at": 0}


# ────────── CONFIG ──────────
def _load_config():
    if not os.path.exists(CONFIG_FILE):
        raise RuntimeError(
            f"Dinero config mangler: {CONFIG_FILE}. "
            f"Opret filen med client_id, client_secret og api_key."
        )
    with open(CONFIG_FILE) as f:
        return json.load(f)


def _save_config(cfg):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ────────── AUTH ──────────
def _get_token():
    """Henter (og cacher) et OAuth access-token. Fornyes automatisk."""
    global _token_cache
    now = time.time()
    if _token_cache["access_token"] and _token_cache["expires_at"] > now + 30:
        return _token_cache["access_token"]

    cfg = _load_config()
    basic = base64.b64encode(
        f"{cfg['client_id']}:{cfg['client_secret']}".encode()
    ).decode()
    res = requests.post(
        AUTH_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "password",
            "scope":      "read write",
            "username":   cfg["api_key"],
            "password":   cfg["api_key"],
        },
        timeout=15,
    )
    res.raise_for_status()
    j = res.json()
    _token_cache["access_token"] = j["access_token"]
    _token_cache["expires_at"]   = now + int(j.get("expires_in", 3600))
    return _token_cache["access_token"]


def _headers():
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }


def _org_id():
    """Slår organisations-id op (cached i config)."""
    cfg = _load_config()
    if cfg.get("organization_id"):
        return cfg["organization_id"]
    res = requests.get(f"{API_BASE}/organizations", headers=_headers(), timeout=15)
    res.raise_for_status()
    orgs = res.json()
    if not orgs:
        raise RuntimeError("Ingen organisationer fundet på Dinero-kontoen.")
    # API returnerer array af dicts med nøgler 'id', 'name', 'isPro'
    org_id = orgs[0].get("id") or orgs[0].get("Id")
    cfg["organization_id"] = org_id
    _save_config(cfg)
    return org_id


def test_forbindelse():
    """Returnerer (ok, besked). Bruges til at tjekke at credentials virker."""
    try:
        oid = _org_id()
        return True, f"Forbundet til organisation {oid}"
    except Exception as e:
        return False, str(e)


# ────────── KONTAKTER ──────────
def hent_kontakter(sog="", limit=100):
    """
    Returnerer liste af kontakter (navn, nummer, email, id).
    sog: valgfri fritekstsøgning (filtreres lokalt).
    """
    oid = _org_id()
    params = {"pageSize": max(limit, 200), "page": 0}
    res = requests.get(
        f"{API_BASE}/{oid}/contacts?{urlencode(params)}",
        headers=_headers(), timeout=15,
    )
    res.raise_for_status()
    coll = res.json().get("Collection", [])
    sog_l = sog.lower().strip()
    ud = []
    for c in coll:
        navn = c.get("name") or c.get("Name") or ""
        if sog_l and sog_l not in navn.lower():
            continue
        ud.append({
            "id":     c.get("contactGuid") or c.get("ContactGuid"),
            "navn":   navn,
            "nummer": c.get("contactNumber") or c.get("ContactNumber"),
            "email":  c.get("email") or c.get("Email") or "",
        })
        if len(ud) >= limit:
            break
    return ud


def find_eller_opret_kontakt(navn, email=""):
    """
    Søger efter en kontakt med samme navn (case-insensitive).
    Opretter en ny hvis ikke fundet. Returnerer ContactGuid.
    """
    if not navn:
        raise ValueError("Kundenavn mangler")
    navn_l = navn.lower().strip()
    for k in hent_kontakter(limit=500):
        if k["navn"].lower().strip() == navn_l:
            return k["id"]

    oid = _org_id()
    payload = {
        "Name":       navn,
        "CountryKey": "DK",
        "IsPerson":   False,
        "Email":      email or None,
    }
    res = requests.post(
        f"{API_BASE}/{oid}/contacts", headers=_headers(),
        data=json.dumps(payload), timeout=15,
    )
    if not res.ok:
        raise RuntimeError(f"Kunne ikke oprette kontakt ({res.status_code}): {res.text[:400]}")
    j = res.json()
    return j.get("ContactGuid") or j.get("contactGuid")


# ────────── FAKTURAER ──────────
def opret_faktura(kunde_navn, linjer, dato=None, valuta="DKK",
                  kommentar="", kontakt_email=""):
    """
    Opretter en faktura-kladde i Dinero.

    linjer: liste af dicts med keys: beskrivelse, antal, enhedspris (DKK).
    Returnerer (guid, timestamp) — guid bruges til senere opslag/bogføring.
    """
    oid = _org_id()
    contact_id = find_eller_opret_kontakt(kunde_navn, kontakt_email)

    product_lines = []
    for l in linjer:
        product_lines.append({
            "ProductGuid":       None,
            "Description":       l.get("beskrivelse", ""),
            "Quantity":          float(l.get("antal", 1) or 1),
            "AccountNumber":     1000,  # Varesalg - tilpas om nødvendigt
            "Unit":              "parts",
            "BaseAmountValue":   float(l.get("enhedspris", 0) or 0),
            "LineType":          "Product",
        })

    payload = {
        "Currency":         valuta,
        "Language":         "da-DK",
        "ExternalReference": kommentar,
        "Description":      kommentar,
        "Comment":          kommentar,
        "Date":             dato or time.strftime("%Y-%m-%d"),
        "ContactGuid":      contact_id,
        "ShowLinesInclVat": False,
        "ProductLines":     product_lines,
        "PaymentConditionType":            "NetDays",
        "PaymentConditionNumberOfDays":    14,
    }

    res = requests.post(
        f"{API_BASE}/{oid}/invoices", headers=_headers(),
        data=json.dumps(payload), timeout=20,
    )
    if not res.ok:
        raise RuntimeError(f"Dinero fejlede ({res.status_code}): {res.text[:400]}")
    j = res.json()
    return j.get("Guid"), j.get("TimeStamp")


def hent_faktura_status(guid, timestamp=None):
    """
    Henter aktuel status for en faktura. Returnerer dict med
    PaymentStatus, TotalInclVat, PaymentDate osv.
    """
    oid = _org_id()
    url = f"{API_BASE}/{oid}/invoices/{guid}"
    res = requests.get(url, headers=_headers(), timeout=15)
    res.raise_for_status()
    return res.json()


def bogfør_faktura(guid, timestamp):
    """Bogfører en faktura-kladde. Timestamp er den ETag-value fra opret/hent."""
    oid = _org_id()
    url = f"{API_BASE}/{oid}/invoices/{quote(guid)}/book"
    res = requests.post(url, headers=_headers(),
                        data=json.dumps({"Timestamp": timestamp}), timeout=20)
    if not res.ok:
        raise RuntimeError(f"Bogføring fejlede ({res.status_code}): {res.text[:400]}")
    return res.json()
