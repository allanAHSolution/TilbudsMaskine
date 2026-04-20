"""
Dinero API-klient.

Loader credentials fra dinero_config.json (gitignored). Håndterer OAuth-token
(grant_type=password med api_key som username+password), slår organisations-id
op første gang, og tilbyder hjælpefunktioner til fakturaer og kontakter.

Dokumentation: https://api.dinero.dk/docs/
"""

import base64
import json
import logging
import os
import re
import time
from typing import Optional
from urllib.parse import urlencode, quote

import requests

log = logging.getLogger(__name__)

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
def slet_faktura(guid, timestamp):
    """Sletter en kladde-faktura i Dinero. Bogførte fakturaer kan ikke slettes."""
    oid = _org_id()
    res = requests.delete(
        f"{API_BASE}/{oid}/invoices/{guid}",
        headers=_headers(),
        data=json.dumps({"Timestamp": timestamp}),
        timeout=20,
    )
    if not res.ok:
        raise RuntimeError(f"Kunne ikke slette faktura ({res.status_code}): {res.text[:300]}")
    return True


def opret_faktura(kunde_navn, linjer, dato=None, valuta="DKK",
                  kommentar="", beskrivelse=None, kontakt_email="",
                  moms="ja"):
    """
    Opretter en faktura-kladde i Dinero.

    linjer: liste af dicts med keys: beskrivelse, antal, enhedspris (DKK).
    moms: 'ja' → konto 1000 (m/moms), 'nej' → konto 1050 (u/moms).
    Ikke-DKK valuta tvinger altid konto 1050 (eksport).
    beskrivelse: titlen på fakturaen (synlig i Dinero-kladde). Hvis None,
                 bruges kommentar som fallback.

    Returnerer (guid, timestamp) — guid bruges til senere opslag/bogføring.
    """
    oid = _org_id()
    contact_id = find_eller_opret_kontakt(kunde_navn, kontakt_email)

    # Vælg salgskonto: 1000 m/moms, 1050 u/moms (eksport)
    eksport = valuta.upper() != "DKK"
    konto = 1000 if (moms == "ja" and not eksport) else 1050

    product_lines = []
    for l in linjer:
        product_lines.append({
            "ProductGuid":       None,
            "Description":       l.get("beskrivelse", ""),
            "Quantity":          float(l.get("antal", 1) or 1),
            "AccountNumber":     konto,
            "Unit":              "parts",
            "BaseAmountValue":   float(l.get("enhedspris", 0) or 0),
            "LineType":          "Product",
        })

    desc = beskrivelse or kommentar
    payload = {
        "Currency":         valuta,
        "Language":         "da-DK",
        "ExternalReference": desc,
        "Description":      desc,
        "Comment":          kommentar,
        "Date":             dato or time.strftime("%Y-%m-%d"),
        "ContactGuid":      contact_id,
        "ShowLinesInclVat": False,
        "ProductLines":     product_lines,
        "PaymentConditionType":            "Netto",
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


# ────────── KØBSBILAG VIA ENTRIES ──────────

VALID_INT_CODES = {"INT-ADMIN", "INT-DRIFT", "INT-KONTOR", "INT-MARKETING", "INT-SALG", "INT-HISTORY"}
_CODE_RE = re.compile(r'\[([A-Z]+-[A-Z0-9]+)\]')


def parse_project_code(text: str) -> Optional[str]:
    """
    Udtrækker en [PROJ-XXX] eller [INT-XXX] kode fra en tekst.
    Returnerer koden (uden firkantede parenteser) eller None.
    """
    if not text:
        return None
    m = _CODE_RE.search(text)
    if not m:
        return None
    code = m.group(1)
    if code.startswith("PROJ-"):
        return code
    if code.startswith("INT-"):
        if code not in VALID_INT_CODES:
            log.warning("Ukendt intern kode '%s' i tekst: %s", code, text[:80])
        return code
    return None


def fetch_purchase_entries(from_date: str, to_date: str) -> list[dict]:
    """
    Henter alle bogførte entries i perioden og filtrerer til
    Purchases + relevante manuelle poster (kto 2000-2999).

    from_date/to_date: ISO-format 'YYYY-MM-DD'. Skal ligge i ét regnskabsår.
    Returnerer liste af dicts med nøgler:
        date, voucher_number, voucher_type, account, account_name,
        description, amount, entry_guid, contact_guid, project_code
    """
    oid = _org_id()
    try:
        res = requests.get(
            f"{API_BASE}/{oid}/entries",
            headers=_headers(),
            params={"fromDate": from_date, "toDate": to_date},
            timeout=30,
        )
    except requests.Timeout:
        raise RuntimeError("Dinero entries-timeout — prøv et kortere datointervald")

    if res.status_code == 429:
        raise RuntimeError("Dinero rate-limit nået — vent og prøv igen")
    if res.status_code == 401:
        global _token_cache
        _token_cache = {"access_token": None, "expires_at": 0}
        raise RuntimeError("Dinero auth-fejl — token er udløbet, prøv igen")
    if not res.ok:
        raise RuntimeError(f"Dinero entries fejlede ({res.status_code}): {res.text[:300]}")

    raw = res.json()
    entries = []
    for e in raw:
        vtype = e.get("VoucherType") or ""
        amount = e.get("Amount", 0)
        acct = e.get("AccountNumber", 0)
        desc = e.get("Description") or ""
        # Kun udgifter: Purchases, eller manuelle poster på konto 2000-2999
        if vtype == "Purchases" and amount > 0:
            pass  # inkluder
        elif vtype == "manuel" and 2000 <= acct < 3000 and amount > 0:
            pass  # inkluder (f.eks. kursdifferencer)
        else:
            continue

        entries.append({
            "date":           e.get("Date", ""),
            "voucher_number": e.get("VoucherNumber"),
            "voucher_type":   vtype,
            "account":        acct,
            "account_name":   e.get("AccountName", ""),
            "description":    desc,
            "amount":         amount,
            "entry_guid":     e.get("EntryGuid"),
            "contact_guid":   e.get("ContactGuid"),
            "project_code":   parse_project_code(desc),
        })
    return entries


def group_entries_by_project(entries: list[dict]) -> dict[str, list[dict]]:
    """
    Grupperer en liste entries efter project_code.
    Entries uden gyldig kode havner under 'UNTAGGED'.
    """
    groups: dict[str, list[dict]] = {}
    for e in entries:
        key = e.get("project_code") or "UNTAGGED"
        groups.setdefault(key, []).append(e)
    return groups
