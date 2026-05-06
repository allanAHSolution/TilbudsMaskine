from flask import Flask, render_template, request, send_file, redirect, url_for, session, jsonify
import json
import os
import uuid
import calendar
from datetime import datetime, timedelta
from fpdf import FPDF

try:
    import dinero as dinero_api
    DINERO_OK = True
except Exception as e:
    dinero_api = None
    DINERO_OK  = False
    print(f"⚠ Dinero-modul kunne ikke loades: {e}")

app = Flask(__name__)
app.secret_key = "ahsolution_secret_2026"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUKTER_FILE     = os.path.join(BASE_DIR, 'produkter.json')
TILBUD_FILE        = os.path.join(BASE_DIR, 'tilbud_arkiv.json')
NUMMER_FILE        = os.path.join(BASE_DIR, 'nummer.txt')
LEVERANDOERER_FILE = os.path.join(BASE_DIR, 'leverandoerer.json')
INDSTILLINGER_FILE = os.path.join(BASE_DIR, 'indstillinger.json')
MALTE_FILE         = os.path.join(BASE_DIR, 'malte_aftale.json')
UNOX_FILE          = os.path.join(BASE_DIR, 'unox_aftale.json')
DINERO_TAGS_FILE   = os.path.join(BASE_DIR, 'dinero_bilag_tags.json')

ADMIN_USER = "allan"
ADMIN_PASS = "ahsolution-Gjern-26"
GUEST_USER = "guest"
GUEST_PASS = "2026"

MAANEDER = ["Jan", "Feb", "Mar", "Apr", "Maj", "Jun",
            "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]


@app.template_filter('dinero_dato')
def dinero_dato_filter(s):
    """Konverterer Dinero datoformat (DD-MM-YYYY eller DD/MM/YYYY) til HTML input format (YYYY-MM-DD)."""
    if not s:
        return ''
    for fmt in ('%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(s.strip(), fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return ''


def load_data(file, default):
    if os.path.exists(file):
        with open(file, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except Exception:
                return default
    return default


def save_data(file, data):
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def get_next_nummer():
    try:
        with open(NUMMER_FILE, 'r') as f:
            n = int(f.read().strip())
    except Exception:
        n = 1
    with open(NUMMER_FILE, 'w') as f:
        f.write(str(n + 1))
    return n


def til_dkk(beloeb, valuta, kurser):
    """Konverterer beløb til DKK baseret på valutakurser."""
    if valuta == 'DKK':
        return beloeb
    return beloeb * kurser.get(valuta, 1.0)


def _f(s, default=0.0):
    """Sikker float-konvertering — håndterer None, '', og dansk talformat."""
    if s is None or s == '':
        return default
    try:
        if isinstance(s, (int, float)):
            return float(s)
        s = str(s).strip()
        if not s:
            return default
        # Dansk format: 1.234,56 → 1234.56 (period=thousand, comma=decimal)
        if ',' in s and '.' in s:
            s = s.replace('.', '').replace(',', '.')
        elif ',' in s:
            s = s.replace(',', '.')
        elif '.' in s:
            # Heuristik: hvis præcis 3 cifre efter eneste punktum og kun cifre før → thousand-sep
            parts = s.split('.')
            if len(parts) == 2 and len(parts[1]) == 3 and parts[0].lstrip('-').isdigit():
                s = s.replace('.', '')
        return float(s)
    except (ValueError, TypeError):
        return default


def gæt_hs_kode(navn):
    """
    Foreslår en HS-kode (TARIC) baseret på produktnavn.
    Domænespecifik for vandrensning/spildevand. Forudsætter at tanke,
    olieudskillere, brønde, karme og kombi-enheder fra Ahlsell/Watercare
    er PE-plast (3925.x). Returnerer tom streng hvis ingen match.
    """
    n = (navn or '').lower()

    # Service-poster (ingen HS)
    if any(k in n for k in ['konsulent', 'timer', 'rådgiv', 'sat ', 'site acceptance',
                             'remote', 'fjernadgang', 'systemintegration', 'idriftsætt',
                             'idriftsät', 'opstart', 'pris i alt', 'total', 'fragt']):
        return ''

    # PE-plast vand-/spildevandsudstyr fra Watercare/Ahlsell først
    # (matches på 'brønd' før 'pumpe' så pumpebrønd bliver plastic, ikke pumpe)
    # 3925.10.00 — Reservoirer/tanke/beholdere af plast >300L
    if 'pumpebrønd' in n: return '3925.10.00'
    if 'prøvetagning' in n or 'brønd' in n: return '3925.10.00'
    if 'olieudskill' in n or 'fedtudskill' in n: return '3925.10.00'
    if 'tungmetal' in n: return '3925.10.00'
    if 'sandfang' in n: return '3925.10.00'
    if 'kombitank' in n or 'buffertank' in n or 'plasttank' in n: return '3925.10.00'
    if 'kombi' in n and ('tank' in n or 'sf' in n or 'sandfang' in n): return '3925.10.00'
    if 'tank' in n: return '3925.10.00'

    # Pumper (efter brønd-checks så 'pumpebrønd' ikke rammer her)
    if 'doseringspumpe' in n: return '8413.50.20'
    if 'feed pump' in n or 'feedpump' in n: return '8413.81.00'
    if 'sump pump' in n or 'sumppump' in n or 'dykpumpe' in n: return '8413.70.21'
    if 'pumpe' in n: return '8413.70.21'

    # Karm/dæksel (PE/plast bygningsudstyr)
    if 'karm' in n or 'dæksel' in n: return '3925.90.20'

    # Filtre med moving/filter-medier (selvrensende osv. har funktionelt apparat)
    if 'selvrensende filter' in n: return '8421.29.00'
    if 'filter' in n: return '8421.29.00'

    # Sensorer / måling
    if 'radar' in n and ('sensor' in n or 'level' in n or 'niveau' in n): return '9026.10.81'
    if 'level sensor' in n or 'levelsensor' in n: return '9026.10.81'
    if 'højvand' in n or 'niveau' in n: return '9026.20.50'
    if 'flow' in n and 'meter' in n: return '9028.20.00'

    # Styring / alarmer
    if 'plc' in n or 'hmi' in n: return '8537.10.99'
    if 'alarm' in n: return '8531.10.95'

    # Fittings / rør
    if 'fitting' in n or 'rørdele' in n: return '3917.40.00'  # plast fittings (PE)
    if 'rør' in n: return '3917.21.10'  # PE rør

    # Vogne / transportudstyr
    if 'hjulvogn' in n or 'vogn' in n: return '8716.80.00'

    return ''


def _dinero_kategori(beskrivelse, kunde):
    """Map en faktura til Dinero-kategori 'varer' eller 'ydelser'.
    Bruges til at vælge salgskonto ved eksport (1255 vs 1260)."""
    kat = _kategoriser_faktura(beskrivelse, kunde)
    return 'ydelser' if kat in ('timer', 'kommission') else 'varer'


def _kategoriser_faktura(beskrivelse, kunde):
    """Klassificér en faktura som produkter / timer / kommission baseret
    på tekst. Bruges til opdelt omsætningsgraf."""
    b = (beskrivelse or '').lower()
    k = (kunde or '').lower()
    if 'malte' in k:
        if 'provision' in b: return 'kommission'
        return 'timer'
    if 'uno-x' in k or 'uno x' in k:
        if 'timer' in b or 'konsulent' in b or 'uge' in b:
            return 'timer'
    if 'timer' in b or 'konsulent' in b:
        return 'timer'
    return 'produkter'


def beregn_statistik_opdelt(all_data, malte_data, unox_data, kurser, aar,
                              dinero_invs_alle=None):
    """
    Returnerer omsætning opdelt i produkter/timer/kommission per måned.

    Bruger faktura-datoer (ikke tilbuds-dato), inkluderer Dinero-only
    fakturaer matchet på kode/navn, og konverterer alt til DKK.
    """
    result = {m: {"produkter": 0.0, "timer": 0.0, "kommission": 0.0} for m in MAANEDER}
    kendte_dinero_guids = set()

    # ── Lokale fakturaer på vundne tilbud ──
    # Tæl kun reelt-bogført omsætning: kladder i Dinero er ikke omsætning endnu
    BOOKED_STATUSES = {'Booked', 'Paid', 'booked', 'paid', ''}
    for t in all_data.values():
        if t.get('slettet'):
            continue
        proj_valuta = t.get('valuta', 'DKK')
        kunde_navn  = t.get('kunde', '')
        for f in t.get('fakturaer', []):
            try:
                dato = datetime.strptime(f.get('dato', ''), '%d-%m-%Y')
            except (ValueError, TypeError):
                continue
            if dato.year != aar:
                continue
            # Husk dinero_guid for at undgå dobbelt-tælling (også for kladder)
            if f.get('dinero_guid'):
                kendte_dinero_guids.add(f['dinero_guid'])
            # Spring kladder over (Dineros omsætningstal indeholder dem ikke)
            din_status = f.get('dinero_status', '')
            if f.get('dinero_guid') and din_status not in BOOKED_STATUSES:
                continue
            beloeb_dkk = til_dkk(_f(f.get('beloeb')),
                                 f.get('valuta') or proj_valuta, kurser)
            kat = _kategoriser_faktura(f.get('beskrivelse', ''), kunde_navn)
            result[MAANEDER[dato.month - 1]][kat] += beloeb_dkk

    # ── Dinero-only fakturaer (oprettet direkte i Dinero, ikke via ERP) ──
    if dinero_invs_alle:
        for i in dinero_invs_alle:
            if i.get('guid') in kendte_dinero_guids:
                continue
            try:
                dato = datetime.strptime(i.get('date', ''), '%Y-%m-%d')
            except (ValueError, TypeError):
                continue
            if dato.year != aar:
                continue
            beloeb_dkk = til_dkk(_f(i.get('total_incl_vat')),
                                 i.get('currency', 'DKK'), kurser)
            kat = _kategoriser_faktura(i.get('description', ''), i.get('contact_name', ''))
            result[MAANEDER[dato.month - 1]][kat] += beloeb_dkk

    return result


def safe_text(text):
    """Returnerer teksten uændret — DejaVu understøtter fuld Unicode."""
    return text or ""


def generer_pdf(tilbud, doc_type="tilbud"):
    """Genererer Tilbud eller Ordrebekræftelse som PDF og returnerer (filepath, filename)."""
    BLUE = (30, 50, 90)
    LIGHT_BLUE = (230, 235, 245)
    GREY_ROW = (245, 247, 250)

    FONT_DIR = os.path.join(BASE_DIR, 'fonts')

    pdf = FPDF()
    pdf.add_font('DejaVu', '',  os.path.join(FONT_DIR, 'DejaVuSans.ttf'))
    pdf.add_font('DejaVu', 'B', os.path.join(FONT_DIR, 'DejaVuSans-Bold.ttf'))
    pdf.add_font('DejaVu', 'I', os.path.join(FONT_DIR, 'DejaVuSans-Oblique.ttf'))
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_margins(15, 15, 15)

    # Logo
    logo_path = os.path.join(BASE_DIR, 'static', 'logo.png')
    if os.path.exists(logo_path):
        pdf.image(logo_path, x=15, y=12, h=18)

    # Dokumenttype og nummer øverst til højre
    pdf.set_xy(15, 12)
    pdf.set_font('DejaVu', 'B', 18)
    pdf.set_text_color(*BLUE)
    if doc_type == "tilbud":
        titel = f"TILBUD #{tilbud['nummer']}"
    else:
        titel = f"ORDREBEKRÆFTELSE #{tilbud['nummer']}"
    pdf.cell(0, 10, safe_text(titel), align='R')
    pdf.ln(10)

    # Dato og reference
    pdf.set_font('DejaVu', '', 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 5, f"Dato: {tilbud.get('dato', '')}   |   Valuta: {tilbud.get('valuta', 'NOK')}   |   Levering: {tilbud.get('incoterm', 'EXW')}", align='R')
    pdf.ln(8)

    # Separator linje
    pdf.set_draw_color(*BLUE)
    pdf.set_line_width(0.5)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(6)

    # Kundeoplysninger
    pdf.set_font('DejaVu', 'B', 8)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 4, 'KUNDE / MODTAGER')
    pdf.ln(5)
    pdf.set_font('DejaVu', 'B', 11)
    pdf.set_text_color(*BLUE)
    pdf.cell(0, 6, safe_text(tilbud.get('kunde', '')))
    pdf.ln(6)
    pdf.set_font('DejaVu', '', 9)
    pdf.set_text_color(50, 50, 50)
    if tilbud.get('site'):
        pdf.cell(0, 5, safe_text(tilbud['site']))
        pdf.ln(5)
    if tilbud.get('att'):
        pdf.cell(0, 5, safe_text(f"Att: {tilbud['att']}"))
        pdf.ln(5)
    pdf.ln(6)

    # Intro tekst
    if tilbud.get('intro_tekst'):
        pdf.set_font('DejaVu', '', 8.5)
        pdf.set_text_color(60, 60, 60)
        pdf.multi_cell(0, 5, safe_text(tilbud['intro_tekst']))
        pdf.ln(6)

    # Produkttabel - header
    valuta = tilbud.get('valuta', 'NOK')
    pdf.set_fill_color(*BLUE)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('DejaVu', 'B', 9)
    pdf.cell(88, 7, 'BESKRIVELSE', fill=True)
    pdf.cell(22, 7, 'ANTAL', fill=True, align='C')
    pdf.cell(40, 7, f'ENHEDSPRIS ({valuta})', fill=True, align='R')
    pdf.cell(30, 7, f'TOTAL ({valuta})', fill=True, align='R')
    pdf.ln(7)

    # Produktlinjer
    total_sum = 0
    for i, p in enumerate(tilbud.get('produkter', [])):
        try:
            antal = _f(p.get('antal', 1), 1)
            pris = _f(p.get('pris', 0))
            linje_total = antal * pris
        except Exception:
            antal, pris, linje_total = 1, 0, 0
        total_sum += linje_total

        fill = (i % 2 == 0)
        bg = GREY_ROW if fill else (255, 255, 255)
        pdf.set_fill_color(*bg)
        pdf.set_text_color(30, 30, 30)
        pdf.set_font('DejaVu', 'B', 9)

        antal_str = str(int(antal)) if antal == int(antal) else str(antal)

        pdf.cell(88, 6, safe_text(p.get('navn', ''))[:48], fill=fill)
        pdf.set_font('DejaVu', '', 9)
        pdf.cell(22, 6, antal_str, fill=fill, align='C')
        # Pris=0 vises som "Inkluderet" — linjen er en del af samlet tilbudspris
        if pris == 0:
            pdf.set_font('DejaVu', 'I', 9)
            pdf.set_text_color(120, 120, 120)
            pdf.cell(40, 6, 'Inkluderet', fill=fill, align='R')
            pdf.cell(30, 6, '—', fill=fill, align='R')
            pdf.set_text_color(30, 30, 30)
            pdf.set_font('DejaVu', '', 9)
        else:
            pdf.cell(40, 6, f"{pris:,.0f}", fill=fill, align='R')
            pdf.cell(30, 6, f"{linje_total:,.0f}", fill=fill, align='R')
        pdf.ln(6)

        if p.get('beskrivelse'):
            pdf.set_font('DejaVu', 'I', 8)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(10, 4, '', fill=fill)
            pdf.cell(170, 4, safe_text(p['beskrivelse'])[:80], fill=fill)
            pdf.ln(4)
            pdf.set_text_color(30, 30, 30)

    pdf.ln(3)

    # Totaler
    pdf.set_fill_color(*LIGHT_BLUE)
    pdf.set_text_color(*BLUE)
    pdf.set_font('DejaVu', 'B', 10)
    pdf.cell(150, 8, 'TOTAL EKSKL. MOMS', fill=True)
    pdf.cell(30, 8, f"{total_sum:,.0f} {valuta}", fill=True, align='R')
    pdf.ln(8)

    if tilbud.get('moms') == 'ja':
        moms = total_sum * 0.25
        total_inkl = total_sum + moms
        pdf.set_font('DejaVu', '', 9)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(150, 6, 'Moms (25%)')
        pdf.cell(30, 6, f"{moms:,.0f} {valuta}", align='R')
        pdf.ln(6)
        pdf.set_fill_color(*BLUE)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font('DejaVu', 'B', 11)
        pdf.cell(150, 8, 'TOTAL INKL. MOMS', fill=True)
        pdf.cell(30, 8, f"{total_inkl:,.0f} {valuta}", fill=True, align='R')
        pdf.ln(8)

    # ── Fragt note ──
    if tilbud.get('fragt_separat'):
        paalæg = tilbud.get('fragt_paalæg_pct', 10)
        pdf.set_font('DejaVu', 'I', 8.5)
        pdf.set_text_color(120, 80, 0)
        pdf.set_fill_color(255, 249, 230)
        pdf.cell(0, 6,
                 f"  OBS: Priser er eksklusive fragt. Fragt faktureres separat med {paalæg:.0f}% påslag.",
                 fill=True)
        pdf.ln(8)
    else:
        pdf.ln(8)

    # ── Salgs- og leveringsbetingelser ──
    pdf.set_draw_color(*BLUE)
    pdf.set_line_width(0.4)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(4)

    pdf.set_font('DejaVu', 'B', 9)
    pdf.set_text_color(*BLUE)
    pdf.cell(0, 5, 'SALGS- OG LEVERINGSBETINGELSER')
    pdf.ln(6)

    if tilbud.get('betaling') == '5050':
        betalingsplan = "50% ved ordrebekræftelse, 50% ved levering."
    else:
        betalingsplan = "40% ved ordrebekræftelse, 40% ved levering og 20% ved afsluttet installation/idriftsættelse."

    betingelser = [
        ("1. Gyldighed",
         "Tilbuddet er gældende i 30 dage."),
        ("2. Betaling",
         f"14 dage netto. Fakturering sker jf. følgende plan: {betalingsplan}"),
        ("3. Ejendomsforbehold",
         "Anlægget forbliver AhSolutions ejendom indtil fuld betaling er erlagt."),
        ("4. Garanti",
         "Der ydes 12 mdr. garanti på fabrikationsfejl fra idriftsættelse."),
        ("5. Undtagelser",
         "Sliddele og følgeskader pga. frost eller manglende vedligehold dækkes ikke."),
        ("6. Ansvar",
         "AhSolution hæfter ikke for driftstab eller tidstab."),
    ]

    for titel, tekst in betingelser:
        pdf.set_font('DejaVu', 'B', 8)
        pdf.set_text_color(50, 50, 50)
        pdf.cell(0, 5, titel)
        pdf.ln(5)
        pdf.set_font('DejaVu', '', 8)
        pdf.set_text_color(80, 80, 80)
        pdf.set_x(25)
        pdf.multi_cell(165, 4.5, tekst)
        pdf.ln(2)

    pdf.ln(4)

    # Footer
    pdf.set_draw_color(200, 200, 200)
    pdf.set_line_width(0.3)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(4)
    pdf.set_font('DejaVu', '', 7.5)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 4, 'AhSolution ApS  |  CVR: 45081125  |  ah@ahsolution.dk  |  +45 23 81 72 72  |  Tingbakken 39, 8883 Gjern', align='C')

    # Filnavn og gem
    kunde_safe = tilbud.get('kunde', 'Kunde').strip().replace(' ', '_').replace('/', '_')
    if doc_type == "tilbud":
        filename = f"Tilbud_{kunde_safe}_{tilbud['nummer']}.pdf"
    else:
        filename = f"Ordrebekræftelse_{kunde_safe}_{tilbud['nummer']}.pdf"

    filepath = os.path.join(BASE_DIR, filename)
    pdf.output(filepath)
    return filepath, filename


def _toldfaktura_default(tilbud, importoer=None):
    """Default-værdier til en ny toldfaktura, brugt før første gemning."""
    imp = importoer or {}
    return {
        "id":              str(uuid.uuid4()),
        "oprettet":        datetime.now().strftime('%d-%m-%Y'),
        "ret_dato":        datetime.now().strftime('%d-%m-%Y'),
        "titel":           "",
        "faktura_nr":      str(tilbud.get('nummer', '')),
        "oprindelsesland": "Danmark",
        "fragt_beloeb":    0.0,
        "fragt_valuta":    tilbud.get('valuta', 'NOK'),
        "importoer": {
            "navn":          imp.get('navn') or tilbud.get('kunde', ''),
            "adresse_linje": imp.get('adresse_linje', ''),
            "postnr":        imp.get('postnr', ''),
            "by":            imp.get('by', ''),
            "land_kode":     imp.get('land_kode', 'NO'),
            "vat_nr":        imp.get('vat_nr', ''),
            "att":           imp.get('att', '') or tilbud.get('att', ''),
        },
        "lev_anderledes":   False,
        "leveringsadresse": {},
        "valgte_indekser":  None,  # None = alle produktlinjer
    }


def _saml_toldfaktura_fra_form(form, tilbud, importoer_default):
    """Bygger en toldfaktura-record ud fra formdata. Bruges af gem-routes."""
    valgte_raw = form.getlist('valgt_idx')
    if valgte_raw:
        try:
            valgte = sorted(set(int(x) for x in valgte_raw))
        except ValueError:
            valgte = None
        # Hvis alle markeret er det samme som None (alle)
        if valgte is not None and len(valgte) == len(tilbud.get('produkter', [])):
            valgte = None
    else:
        valgte = []  # ingen markeret → tom liste (PDF bliver tom)

    importoer = {
        "navn":          form.get('imp_navn', '').strip() or (importoer_default.get('navn') or tilbud.get('kunde', '')),
        "adresse_linje": form.get('imp_adresse', '').strip(),
        "postnr":        form.get('imp_postnr', '').strip(),
        "by":            form.get('imp_by', '').strip(),
        "land_kode":     form.get('imp_land', 'NO').strip().upper() or 'NO',
        "vat_nr":        form.get('imp_vat', '').strip(),
        "att":           form.get('imp_att', '').strip(),
    }

    lev_anderledes = form.get('lev_anderledes') == 'on'
    leveringsadresse = {}
    if lev_anderledes:
        leveringsadresse = {
            "navn":          form.get('lev_navn', '').strip(),
            "adresse_linje": form.get('lev_adresse', '').strip(),
            "postnr":        form.get('lev_postnr', '').strip(),
            "by":            form.get('lev_by', '').strip(),
            "att":           form.get('lev_att', '').strip(),
        }

    return {
        "titel":            form.get('titel', '').strip(),
        "faktura_nr":       form.get('faktura_nr', '').strip() or str(tilbud.get('nummer', '')),
        "oprindelsesland":  form.get('oprindelsesland', 'Danmark').strip() or 'Danmark',
        "fragt_beloeb":     _f(form.get('fragt_beloeb')),
        "fragt_valuta":     form.get('fragt_valuta') or tilbud.get('valuta', 'NOK'),
        "importoer":        importoer,
        "lev_anderledes":   lev_anderledes,
        "leveringsadresse": leveringsadresse,
        "valgte_indekser":  valgte,
        "ret_dato":         datetime.now().strftime('%d-%m-%Y'),
    }


def _leveringsadresse_til_pdf(told, tilbud):
    """Beregn endelig leveringsadresse: enten manuel (lev_anderledes), ellers fra importør+site."""
    if told.get('lev_anderledes') and told.get('leveringsadresse'):
        return told['leveringsadresse']
    imp = told.get('importoer', {})
    site = (tilbud.get('site') or '').strip()
    return {
        'navn':          (imp.get('navn', '') + (' — ' + site if site else '')),
        'adresse_linje': imp.get('adresse_linje', ''),
        'postnr':        imp.get('postnr', ''),
        'by':            imp.get('by', ''),
        'att':           imp.get('att', ''),
    }


def generer_toldfaktura_pdf(tilbud, fragt_beloeb=0.0, fragt_valuta=None,
                             oprindelsesland="Danmark",
                             importoer=None, leveringsadresse=None,
                             faktura_nr=None, faktura_dato=None,
                             valgte_indekser=None):
    """Genererer toldfaktura-PDF.

    valgte_indekser: liste af indekser i tilbud.produkter — hvis sat,
    medtages kun disse linjer (bruges til at splitte fx DK-varer og
    Kina-varer på separate toldfakturaer). None = alle produktlinjer.
    """
    BLUE      = (30, 50, 90)
    GREY_ROW  = (245, 247, 250)
    GREY_TEXT = (90, 90, 95)

    FONT_DIR = os.path.join(BASE_DIR, 'fonts')
    pdf = FPDF()
    pdf.add_font('DejaVu', '',  os.path.join(FONT_DIR, 'DejaVuSans.ttf'))
    pdf.add_font('DejaVu', 'B', os.path.join(FONT_DIR, 'DejaVuSans-Bold.ttf'))
    pdf.add_font('DejaVu', 'I', os.path.join(FONT_DIR, 'DejaVuSans-Oblique.ttf'))
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_margins(15, 15, 15)

    valuta = fragt_valuta or tilbud.get('valuta', 'DKK')
    nr_str = str(faktura_nr) if faktura_nr else str(tilbud.get('nummer', ''))
    dato_str = faktura_dato or datetime.now().strftime('%B %d, %Y')

    # ── Header: Firmanavn venstre, TOLDFAKTURA højre i lysegrå ──
    pdf.set_xy(15, 15)
    pdf.set_font('DejaVu', 'B', 16); pdf.set_text_color(20, 20, 30)
    pdf.cell(95, 8, 'AhSolution ApS')
    pdf.set_xy(110, 12)
    pdf.set_font('DejaVu', 'B', 28); pdf.set_text_color(180, 180, 190)
    pdf.cell(85, 14, 'TOLDFAKTURA', align='R')
    pdf.ln(14)

    # ── Adresse-linjer venstre + Dato/nr højre ──
    pdf.set_xy(15, 30)
    pdf.set_font('DejaVu', '', 9); pdf.set_text_color(50, 50, 50)
    afs_lines = ['Tingbakken 39', '8883 Gjern', 'Tlf. 2381 7272', 'CVR.nr. 45 08 11 25']
    for line in afs_lines:
        pdf.cell(95, 4.5, safe_text(line)); pdf.ln(4.5)

    # Dato + Faktura nr højre side
    pdf.set_xy(125, 30)
    pdf.set_font('DejaVu', 'B', 9); pdf.set_text_color(20, 20, 30)
    pdf.cell(35, 4.5, 'Dato', align='R')
    pdf.set_font('DejaVu', '', 9)
    pdf.cell(35, 4.5, dato_str, align='R'); pdf.ln(4.5)
    pdf.set_x(125)
    pdf.set_font('DejaVu', 'B', 9)
    pdf.cell(35, 4.5, 'Faktura nr.', align='R')
    pdf.set_font('DejaVu', '', 9)
    pdf.cell(35, 4.5, nr_str, align='R'); pdf.ln(8)

    # ── Importør + Leveringsadresse side om side ──
    y_blocks = max(pdf.get_y() + 4, 56)
    pdf.set_xy(15, y_blocks)

    imp = importoer or {}
    pdf.set_font('DejaVu', 'B', 9); pdf.set_text_color(20, 20, 30)
    pdf.cell(90, 4.5, 'Kunde importør :'); pdf.ln(5)
    pdf.set_font('DejaVu', '', 9); pdf.set_text_color(50, 50, 50)
    imp_lines = [imp.get('navn') or tilbud.get('kunde', '')]
    if imp.get('adresse_linje'): imp_lines.append(imp['adresse_linje'])
    postnr_by = ' '.join(filter(None, [imp.get('postnr', ''), imp.get('by', '')]))
    if postnr_by:
        # Map landekode til navn
        land_map = {'NO': 'Norge', 'SE': 'Sverige', 'DE': 'Tyskland', 'GB': 'UK', 'DK': 'Danmark'}
        land = land_map.get((imp.get('land_kode') or '').upper(), imp.get('land_kode', ''))
        imp_lines.append(f"{postnr_by}{(' ' + land) if land else ''}")
    if imp.get('vat_nr'):
        # Norske kontakter har 9-cifret org.nr — præfix NO
        vat = imp['vat_nr']
        if (imp.get('land_kode') or '').upper() == 'NO' and not vat.upper().startswith('NO'):
            vat = 'NO' + vat
        imp_lines.append(vat)
    if imp.get('att'):
        imp_lines.append(f"[Att. {imp['att']}]")
    elif tilbud.get('att'):
        imp_lines.append(f"[Att. {tilbud['att']}]")
    for line in imp_lines:
        pdf.cell(90, 4.5, safe_text(line)); pdf.ln(4.5)

    # Leveringsadresse højre
    pdf.set_xy(110, y_blocks)
    pdf.set_font('DejaVu', 'B', 9); pdf.set_text_color(20, 20, 30)
    pdf.cell(85, 4.5, 'Leveringsadresse'); pdf.ln(5)
    pdf.set_font('DejaVu', '', 9); pdf.set_text_color(50, 50, 50)
    lev = leveringsadresse or {}
    lev_lines = []
    if lev.get('navn'): lev_lines.append(lev['navn'])
    if lev.get('adresse_linje'): lev_lines.append(lev['adresse_linje'])
    if lev.get('postnr') or lev.get('by'):
        lev_lines.append(' '.join(filter(None, [lev.get('postnr', ''), lev.get('by', '')])))
    if lev.get('att'):
        lev_lines.append(f"[Att. {lev['att']}]")
    if not lev_lines:
        lev_lines = ['—']
    for line in lev_lines:
        pdf.set_x(110)
        pdf.cell(85, 4.5, safe_text(line)); pdf.ln(4.5)

    pdf.ln(6)

    # ── Produkttabel: Beskrivelse | Beløb ──
    table_y = pdf.get_y()
    pdf.set_fill_color(*BLUE); pdf.set_text_color(255, 255, 255)
    pdf.set_font('DejaVu', 'B', 9.5)
    pdf.cell(135, 8, '  Beskrivelse', fill=True)
    pdf.cell(45, 8, 'Beløb  ', fill=True, align='R')
    pdf.ln(8)

    # Stamdata-lookup
    stamdata_hs = {p['navn'].lower().strip(): (p.get('hs_kode') or '').strip()
                   for p in load_data(PRODUKTER_FILE, [])}

    total_sum = 0.0
    valgte_set = set(valgte_indekser) if valgte_indekser is not None else None
    for i, p in enumerate(tilbud.get('produkter', [])):
        if valgte_set is not None and i not in valgte_set:
            continue
        antal = _f(p.get('antal', 1), 1)
        pris  = _f(p.get('pris', 0))
        linje_total = antal * pris
        total_sum += linje_total

        navn = p.get('navn', '')
        antal_str = str(int(antal)) if antal == int(antal) else f"{antal:.2f}"

        # Beskrivelse-tekst
        if pris > 0 and antal != 1:
            besk = f"{antal_str} stk {navn} x {pris:,.0f} {valuta}"
        elif pris > 0:
            besk = f"{antal_str} stk {navn}"
        else:
            besk = f"{antal_str} stk {navn}"

        # HS-kode (3-trins fallback)
        hs = (p.get('hs_kode') or '').strip()
        if not hs:
            hs = stamdata_hs.get(navn.lower().strip(), '')
        if not hs:
            hs = gæt_hs_kode(navn)

        # Beløb-tekst
        if pris == 0:
            beloeb_str = 'Inkluderet'
        else:
            beloeb_str = f"{linje_total:,.2f} {valuta}".replace(',', '_').replace('.', ',').replace('_', '.')

        pdf.set_text_color(20, 20, 30); pdf.set_font('DejaVu', '', 9)
        pdf.cell(135, 6, safe_text(besk))
        if pris == 0:
            pdf.set_font('DejaVu', 'I', 9); pdf.set_text_color(120, 120, 120)
            pdf.cell(45, 6, beloeb_str + '  ', align='R')
            pdf.set_text_color(20, 20, 30); pdf.set_font('DejaVu', '', 9)
        else:
            pdf.cell(45, 6, beloeb_str + '  ', align='R')
        pdf.ln(5)

        if hs:
            pdf.set_font('DejaVu', '', 8); pdf.set_text_color(*GREY_TEXT)
            # Vis kun de 4 første tegn af HS (industristandard på fakturaer)
            kort_hs = hs.split('.')[0] if '.' in hs else hs[:4]
            pdf.cell(135, 4.5, f"HS {kort_hs}")
            pdf.cell(45, 4.5, '', align='R')
            pdf.ln(5)

        pdf.ln(1)

    # Fragt-linje
    if fragt_beloeb > 0:
        pdf.set_text_color(20, 20, 30); pdf.set_font('DejaVu', '', 9)
        pdf.cell(135, 6, 'Fragt og håndtering')
        fragt_str = f"{fragt_beloeb:,.2f} {valuta}".replace(',', '_').replace('.', ',').replace('_', '.')
        pdf.cell(45, 6, fragt_str + '  ', align='R')
        pdf.ln(8)

    # Lidt mellemrum
    pdf.ln(4)

    # ── Subtotal + Total ──
    total = total_sum + fragt_beloeb
    sub_str   = f"{total_sum + fragt_beloeb:,.2f} {valuta}".replace(',', '_').replace('.', ',').replace('_', '.')
    total_str = f"{total:,.2f} {valuta}".replace(',', '_').replace('.', ',').replace('_', '.')

    # Subtotal-række
    pdf.set_fill_color(*GREY_ROW); pdf.set_text_color(20, 20, 30)
    pdf.cell(110, 7, '', fill=True)
    pdf.set_font('DejaVu', 'B', 9.5)
    pdf.cell(35, 7, 'Subtotal', fill=True, align='R')
    pdf.set_font('DejaVu', '', 9.5)
    pdf.cell(35, 7, sub_str + '  ', fill=True, align='R')
    pdf.ln(7)

    # Tom mellem-række (visuelt mellemrum i grå boks)
    pdf.set_fill_color(*GREY_ROW)
    pdf.cell(180, 5, '', fill=True)
    pdf.ln(5)

    # Total-række
    pdf.set_fill_color(*GREY_ROW); pdf.set_font('DejaVu', 'B', 10.5)
    pdf.cell(110, 8, '', fill=True)
    pdf.cell(35, 8, 'Total', fill=True, align='R')
    pdf.cell(35, 8, total_str + '  ', fill=True, align='R')
    pdf.ln(12)

    # ── Footer notes ──
    pdf.set_text_color(20, 20, 30); pdf.set_font('DejaVu', '', 9)
    pdf.cell(0, 5, 'Moms: 0% - eksport af varer uden for EU, ML §34'); pdf.ln(5)

    leverings_by = (lev.get('by') if lev else '') or imp.get('by', '') or '[by]'
    pdf.cell(0, 5, f'Incoterm: "DAP {leverings_by}, Norway (Incoterms® 2020)')
    pdf.ln(8)

    kunde_safe = (importoer or {}).get('navn', tilbud.get('kunde', 'Kunde')).strip().replace(' ', '_').replace('/', '_')
    filename = f"Toldfaktura_{kunde_safe}_{nr_str}.pdf"
    filepath = os.path.join(BASE_DIR, filename)
    pdf.output(filepath)
    return filepath, filename


# ──────────────────────────────────────────────
# RUTER
# ──────────────────────────────────────────────

@app.route('/')
def forside():
    return render_template('forside.html')


@app.route('/vandberegner')
def vandberegner():
    if not session.get('logged_in') and not session.get('guest'):
        return redirect(url_for('login'))
    return render_template('vandberegner.html')


@app.route('/vandberegner/no')
def vandberegner_no():
    if not session.get('logged_in') and not session.get('guest'):
        return redirect(url_for('login'))
    return redirect('/vandberegner?land=no')


# ────────── DINERO-INTEGRATION ──────────
@app.route('/dinero/kontakter')
def dinero_kontakter():
    """Autocomplete-endpoint: returnerer liste af kontakter fra Dinero."""
    if not session.get('logged_in'):
        return jsonify([])
    if not DINERO_OK:
        return jsonify({"error": "Dinero ikke konfigureret"}), 503
    try:
        sog = request.args.get('q', '')
        return jsonify(dinero_api.hent_kontakter(sog=sog, limit=20))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/dinero/sync')
def dinero_sync():
    """Opdater betalingsstatus på pushede fakturaer + retry fejlede pushes."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    if not DINERO_OK:
        return redirect(url_for('admin_panel'))

    all_data = load_data(TILBUD_FILE, {})
    for t in all_data.values():
        for f in t.get('fakturaer', []):
            guid = f.get('dinero_guid')

            # 1) Retry: fakturaer der fejlede tidligere
            if not guid and f.get('dinero_fejl'):
                try:
                    beskrivelse = f.get('beskrivelse') or f"Faktura til {t.get('kunde','')}"
                    try:
                        dato_iso = datetime.strptime(f.get('dato',''), '%d-%m-%Y').strftime('%Y-%m-%d')
                    except ValueError:
                        dato_iso = datetime.now().strftime('%Y-%m-%d')
                    linjer = [{"beskrivelse": beskrivelse, "antal": 1, "enhedspris": _f(f.get('beloeb'))}]
                    site_str = f" · {t.get('site').strip()}" if t.get('site') else ''
                    titel    = f"#{t.get('nummer')} {t.get('kunde','')}{site_str} — {beskrivelse}"
                    komment  = f"{titel}\n\nJf. vores ordrebekræftelse nr. {t.get('nummer')}"
                    new_guid, ts = dinero_api.opret_faktura(
                        kunde_navn=t.get('kunde', ''),
                        linjer=linjer,
                        dato=dato_iso,
                        valuta=t.get('valuta', 'DKK'),
                        moms=t.get('moms', 'ja'),
                        beskrivelse=titel,
                        kommentar=komment,
                        kategori=_dinero_kategori(beskrivelse, t.get('kunde', '')),
                    )
                    f['dinero_guid']      = new_guid
                    f['dinero_timestamp'] = ts
                    f['dinero_status']    = 'Draft'
                    f.pop('dinero_fejl', None)
                except Exception as e:
                    f['dinero_fejl'] = str(e)[:200]
                continue

            # 2) Opdater status på eksisterende
            if not guid or f.get('dinero_status') == 'Paid':
                continue
            try:
                info = dinero_api.hent_faktura_status(guid)
                ps   = info.get('PaymentStatus') or info.get('paymentStatus') or ''
                f['dinero_status']    = ps if ps else (f.get('dinero_status') or 'Draft')
                f['dinero_timestamp'] = info.get('TimeStamp') or info.get('timeStamp') or f.get('dinero_timestamp')
            except Exception as e:
                f['dinero_fejl'] = str(e)[:200]

    save_data(TILBUD_FILE, all_data)
    return redirect(url_for('dinero_omkostninger'))


def _anvend_lokale_tags(entries, tags, projekt_map=None):
    """
    Lægger lokale ERP-tags oven på entries. Prioritet:
      1. Lokal tag (dinero_bilag_tags.json)
      2. Kode i tekst (PROJ-XXX / INT-XXX)
      3. Fallback: match på site/kunde-navn fra vundne projekter
    """
    for e in entries:
        guid = e.get('entry_guid')
        if guid and guid in tags:
            e['project_code'] = tags[guid].get('code')
            e['tag_source']   = 'local'
            e['tag_note']     = tags[guid].get('note', '')
        elif e.get('project_code'):
            e['tag_source'] = 'dinero'
        elif projekt_map:
            match = _match_paa_navn(e.get('description', ''), projekt_map)
            if match:
                e['project_code'] = match
                e['tag_source']   = 'navn'
            else:
                e['tag_source'] = None
        else:
            e['tag_source'] = None
    return entries


def _projekt_map(all_data):
    """Byg PROJ-XXX → tilbud dict for matching."""
    pm = {}
    for tid, t in all_data.items():
        if t.get('vundet') and not t.get('slettet'):
            code = f"PROJ-{t.get('nummer', 0):03d}"
            pm[code] = {
                'id':     tid,
                'kunde':  t.get('kunde', ''),
                'site':   t.get('site', ''),
                'nummer': t.get('nummer', 0),
            }
    return pm


def _match_paa_navn(desc, projekt_map):
    """
    Fallback matching: hvis ingen kode, prøv at matche på site eller
    kunde-navn. Returnerer PROJ-XXX hvis ét entydigt match, ellers None.
    """
    if not desc:
        return None
    low = desc.lower()
    # Prioriter site-navn (mere specifikt end kunde)
    site_hits = [code for code, info in projekt_map.items()
                 if info.get('site') and len(info['site'].strip()) >= 4
                 and info['site'].strip().lower() in low]
    if len(site_hits) == 1:
        return site_hits[0]
    # Fallback til kundenavn
    kunde_hits = [code for code, info in projekt_map.items()
                  if info.get('kunde') and len(info['kunde'].strip()) >= 4
                  and info['kunde'].strip().lower() in low]
    if len(kunde_hits) == 1:
        return kunde_hits[0]
    return None


@app.route('/dinero/omkostninger')
def dinero_omkostninger():
    """Viser alle købsposter fra Dinero grupperet efter projektkode."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    if not DINERO_OK:
        return redirect(url_for('admin_panel'))

    aar = datetime.now().year
    try:
        entries = dinero_api.fetch_purchase_entries(f'{aar}-01-01', datetime.now().strftime('%Y-%m-%d'))
    except Exception as e:
        return f"Fejl ved hentning: {e} <a href='/admin'>Tilbage</a>", 500

    tags = load_data(DINERO_TAGS_FILE, {})
    all_data = load_data(TILBUD_FILE, {})
    projekt_map = _projekt_map(all_data)
    entries = _anvend_lokale_tags(entries, tags, projekt_map)
    groups = dinero_api.group_entries_by_project(entries)

    return render_template('dinero_omkostninger.html',
                           groups=groups,
                           projekt_map=projekt_map,
                           total_entries=len(entries),
                           aar=aar)


@app.route('/dinero/bilag/tag', methods=['POST'])
def dinero_bilag_tag():
    """Gemmer et lokalt ERP-tag på et Dinero-bilag (kan ikke rettes i Dinero bagefter)."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    guid = request.form.get('entry_guid', '').strip()
    code = request.form.get('code', '').strip()
    note = request.form.get('note', '').strip()

    if not guid:
        return redirect(url_for('dinero_omkostninger'))

    tags = load_data(DINERO_TAGS_FILE, {})
    if not code:
        # Tom kode = fjern tag
        tags.pop(guid, None)
    else:
        tags[guid] = {
            "code":        code,
            "note":        note,
            "assigned_at": datetime.now().strftime('%Y-%m-%d %H:%M'),
        }
    save_data(DINERO_TAGS_FILE, tags)
    return redirect(url_for('dinero_omkostninger'))


@app.route('/dinero/kladder/reset')
def dinero_reset_kladder():
    """
    Sletter alle Dinero-kladder (ikke-bogførte) for alle fakturaer i ERP
    og rydder lokale dinero_guid-felter så de pushes på ny ved næste sync.
    Bogførte fakturaer røres IKKE.
    """
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    if not DINERO_OK:
        return redirect(url_for('admin_panel'))

    all_data = load_data(TILBUD_FILE, {})
    slettet = 0
    bevaret = 0
    fejl    = 0
    for t in all_data.values():
        for f in t.get('fakturaer', []):
            guid = f.get('dinero_guid')
            if not guid:
                continue
            try:
                info   = dinero_api.hent_faktura_status(guid)
                status = info.get('PaymentStatus') or info.get('Status') or ''
                if status in ('Draft', 'Kladde', 'draft'):
                    ts = info.get('TimeStamp') or f.get('dinero_timestamp')
                    dinero_api.slet_faktura(guid, ts)
                    # Ryd lokalt så den retrys næste sync
                    f.pop('dinero_guid', None)
                    f.pop('dinero_timestamp', None)
                    f.pop('dinero_status', None)
                    f.pop('dinero_fejl', None)
                    slettet += 1
                else:
                    bevaret += 1
            except Exception as e:
                f['dinero_fejl'] = f"Reset fejlede: {str(e)[:180]}"
                fejl += 1

    save_data(TILBUD_FILE, all_data)
    return (
        f"✓ Slettet {slettet} kladder i Dinero. "
        f"Bevaret {bevaret} bogførte. Fejl: {fejl}. "
        f"<br><a href='/dinero/sync'>Klik her for at re-pushe dem nu</a> "
        f"<br><a href='/admin'>Eller tilbage til admin</a>"
    )


@app.route('/dinero/test')
def dinero_test():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    if not DINERO_OK:
        return "Dinero-modulet kunne ikke loades (mangler requests eller dinero_config.json)", 503
    ok, msg = dinero_api.test_forbindelse()
    return f"{'✓' if ok else '✗'} {msg}"


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        u = request.form['username']
        p = request.form['password']
        if u == ADMIN_USER and p == ADMIN_PASS:
            session['logged_in'] = True
            return redirect(url_for('admin_panel'))
        error = "Forkert brugernavn eller adgangskode"
    return render_template('login.html', error=error)


@app.route('/vandberegner/login', methods=['GET', 'POST'])
def vandberegner_login():
    error = None
    if request.method == 'POST':
        u = request.form['username']
        p = request.form['password']
        if u == GUEST_USER and p == GUEST_PASS:
            session['guest'] = True
            return redirect(url_for('vandberegner'))
        error = "Forkert brugernavn eller adgangskode"
    return render_template('login.html',
                           error=error,
                           titel='Vandberegner',
                           undertitel='Log ind for at tilgå vandbesparings-beregneren')


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('guest', None)
    return redirect(url_for('forside'))


@app.route('/admin')
def admin_panel():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})

    afventer   = {k: v for k, v in all_data.items() if v.get('vundet') is None  and not v.get('arkiveret') and not v.get('slettet')}
    vundne     = {k: v for k, v in all_data.items() if v.get('vundet') is True  and not v.get('arkiveret') and not v.get('slettet')}
    tabte      = {k: v for k, v in all_data.items() if v.get('vundet') is False and not v.get('arkiveret') and not v.get('slettet')}
    arkiverede = {k: v for k, v in all_data.items() if v.get('arkiveret') and not v.get('slettet')}

    # Pre-beregn alder på afventende tilbud (bruges til udløbsadvarsler)
    idag = datetime.now()
    for t in afventer.values():
        try:
            dato = datetime.strptime(t['dato'], '%d-%m-%Y')
            t['_dage_gammel'] = (idag - dato).days
        except Exception:
            t['_dage_gammel'] = 0

    indstillinger = load_data(INDSTILLINGER_FILE, {"kurser": {"NOK": 0.63, "EUR": 7.46, "SEK": 0.67}})
    kurser = indstillinger.get("kurser", {"NOK": 0.63, "EUR": 7.46, "SEK": 0.67})
    aar = idag.year

    malte_data = load_data(MALTE_FILE, _malte_default())
    unox_data  = load_data(UNOX_FILE, _unox_default())

    # Hent ALLE Dinero-fakturaer for året — bruges af både stat og pr-projekt
    dinero_invs_alle = []
    if DINERO_OK:
        try:
            dinero_invs_alle = dinero_api.fetch_invoices(from_date=f'{aar}-01-01')
        except Exception:
            pass

    statistik_opdelt = beregn_statistik_opdelt(all_data, malte_data, unox_data,
                                                kurser, aar, dinero_invs_alle)
    max_val = max((sum(v.values()) for v in statistik_opdelt.values()), default=1) or 1
    now_date = idag.strftime('%Y-%m-%d')

    # Summariske tal til stat-kort
    afventer_total = sum(
        sum(_f(p.get('antal', 1), 1) * _f(p.get('pris', 0)) for p in t.get('produkter', []))
        for t in afventer.values()
    )
    vundne_ufaktureret = sum(
        max(0, sum(_f(p.get('antal', 1), 1) * _f(p.get('pris', 0)) for p in t.get('produkter', []))
               - sum(_f(f.get('beloeb')) for f in t.get('fakturaer', [])))
        for t in vundne.values()
    )
    faktureringsopgaver = _beregn_faktureringsopgaver(idag)
    alle_opgaver        = _alle_aabne_opgaver(all_data, idag)

    # --- Aggregér pr. projekt fra de allerede-hentede Dinero-data ---
    dinero_salg_per_proj      = {}  # PROJ-XXX → sum (DKK)
    dinero_salg_list_per_proj = {}  # PROJ-XXX → liste af fakturaer
    dinero_omk_per_proj       = {}  # PROJ-XXX → sum (DKK)
    if DINERO_OK:
        try:
            pmap = _projekt_map(all_data)
            tags = load_data(DINERO_TAGS_FILE, {})
            # Salgsfakturaer matchet til projekter
            kendte_guids = set()
            for t in vundne.values():
                for f in t.get('fakturaer', []):
                    if f.get('dinero_guid'):
                        kendte_guids.add(f['dinero_guid'])
            # Byg allowlist-map: fakturanummer → projektkode (eksplicit tilknytning)
            # og sæt af projekter der opter ud af navne-matching pga. allowlist
            allowlist_map = {}
            projekter_med_allowlist = set()
            for _tid, _t in all_data.items():
                _nums = _dinero_allowlist(_t)
                if _nums is None:
                    continue
                _kode = f"PROJ-{_t.get('nummer', 0):03d}"
                projekter_med_allowlist.add(_kode)
                for _n in _nums:
                    allowlist_map[_n] = _kode

            for i in dinero_invs_alle:
                if i['guid'] in kendte_guids:
                    continue
                inv_num = None
                try:
                    inv_num = int(i.get('number'))
                except (TypeError, ValueError):
                    pass
                if inv_num is not None and inv_num in allowlist_map:
                    kode = allowlist_map[inv_num]
                else:
                    kode = i.get('project_code') or _match_paa_navn(i.get('description', ''), pmap)
                    # Hvis projektet har allowlist og denne faktura ikke er på den → drop
                    if kode in projekter_med_allowlist:
                        kode = None
                if kode and kode.startswith('PROJ-'):
                    beloeb_dkk = til_dkk(_f(i.get('total_incl_vat')), i.get('currency', 'DKK'), kurser)
                    dinero_salg_per_proj[kode] = dinero_salg_per_proj.get(kode, 0) + beloeb_dkk
                    dinero_salg_list_per_proj.setdefault(kode, []).append(i)
            # Købsbilag (omkostninger)
            entries = dinero_api.fetch_purchase_entries(f'{aar}-01-01', idag.strftime('%Y-%m-%d'))
            entries = _anvend_lokale_tags(entries, tags, pmap)
            for e in entries:
                kode = e.get('project_code')
                if kode and kode.startswith('PROJ-'):
                    dinero_omk_per_proj[kode] = dinero_omk_per_proj.get(kode, 0) + e['amount']
        except Exception:
            pass

    # Gruppér vundne projekter efter kunde (normaliser: strip + case-insensitive)
    vundne_per_kunde = {}
    for tid, t in vundne.items():
        raw   = (t.get('kunde') or '—').strip() or '—'
        nkey  = raw.lower()
        if nkey not in vundne_per_kunde:
            vundne_per_kunde[nkey] = {'display': raw, 'items': []}
        vundne_per_kunde[nkey]['items'].append((tid, t))
    vundne_grupper = []
    for nkey in sorted(vundne_per_kunde.keys()):
        projekter = sorted(vundne_per_kunde[nkey]['items'], key=lambda x: -(x[1].get('nummer') or 0))
        grp_total = 0  # budget i DKK
        grp_fakt  = 0  # faktureret i DKK
        grp_udg   = 0  # udgifter i DKK
        for _, t in projekter:
            proj_code = f"PROJ-{t.get('nummer', 0):03d}"
            pval      = t.get('valuta', 'DKK')
            # Budget + lokale fakturaer er i projekt-valuta → konverter til DKK
            grp_total += til_dkk(sum(_f(p.get('antal',1), 1)*_f(p.get('pris',0)) for p in t.get('produkter',[])), pval, kurser)
            # Hver faktura kan have sin egen valuta (fx DKK-faktura på NOK-projekt)
            for f in t.get('fakturaer', []):
                grp_fakt += til_dkk(_f(f.get('beloeb')), f.get('valuta') or pval, kurser)
            grp_udg   += til_dkk(sum(_f(o.get('beloeb')) for o in t.get('projekt',{}).get('omkostninger',[])), pval, kurser)
            # Dinero-salg og -køb er allerede i DKK (eller egen valuta for salg)
            grp_fakt  += dinero_salg_per_proj.get(proj_code, 0)  # antaget DKK (TODO: konverter salg-valuta)
            grp_udg   += dinero_omk_per_proj.get(proj_code, 0)   # DKK
        vundne_grupper.append({
            'kunde':     vundne_per_kunde[nkey]['display'],
            'projekter': projekter,
            'total':     grp_total,
            'faktureret': grp_fakt,
            'udgifter':  grp_udg,
            'antal':     len(projekter),
            'valuta':    'DKK',
        })

    return render_template('index.html',
                           produkter=load_data(PRODUKTER_FILE, []),
                           afventer=afventer,
                           vundne=vundne,
                           vundne_grupper=vundne_grupper,
                           dinero_salg_per_proj=dinero_salg_per_proj,
                           dinero_salg_list_per_proj=dinero_salg_list_per_proj,
                           dinero_omk_per_proj=dinero_omk_per_proj,
                           tabte=tabte,
                           arkiverede=arkiverede,
                           statistik_opdelt=statistik_opdelt,
                           max_val=max_val,
                           aar=aar,
                           kurser=kurser,
                           now_date=now_date,
                           afventer_total=afventer_total,
                           vundne_ufaktureret=vundne_ufaktureret,
                           faktureringsopgaver=faktureringsopgaver,
                           alle_opgaver=alle_opgaver)


@app.route('/admin/produkter', methods=['GET', 'POST'])
def manage_products():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    products = load_data(PRODUKTER_FILE, [])
    leverandoerer = load_data(LEVERANDOERER_FILE, [])
    indstillinger = load_data(INDSTILLINGER_FILE, {})
    # Merge defaults så manglende kurser (fx USD) altid er tilstede
    default_kurser = {"NOK": 0.63, "EUR": 7.46, "SEK": 0.67, "USD": 6.85}
    kurser = {**default_kurser, **(indstillinger.get("kurser") or {})}

    def _safe_float(s, default=0.0):
        try:
            return float(s) if s not in (None, '') else default
        except (ValueError, TypeError):
            return default

    if request.method == 'POST':
        action = request.form.get('action')
        # Kostpris kan indtastes i hvilken som helst valuta — konverter til DKK
        kostpris_orig   = _safe_float(request.form.get('kostpris_input'))
        kostpris_valuta = request.form.get('kostpris_valuta', 'DKK') or 'DKK'
        kostpris_dkk    = til_dkk(kostpris_orig, kostpris_valuta, kurser)

        if action == 'add':
            products.append({
                "navn":            request.form.get('navn'),
                "pris":            _safe_float(request.form.get('pris')),
                "kostpris":        kostpris_dkk,
                "kostpris_orig":   kostpris_orig,
                "kostpris_valuta": kostpris_valuta,
                "hs_kode":         (request.form.get('hs_kode') or '').strip(),
                "enhed":           request.form.get('enhed', ''),
                "leverandoer":     request.form.get('leverandoer', '')
            })
        elif action == 'delete':
            index = int(request.form.get('index'))
            products.pop(index)
        elif action == 'edit':
            index = int(request.form.get('index'))
            products[index] = {
                "navn":            request.form.get('navn'),
                "pris":            _safe_float(request.form.get('pris')),
                "kostpris":        kostpris_dkk,
                "kostpris_orig":   kostpris_orig,
                "kostpris_valuta": kostpris_valuta,
                "hs_kode":         (request.form.get('hs_kode') or '').strip(),
                "enhed":           request.form.get('enhed', ''),
                "leverandoer":     request.form.get('leverandoer', '')
            }
        elif action == 'foreslå_hs':
            # Fyld TOMME HS-koder ud baseret på produktnavn
            for p in products:
                if not (p.get('hs_kode') or '').strip():
                    forslag = gæt_hs_kode(p.get('navn', ''))
                    if forslag:
                        p['hs_kode'] = forslag
        elif action == 'genberegn_hs':
            # Overskriv ALLE HS-koder med nye gæt (fx efter regelopdatering)
            for p in products:
                forslag = gæt_hs_kode(p.get('navn', ''))
                p['hs_kode'] = forslag
        save_data(PRODUKTER_FILE, products)
        return redirect(url_for('manage_products'))

    return render_template('manage_products.html',
                           produkter=products,
                           leverandoerer=leverandoerer,
                           aktiv_fane=request.args.get('fane', 'produkter'),
                           kurser=kurser)


@app.route('/admin/leverandoerer', methods=['POST'])
def manage_leverandoerer():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    leverandoerer = load_data(LEVERANDOERER_FILE, [])
    action = request.form.get('action')

    if action == 'add':
        leverandoerer.append({
            "navn":      request.form.get('navn', ''),
            "kontakt":   request.form.get('kontakt', ''),
            "email":     request.form.get('email', ''),
            "telefon":   request.form.get('telefon', ''),
            "hjemmeside": request.form.get('hjemmeside', ''),
            "noter":     request.form.get('noter', '')
        })
    elif action == 'edit':
        index = int(request.form.get('index'))
        leverandoerer[index] = {
            "navn":      request.form.get('navn', ''),
            "kontakt":   request.form.get('kontakt', ''),
            "email":     request.form.get('email', ''),
            "telefon":   request.form.get('telefon', ''),
            "hjemmeside": request.form.get('hjemmeside', ''),
            "noter":     request.form.get('noter', '')
        }
    elif action == 'delete':
        index = int(request.form.get('index'))
        leverandoerer.pop(index)

    save_data(LEVERANDOERER_FILE, leverandoerer)
    return redirect(url_for('manage_products', fane='leverandoerer'))


@app.route('/opret', methods=['POST'])
def opret_tilbud():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    eksisterende_id = request.form.get('eksisterende_id', '').strip()
    all_data = load_data(TILBUD_FILE, {})

    # Saml produktlinjer
    navne          = request.form.getlist('p_navn')
    antal_list     = request.form.getlist('p_antal')
    priser         = request.form.getlist('p_pris')
    kostpriser     = request.form.getlist('p_kostpris')
    beskrivelser   = request.form.getlist('p_beskrivelse')

    produkter = []
    for i, navn in enumerate(navne):
        if navn.strip():
            produkter.append({
                "navn":        navn,
                "antal":       antal_list[i] if i < len(antal_list) else "1",
                "pris":        priser[i] if i < len(priser) else "0",
                "kostpris":    _f(kostpriser[i]) if i < len(kostpriser) else 0.0,
                "beskrivelse": beskrivelser[i] if i < len(beskrivelser) else "",
            })

    try:
        fragt_paalæg = float(request.form.get('fragt_paalæg_pct', 10) or 10)
    except (ValueError, TypeError):
        fragt_paalæg = 10.0

    faelles_felter = {
        "kunde": request.form.get('kunde', ''),
        "site": request.form.get('site', ''),
        "att": request.form.get('att', ''),
        "moms": request.form.get('moms', 'nej'),
        "valuta": request.form.get('valuta', 'NOK'),
        "betaling": request.form.get('betaling', '5050'),
        "incoterm": request.form.get('incoterm', 'EXW'),
        "intro_tekst": request.form.get('intro_tekst', ''),
        "produkter": produkter,
        "fragt_separat": request.form.get('fragt_separat') == 'on',
        "fragt_paalæg_pct": fragt_paalæg,
    }

    if eksisterende_id and eksisterende_id in all_data:
        # Opdater eksisterende tilbud
        all_data[eksisterende_id].update(faelles_felter)
        tilbud = all_data[eksisterende_id]
    else:
        # Nyt tilbud — kan være et selvstændigt tilbud eller et ekstra tilbud (child) på et eksisterende projekt
        parent_id = (request.form.get('parent_id') or '').strip()
        er_ekstra = parent_id and parent_id in all_data
        tilbud_id = str(uuid.uuid4())
        nummer = get_next_nummer()
        tilbud = {
            "id": tilbud_id,
            "nummer": nummer,
            "dato": datetime.now().strftime('%d-%m-%Y'),
            "arkiveret": False,
            # Ekstra tilbud markeres automatisk som vundet (de er en del af et eksisterende projekt)
            "vundet": True if er_ekstra else None,
            "slettet": False,
            "parent_id": parent_id if er_ekstra else None,
            **faelles_felter
        }
        all_data[tilbud_id] = tilbud

    save_data(TILBUD_FILE, all_data)
    generer_pdf(tilbud, "tilbud")

    return redirect(url_for('admin_panel'))


@app.route('/download/<tilbud_id>/<doc_type>')
def download_pdf(tilbud_id, doc_type):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    tilbud = all_data.get(tilbud_id)
    if not tilbud:
        return "Tilbud ikke fundet", 404

    filepath, filename = generer_pdf(tilbud, doc_type)
    return send_file(filepath, as_attachment=True, download_name=filename)


def _hent_importoer(tilbud):
    """Hent importør-info fra Dinero med fallback til tilbuddets kunde."""
    importoer = None
    if DINERO_OK and tilbud.get('kunde'):
        try:
            importoer = dinero_api.hent_kontakt_detaljer(tilbud['kunde'])
        except Exception:
            importoer = None
    if not importoer:
        importoer = {'navn': tilbud.get('kunde', ''), 'land_kode': 'NO'}
    return importoer


@app.route('/toldfaktura/<tilbud_id>')
def toldfaktura_liste(tilbud_id):
    """Liste over toldfakturaer på et tilbud. Tom liste = vis 'opret ny'-knap."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    all_data = load_data(TILBUD_FILE, {})
    tilbud = all_data.get(tilbud_id)
    if not tilbud:
        return "Tilbud ikke fundet", 404
    toldfakturaer = tilbud.get('toldfakturaer', [])
    return render_template('toldfaktura_liste.html',
                           t=tilbud, id=tilbud_id,
                           toldfakturaer=toldfakturaer)


@app.route('/toldfaktura/<tilbud_id>/ny')
def toldfaktura_ny(tilbud_id):
    """Vis blank form til ny toldfaktura."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    all_data = load_data(TILBUD_FILE, {})
    tilbud = all_data.get(tilbud_id)
    if not tilbud:
        return "Tilbud ikke fundet", 404
    importoer = _hent_importoer(tilbud)
    told = _toldfaktura_default(tilbud, importoer)
    return render_template('toldfaktura_form.html',
                           t=tilbud, id=tilbud_id, importoer=importoer,
                           told=told, told_id=None, er_ny=True)


@app.route('/toldfaktura/<tilbud_id>/ret/<told_id>')
def toldfaktura_ret(tilbud_id, told_id):
    """Vis form til redigering af eksisterende toldfaktura."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    all_data = load_data(TILBUD_FILE, {})
    tilbud = all_data.get(tilbud_id)
    if not tilbud:
        return "Tilbud ikke fundet", 404
    told = next((x for x in tilbud.get('toldfakturaer', []) if x.get('id') == told_id), None)
    if not told:
        return redirect(url_for('toldfaktura_liste', tilbud_id=tilbud_id))
    importoer = _hent_importoer(tilbud)
    return render_template('toldfaktura_form.html',
                           t=tilbud, id=tilbud_id, importoer=importoer,
                           told=told, told_id=told_id, er_ny=False)


@app.route('/toldfaktura/<tilbud_id>/gem', methods=['POST'])
@app.route('/toldfaktura/<tilbud_id>/gem/<told_id>', methods=['POST'])
def toldfaktura_gem(tilbud_id, told_id=None):
    """Gem ny eller eksisterende toldfaktura. Redirect til list-view."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    tilbud = all_data.get(tilbud_id)
    if not tilbud:
        return "Tilbud ikke fundet", 404

    importoer_default = _hent_importoer(tilbud)
    payload = _saml_toldfaktura_fra_form(request.form, tilbud, importoer_default)

    tilbud.setdefault('toldfakturaer', [])
    if told_id:
        # Opdater eksisterende
        for x in tilbud['toldfakturaer']:
            if x.get('id') == told_id:
                x.update(payload)
                break
        else:
            return redirect(url_for('toldfaktura_liste', tilbud_id=tilbud_id))
        save_id = told_id
    else:
        # Opret ny
        ny = _toldfaktura_default(tilbud, importoer_default)
        ny.update(payload)
        tilbud['toldfakturaer'].append(ny)
        save_id = ny['id']

    save_data(TILBUD_FILE, all_data)

    # Hvis brugeren trykkede "Gem og hent PDF" — generer + download
    if request.form.get('gem_og_hent_pdf') == '1':
        return redirect(url_for('toldfaktura_pdf', tilbud_id=tilbud_id, told_id=save_id))
    return redirect(url_for('toldfaktura_liste', tilbud_id=tilbud_id))


@app.route('/toldfaktura/<tilbud_id>/pdf/<told_id>')
def toldfaktura_pdf(tilbud_id, told_id):
    """Generer + download PDF for en gemt toldfaktura."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    all_data = load_data(TILBUD_FILE, {})
    tilbud = all_data.get(tilbud_id)
    if not tilbud:
        return "Tilbud ikke fundet", 404
    told = next((x for x in tilbud.get('toldfakturaer', []) if x.get('id') == told_id), None)
    if not told:
        return redirect(url_for('toldfaktura_liste', tilbud_id=tilbud_id))

    leveringsadresse = _leveringsadresse_til_pdf(told, tilbud)
    filepath, filename = generer_toldfaktura_pdf(
        tilbud,
        fragt_beloeb=_f(told.get('fragt_beloeb')),
        fragt_valuta=told.get('fragt_valuta'),
        oprindelsesland=told.get('oprindelsesland', 'Danmark'),
        importoer=told.get('importoer'),
        leveringsadresse=leveringsadresse,
        faktura_nr=told.get('faktura_nr') or None,
        valgte_indekser=told.get('valgte_indekser'),
    )
    return send_file(filepath, as_attachment=True, download_name=filename)


@app.route('/toldfaktura/<tilbud_id>/slet/<told_id>')
def toldfaktura_slet(tilbud_id, told_id):
    """Slet en toldfaktura."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    all_data = load_data(TILBUD_FILE, {})
    tilbud = all_data.get(tilbud_id)
    if tilbud and tilbud.get('toldfakturaer'):
        tilbud['toldfakturaer'] = [x for x in tilbud['toldfakturaer'] if x.get('id') != told_id]
        save_data(TILBUD_FILE, all_data)
    return redirect(url_for('toldfaktura_liste', tilbud_id=tilbud_id))


@app.route('/status/<tilbud_id>/<status>')
def set_status(tilbud_id, status):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    if tilbud_id in all_data:
        if status == 'vundet':
            all_data[tilbud_id]['vundet'] = True
            if 'projekt' not in all_data[tilbud_id]:
                all_data[tilbud_id]['projekt'] = {
                    "oprettet": datetime.now().strftime('%d-%m-%Y'),
                    "omkostninger": [],
                    "noter": "",
                }
        elif status == 'tabt':
            all_data[tilbud_id]['vundet'] = False
        elif status == 'aktiv':
            all_data[tilbud_id]['vundet'] = None
        save_data(TILBUD_FILE, all_data)
    return redirect(url_for('admin_panel'))


@app.route('/arkiver/<tilbud_id>')
def arkiver_tilbud(tilbud_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    if tilbud_id in all_data:
        all_data[tilbud_id]['arkiveret'] = True
        save_data(TILBUD_FILE, all_data)
    return redirect(url_for('admin_panel'))


@app.route('/genaktiver/<tilbud_id>')
def genaktiver_tilbud(tilbud_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    if tilbud_id in all_data:
        all_data[tilbud_id]['arkiveret'] = False
        save_data(TILBUD_FILE, all_data)
    return redirect(url_for('admin_panel'))


# ────────── PROJEKT-MODUL ──────────
def _projekt_budget(tilbud):
    """Beregn samlet tilbudssum som budget."""
    return sum(
        _f(p.get('antal', 1), 1) * _f(p.get('pris', 0))
        for p in tilbud.get('produkter', [])
    )


def _projekt_rod_id(all_data, tilbud_id):
    """Returnér roden af projekt-træet (parent uden parent). Bruges til at gruppere ekstra tilbud."""
    seen = set()
    cur = tilbud_id
    while cur and cur not in seen:
        seen.add(cur)
        t = all_data.get(cur)
        if not t:
            return cur
        pid = t.get('parent_id')
        if not pid or pid not in all_data:
            return cur
        cur = pid
    return cur


def _projekt_soeskende(all_data, tilbud_id):
    """Returnér liste af alle tilbud i samme projekt-gruppe (root + children) ordnet efter nummer."""
    rod = _projekt_rod_id(all_data, tilbud_id)
    medlemmer = []
    for tid, t in all_data.items():
        if t.get('slettet'):
            continue
        if tid == rod or t.get('parent_id') == rod:
            medlemmer.append((tid, t))
    medlemmer.sort(key=lambda x: x[1].get('nummer') or 0)
    return rod, medlemmer


def _opgave_frist_dt(o):
    """Parse frist-dato (DD-MM-YYYY). Returnér datetime.max hvis tom/ugyldig så den ryger sidst i sortering."""
    try:
        return datetime.strptime(o.get('frist', ''), '%d-%m-%Y')
    except Exception:
        return datetime.max


def _sorter_opgaver(opgaver):
    """Sortér efter (færdig sidst, frist stigende). Tilføjer original_index så templates kan slette/toggle korrekt."""
    indekseret = [{**o, 'original_index': i} for i, o in enumerate(opgaver)]
    indekseret.sort(key=lambda o: (bool(o.get('faerdig')), _opgave_frist_dt(o)))
    return indekseret


def _dinero_allowlist(tilbud):
    """Returnér set af Dinero-fakturanumre der eksplicit hører til projektet, eller None hvis ingen allowlist er sat."""
    nums = tilbud.get('projekt', {}).get('dinero_faktura_numre')
    if not nums:
        return None
    result = set()
    for n in nums:
        try:
            result.add(int(str(n).strip()))
        except (TypeError, ValueError):
            continue
    return result if result else None


def _alle_aabne_opgaver(all_data, idag):
    """Saml ufærdige opgaver fra alle vundne, ikke-arkiverede projekter, sorteret efter frist."""
    result = []
    for tid, t in all_data.items():
        if t.get('arkiveret') or t.get('slettet') or t.get('vundet') is not True:
            continue
        for idx, o in enumerate(t.get('projekt', {}).get('opgaver', [])):
            if o.get('faerdig'):
                continue
            frist_dt = _opgave_frist_dt(o)
            dage = (frist_dt - idag).days if frist_dt != datetime.max else None
            result.append({
                'tilbud_id': tid,
                'index': idx,
                'titel': o.get('titel', ''),
                'frist': o.get('frist', ''),
                'frist_sort': frist_dt,
                'dage': dage,
                'projekt_nummer': t.get('nummer'),
                'projekt_kunde': t.get('kunde'),
                'projekt_site': t.get('site'),
            })
    result.sort(key=lambda x: x['frist_sort'])
    return result


@app.route('/projekt/<tilbud_id>')
def projekt_side(tilbud_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    t = all_data.get(tilbud_id)
    if not t or t.get('vundet') is not True:
        return redirect(url_for('admin_panel'))

    if 'projekt' not in t:
        t['projekt'] = {"oprettet": datetime.now().strftime('%d-%m-%Y'), "omkostninger": [], "opgaver": [], "noter": ""}
        save_data(TILBUD_FILE, all_data)
    elif 'opgaver' not in t['projekt']:
        t['projekt']['opgaver'] = []
        save_data(TILBUD_FILE, all_data)

    # Valutakurser til konvertering (alt samles i DKK for korrekt margin)
    # Merge defaults så manglende kurser (USD/SEK) altid er tilstede
    indstillinger = load_data(INDSTILLINGER_FILE, {})
    default_kurser = {"NOK": 0.63, "EUR": 7.46, "SEK": 0.67, "USD": 6.85}
    kurser        = {**default_kurser, **(indstillinger.get("kurser") or {})}
    proj_valuta   = t.get('valuta', 'DKK')

    budget         = _projekt_budget(t)
    budget_dkk     = til_dkk(budget, proj_valuta, kurser)
    # Kostpris-baseret budget (intern forbrugsestimat) — altid i DKK
    omk_budget_dkk = sum(
        _f(p.get('antal', 1), 1) * _f(p.get('kostpris'))
        for p in t.get('produkter', [])
    )
    faktureret     = sum(_f(f.get('beloeb')) for f in t.get('fakturaer', []))
    # Pr-faktura valuta → summer DKK præcist
    faktureret_dkk = sum(
        til_dkk(_f(f.get('beloeb')), f.get('valuta') or proj_valuta, kurser)
        for f in t.get('fakturaer', [])
    )
    omkostninger   = t['projekt'].get('omkostninger', [])
    total_manuel   = sum(_f(o.get('beloeb')) for o in omkostninger)
    total_manuel_dkk = til_dkk(total_manuel, proj_valuta, kurser)

    # Hent Dinero-syncede omkostninger + salgsfakturaer tagget til projektet
    dinero_omk   = []
    dinero_salg  = []
    proj_code    = f"PROJ-{t.get('nummer', 0):03d}"
    if DINERO_OK:
        aar = datetime.now().year
        pmap = _projekt_map(all_data)

        # --- Omkostninger (købsbilag) ---
        try:
            entries = dinero_api.fetch_purchase_entries(f'{aar}-01-01', datetime.now().strftime('%Y-%m-%d'))
            tags = load_data(DINERO_TAGS_FILE, {})
            entries = _anvend_lokale_tags(entries, tags, pmap)
            dinero_omk = [e for e in entries if e.get('project_code') == proj_code]
        except Exception:
            pass

        # --- Salgsfakturaer (direkte oprettet i Dinero) ---
        try:
            invs = dinero_api.fetch_invoices(from_date=f'{aar}-01-01')
            # Ekskludér fakturaer der allerede er i tilbud.fakturaer (pushet fra ERP)
            kendte_guids = {f.get('dinero_guid') for f in t.get('fakturaer', []) if f.get('dinero_guid')}
            allowlist_nums = _dinero_allowlist(t)
            for i in invs:
                if i['guid'] in kendte_guids:
                    continue
                if allowlist_nums is not None:
                    # Eksplicit allowlist: kun de angivne fakturanumre tilhører projektet
                    try:
                        if int(i.get('number')) in allowlist_nums:
                            dinero_salg.append(i)
                    except (TypeError, ValueError):
                        pass
                    continue
                # Ingen allowlist: auto-match via projektkode eller navn
                kode = i.get('project_code') or _match_paa_navn(i.get('description', ''), pmap)
                if kode == proj_code:
                    dinero_salg.append(i)
        except Exception:
            pass

    # Dinero-tal er altid i DKK (købsbilag) eller egen valuta (salgsfakturaer)
    total_dinero_dkk      = sum(e['amount'] for e in dinero_omk)  # Dinero entries = DKK
    total_salg_dinero_dkk = sum(til_dkk(_f(i.get('total_incl_vat')),
                                         i.get('currency', 'DKK'), kurser)
                                 for i in dinero_salg)

    # Alle summer normaliseret til DKK for korrekt margin-beregning
    total_omk_dkk        = total_manuel_dkk + total_dinero_dkk
    faktureret_total_dkk = faktureret_dkk + total_salg_dinero_dkk
    margin_dkk           = faktureret_total_dkk - total_omk_dkk

    opgaver = _sorter_opgaver(t['projekt'].get('opgaver', []))

    # Find søskende-tilbud (samme projekt-gruppe) — vises kun hvis der er mere end ét tilbud
    rod_id, soeskende = _projekt_soeskende(all_data, tilbud_id)
    relaterede = [(tid, st) for tid, st in soeskende if tid != tilbud_id]
    er_rod = (rod_id == tilbud_id)

    return render_template('projekt.html',
                           t=t, id=tilbud_id,
                           opgaver=opgaver,
                           relaterede_tilbud=relaterede,
                           er_rod_tilbud=er_rod,
                           rod_tilbud_id=rod_id,
                           budget=budget,                  # i projekt-valuta
                           budget_dkk=budget_dkk,
                           omk_budget_dkk=omk_budget_dkk,  # kostpris-budget (DKK)
                           faktureret=faktureret,          # lokale fakturaer i proj-valuta
                           faktureret_total_dkk=faktureret_total_dkk,
                           total_omk_dkk=total_omk_dkk,
                           total_manuel=total_manuel,      # i proj-valuta
                           total_manuel_dkk=total_manuel_dkk,
                           total_dinero_dkk=total_dinero_dkk,
                           total_salg_dinero_dkk=total_salg_dinero_dkk,
                           dinero_omk=dinero_omk,
                           dinero_salg=dinero_salg,
                           proj_code=proj_code,
                           margin_dkk=margin_dkk,
                           proj_valuta=proj_valuta,
                           now_date=datetime.now().strftime('%Y-%m-%d'))


@app.route('/projekt/<tilbud_id>/omkostning', methods=['POST'])
def projekt_tilfoej_omkostning(tilbud_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    t = all_data.get(tilbud_id)
    if not t or 'projekt' not in t:
        return redirect(url_for('admin_panel'))

    dato_raw = request.form.get('dato', '')
    try:
        dato = datetime.strptime(dato_raw, '%Y-%m-%d').strftime('%d-%m-%Y')
    except ValueError:
        dato = datetime.now().strftime('%d-%m-%Y')

    t['projekt']['omkostninger'].append({
        "dato":        dato,
        "beskrivelse": request.form.get('beskrivelse', ''),
        "beloeb":      float(request.form.get('beloeb', 0) or 0),
        "leverandoer": request.form.get('leverandoer', ''),
    })
    save_data(TILBUD_FILE, all_data)
    return redirect(url_for('projekt_side', tilbud_id=tilbud_id))


@app.route('/projekt/<tilbud_id>/omkostning/slet/<int:index>')
def projekt_slet_omkostning(tilbud_id, index):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    t = all_data.get(tilbud_id)
    if t and 'projekt' in t:
        omk = t['projekt'].get('omkostninger', [])
        if 0 <= index < len(omk):
            omk.pop(index)
        save_data(TILBUD_FILE, all_data)
    return redirect(url_for('projekt_side', tilbud_id=tilbud_id))


@app.route('/projekt/<tilbud_id>/dinero-numre', methods=['POST'])
def projekt_gem_dinero_numre(tilbud_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    t = all_data.get(tilbud_id)
    if not t or 'projekt' not in t:
        return redirect(url_for('admin_panel'))

    raw = request.form.get('numre', '')
    numre = []
    for stykke in raw.replace(';', ',').split(','):
        s = stykke.strip()
        if not s:
            continue
        try:
            numre.append(int(s))
        except ValueError:
            continue
    # Tom liste fjerner allowlist (auto-match igen)
    if numre:
        t['projekt']['dinero_faktura_numre'] = numre
    else:
        t['projekt'].pop('dinero_faktura_numre', None)
    save_data(TILBUD_FILE, all_data)
    return redirect(url_for('projekt_side', tilbud_id=tilbud_id))


@app.route('/projekt/<tilbud_id>/opgave', methods=['POST'])
def projekt_tilfoej_opgave(tilbud_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    t = all_data.get(tilbud_id)
    if not t or 'projekt' not in t:
        return redirect(url_for('admin_panel'))

    titel = (request.form.get('titel') or '').strip()
    if not titel:
        return redirect(url_for('projekt_side', tilbud_id=tilbud_id))

    frist_raw = request.form.get('frist', '')
    try:
        frist = datetime.strptime(frist_raw, '%Y-%m-%d').strftime('%d-%m-%Y')
    except ValueError:
        frist = ''

    t['projekt'].setdefault('opgaver', []).append({
        "titel":    titel,
        "frist":    frist,
        "faerdig":  False,
        "oprettet": datetime.now().strftime('%d-%m-%Y'),
    })
    save_data(TILBUD_FILE, all_data)
    return redirect(url_for('projekt_side', tilbud_id=tilbud_id))


@app.route('/projekt/<tilbud_id>/opgave/toggle/<int:index>')
def projekt_toggle_opgave(tilbud_id, index):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    t = all_data.get(tilbud_id)
    if t and 'projekt' in t:
        opgaver = t['projekt'].get('opgaver', [])
        if 0 <= index < len(opgaver):
            opgaver[index]['faerdig'] = not opgaver[index].get('faerdig', False)
            save_data(TILBUD_FILE, all_data)

    redir = request.args.get('redir', 'projekt')
    if redir == 'admin':
        return redirect(url_for('admin_panel'))
    return redirect(url_for('projekt_side', tilbud_id=tilbud_id))


@app.route('/projekt/<tilbud_id>/opgave/slet/<int:index>')
def projekt_slet_opgave(tilbud_id, index):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    t = all_data.get(tilbud_id)
    if t and 'projekt' in t:
        opgaver = t['projekt'].get('opgaver', [])
        if 0 <= index < len(opgaver):
            opgaver.pop(index)
            save_data(TILBUD_FILE, all_data)
    return redirect(url_for('projekt_side', tilbud_id=tilbud_id))


@app.route('/nyt-tilbud')
def nyt_tilbud():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    rediger_id = request.args.get('rediger')
    ekstra_id  = request.args.get('ekstra')  # parent_id for ekstra tilbud
    all_data = load_data(TILBUD_FILE, {})
    rediger_data = all_data.get(rediger_id) if rediger_id else None
    indstillinger = load_data(INDSTILLINGER_FILE, {"kurser": {"NOK": 0.63, "EUR": 7.46, "SEK": 0.67}})
    kurser = indstillinger.get("kurser", {"NOK": 0.63, "EUR": 7.46, "SEK": 0.67})

    # Ekstra tilbud: forhånds-udfyld kunde/site/valuta fra parent men tom produktliste
    ekstra_parent = None
    forhaands_data = None
    if ekstra_id and not rediger_data:
        parent = all_data.get(ekstra_id)
        if parent:
            ekstra_parent = parent
            forhaands_data = {
                "kunde":       parent.get('kunde', ''),
                "site":        parent.get('site', ''),
                "att":         parent.get('att', ''),
                "moms":        parent.get('moms', 'nej'),
                "valuta":      parent.get('valuta', 'NOK'),
                "betaling":    parent.get('betaling', '5050'),
                "incoterm":    parent.get('incoterm', 'EXW'),
                "intro_tekst": '',
                "produkter":   [],
            }

    return render_template('nyt_tilbud.html',
                           produkter=load_data(PRODUKTER_FILE, []),
                           rediger_data=rediger_data,
                           forhaands_data=forhaands_data,
                           ekstra_parent_id=ekstra_id if ekstra_parent else None,
                           ekstra_parent=ekstra_parent,
                           kurser=kurser)


def _malte_default():
    return {
        "indstillinger": {"timer": 20, "timepris": 700, "provision_pct": 5, "valuta": "DKK"},
        "salg": [],
        "provision_faktureret": {},
        "retainer_faktureret": {}
    }


def _get_kvartal(dato_str):
    """'01-04-2026' → '2026-Q2'"""
    try:
        d = datetime.strptime(dato_str, '%d-%m-%Y')
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    except Exception:
        return None


def _kvartal_slut(kvartal_str):
    """'2026-Q2' → datetime(2026, 6, 30)"""
    try:
        year, q = kvartal_str.split('-Q')
        year, q = int(year), int(q)
        slut_md = q * 3
        return datetime(year, slut_md, calendar.monthrange(year, slut_md)[1])
    except Exception:
        return None


def _kvartal_navn(kvartal_str):
    """'2026-Q2' → 'Q2 2026'"""
    try:
        year, q = kvartal_str.split('-Q')
        return f"Q{q} {year}"
    except Exception:
        return kvartal_str


def _maaned_slut(year, month):
    """Returnerer sidste dag i måneden som datetime."""
    return datetime(year, month, calendar.monthrange(year, month)[1])


def _malte_oversigt(data):
    """Bygger kvartalsoversigt fra salgsdata."""
    kvartaler = {}
    for s in data.get('salg', []):
        k = _get_kvartal(s.get('dato', ''))
        if not k:
            continue
        if k not in kvartaler:
            slut = _kvartal_slut(k)
            kvartaler[k] = {
                'navn': _kvartal_navn(k),
                'slut': slut,
                'slut_str': slut.strftime('%d-%m-%Y') if slut else '',
                'salg': [],
                'total_salg': 0.0,
                'total_provision': 0.0,
                'faktureret': k in data.get('provision_faktureret', {}),
                'faktureret_dato': data.get('provision_faktureret', {}).get(k, ''),
            }
        total = _f(s.get('antal', 1), 1) * _f(s.get('enhedspris'))
        prov = total * _f(s.get('provision_pct', 5), 5) / 100
        kvartaler[k]['salg'].append(s)
        kvartaler[k]['total_salg'] += total
        kvartaler[k]['total_provision'] += prov
    return dict(sorted(kvartaler.items(), reverse=True))


def _unox_default():
    return {
        "indstillinger": {"timer_pr_periode": 74, "timepris": 0, "valuta": "DKK", "start_dato": "02-01-2026"},
        "perioder_faktureret": {},
        "indkoeb": []
    }


def _unox_perioder(start_dato_str, idag, faktureret):
    """Returnerer liste af 14-dages perioder fra start til nu+1."""
    try:
        start = datetime.strptime(start_dato_str, '%d-%m-%Y')
    except Exception:
        start = idag - timedelta(days=28)
    perioder = []
    dato = start
    while dato <= idag + timedelta(days=14):
        slut = dato + timedelta(days=14)
        key = slut.strftime('%Y-%m-%d')
        perioder.append({
            'start_str': dato.strftime('%d-%m-%Y'),
            'slut_str':  slut.strftime('%d-%m-%Y'),
            'key':       key,
            'faktureret': key in faktureret,
            'faktureret_dato': faktureret.get(key, ''),
            'slut_dato': slut,
            'dage_til':  (slut - idag).days,
        })
        dato = slut
    return list(reversed(perioder))


def _beregn_faktureringsopgaver(idag):
    """Aggregerer åbne faktureringsopgaver fra Malte, UNO-X og vundne tilbud."""
    opgaver = []

    # ── Malte retainer ──
    malte = load_data(MALTE_FILE, _malte_default())
    mind  = malte['indstillinger']
    md_key = idag.strftime('%Y-%m')
    if md_key not in malte.get('retainer_faktureret', {}):
        slut = _maaned_slut(idag.year, idag.month)
        opgaver.append({
            'titel':   f"Malte Timer – {slut.strftime('%B %Y')}",
            'beloeb':  mind['timer'] * mind['timepris'],
            'valuta':  'DKK',
            'forfald': slut.strftime('%d-%m-%Y'),
            'dage':    (slut - idag).days,
            'url':     '/malte',
            'ikon':    'fa-handshake-o',
            'farve':   '#7c3aed',
        })

    # ── Malte provision ──
    for k, kv in _malte_oversigt(malte).items():
        if not kv['faktureret'] and kv['total_provision'] > 0:
            dage = (kv['slut'] - idag).days if kv['slut'] else 999
            opgaver.append({
                'titel':   f"Malte Provision {kv['navn']}",
                'beloeb':  kv['total_provision'],
                'valuta':  mind.get('valuta', 'SEK'),
                'forfald': kv['slut_str'],
                'dage':    dage,
                'url':     '/malte',
                'ikon':    'fa-percent',
                'farve':   '#7c3aed',
            })

    # ── UNO-X timer ──
    unox = load_data(UNOX_FILE, _unox_default())
    uind = unox['indstillinger']
    if uind.get('timepris', 0) > 0:
        perioder = _unox_perioder(uind.get('start_dato', '02-01-2026'), idag, unox.get('perioder_faktureret', {}))
        for p in perioder[:3]:
            if not p['faktureret'] and p['dage_til'] <= 14:
                opgaver.append({
                    'titel':   f"UNO-X Timer {p['start_str']}–{p['slut_str']}",
                    'beloeb':  uind['timer_pr_periode'] * uind['timepris'],
                    'valuta':  uind['valuta'],
                    'forfald': p['slut_str'],
                    'dage':    p['dage_til'],
                    'url':     '/unox',
                    'ikon':    'fa-clock-o',
                    'farve':   '#0ea5e9',
                })

    # ── UNO-X indkøb ──
    for k in unox.get('indkoeb', []):
        if not k.get('faktureret'):
            opgaver.append({
                'titel':   k['beskrivelse'],
                'beloeb':  k.get('beloeb', 0),
                'valuta':  uind['valuta'],
                'forfald': '',
                'dage':    999,
                'url':     '/unox',
                'ikon':    'fa-shopping-cart',
                'farve':   '#0ea5e9',
            })

    # ── Vundne tilbud – betalingsrater ──
    all_tilbud = load_data(TILBUD_FILE, {})
    indstillinger_local = load_data(INDSTILLINGER_FILE, {"kurser": {"NOK": 0.63, "EUR": 7.46, "SEK": 0.67}})
    kurser_local = indstillinger_local.get("kurser", {"NOK": 0.63, "EUR": 7.46, "SEK": 0.67})

    # Hent Dinero-matchede fakturaer pr. projekt for at få korrekt faktureret-sum
    dinero_fakt_dkk = {}  # PROJ-XXX → sum DKK
    if DINERO_OK:
        try:
            pmap = _projekt_map(all_tilbud)
            kendte = set()
            for t in all_tilbud.values():
                for f in t.get('fakturaer', []):
                    if f.get('dinero_guid'):
                        kendte.add(f['dinero_guid'])
            invs = dinero_api.fetch_invoices(from_date=f'{idag.year}-01-01')
            for i in invs:
                if i['guid'] in kendte:
                    continue
                kode = i.get('project_code') or _match_paa_navn(i.get('description', ''), pmap)
                if kode and kode.startswith('PROJ-'):
                    bdkk = til_dkk(_f(i.get('total_incl_vat')), i.get('currency', 'DKK'), kurser_local)
                    dinero_fakt_dkk[kode] = dinero_fakt_dkk.get(kode, 0) + bdkk
        except Exception:
            pass

    for t in all_tilbud.values():
        if t.get('vundet') is not True or t.get('slettet') or t.get('arkiveret'):
            continue

        valuta   = t.get('valuta', 'NOK')
        betaling = t.get('betaling', '5050')

        total = sum(
            _f(p.get('antal', 1), 1) * _f(p.get('pris', 0))
            for p in t.get('produkter', [])
        )
        if total <= 0:
            continue
        # Tilbudssum konverteres til DKK for sammenligning på tværs af valutaer
        total_dkk = til_dkk(total, valuta, kurser_local)

        # Faktureret = lokale fakturaer (egen valuta pr stk) + Dinero-matchede (allerede DKK)
        faktureret_dkk = sum(
            til_dkk(_f(f.get('beloeb')), f.get('valuta') or valuta, kurser_local)
            for f in t.get('fakturaer', [])
        )
        proj_kode = f"PROJ-{t.get('nummer', 0):03d}"
        faktureret_dkk += dinero_fakt_dkk.get(proj_kode, 0)

        # Brug DKK-værdier til rate-sammenligning
        total      = total_dkk
        faktureret = faktureret_dkk

        # Definer rater: (label, andel, dage-til-forfald)
        if betaling == '5050':
            rater = [
                ("Rate 1 – 50% (ordrebek.)", 0.50, 0),
                ("Rate 2 – 50% (levering)",  0.50, 999),
            ]
        else:  # 404020
            rater = [
                ("Rate 1 – 40% (ordrebek.)",  0.40, 0),
                ("Rate 2 – 40% (levering)",   0.40, 999),
                ("Rate 3 – 20% (afslutning)", 0.20, 999),
            ]

        # Vis rater der endnu ikke er dækket af faktureret beløb (alt i DKK)
        # rate_beloeb til visning beregnes i original valuta så det ligner tilbuddet
        total_orig = sum(
            _f(p.get('antal', 1), 1) * _f(p.get('pris', 0))
            for p in t.get('produkter', [])
        )
        akkumuleret_dkk = 0.0
        for rate_label, rate_pct, rate_dage in rater:
            rate_beloeb_orig = round(total_orig * rate_pct, 2)
            akkumuleret_dkk += round(total * rate_pct, 2)  # total er DKK her
            if faktureret < akkumuleret_dkk - 0.01:
                opgaver.append({
                    'titel':   f"#{t.get('nummer')} {t.get('kunde', '')} – {rate_label}",
                    'beloeb':  rate_beloeb_orig,
                    'valuta':  valuta,
                    'forfald': '',
                    'dage':    rate_dage,
                    'url':     '/admin',
                    'ikon':    'fa-file-text-o',
                    'farve':   '#1e325a',
                })

    opgaver.sort(key=lambda x: x['dage'])
    return opgaver


@app.route('/faktura/tilfoej/<tilbud_id>', methods=['POST'])
def faktura_tilfoej(tilbud_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    if tilbud_id not in all_data:
        return "Tilbud ikke fundet", 404

    t = all_data[tilbud_id]
    if 'fakturaer' not in t:
        t['fakturaer'] = []

    dato_raw = request.form.get('dato', '')
    try:
        dato     = datetime.strptime(dato_raw, '%Y-%m-%d').strftime('%d-%m-%Y')
        dato_iso = datetime.strptime(dato_raw, '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:
        dato     = datetime.now().strftime('%d-%m-%Y')
        dato_iso = datetime.now().strftime('%Y-%m-%d')

    beloeb      = float(request.form.get('beloeb', 0) or 0)
    beskrivelse = request.form.get('beskrivelse', '')
    push_dinero = request.form.get('push_dinero') == 'on'
    # Valuta kan overskrives pr. faktura (fx UNO-X projekter i NOK der faktureres DKK)
    fak_valuta  = request.form.get('valuta', '').strip() or t.get('valuta', 'DKK')

    faktura = {
        "dato":        dato,
        "beskrivelse": beskrivelse,
        "beloeb":      beloeb,
        "valuta":      fak_valuta,
    }

    # Auto-push til Dinero som kladde
    if push_dinero and DINERO_OK and beloeb > 0:
        try:
            linjer = [{
                "beskrivelse": beskrivelse or f"Faktura til {t.get('kunde', '')}",
                "antal":       1,
                "enhedspris":  beloeb,
            }]
            site_str = f" · {t.get('site').strip()}" if t.get('site') else ''
            titel    = f"#{t.get('nummer')} {t.get('kunde','')}{site_str} — {beskrivelse}"
            komment  = f"{titel}\n\nJf. vores ordrebekræftelse nr. {t.get('nummer')}"
            # Moms: altid 'nej' når fakturaen er i udenlandsk valuta (eksport)
            moms_faktura = 'nej' if fak_valuta != 'DKK' else t.get('moms', 'ja')
            guid, ts = dinero_api.opret_faktura(
                kunde_navn=t.get('kunde', ''),
                linjer=linjer,
                dato=dato_iso,
                valuta=fak_valuta,
                moms=moms_faktura,
                beskrivelse=titel,
                kommentar=komment,
                kategori=_dinero_kategori(beskrivelse, t.get('kunde', '')),
            )
            faktura["dinero_guid"]      = guid
            faktura["dinero_timestamp"] = ts
            faktura["dinero_status"]    = "Draft"
        except Exception as e:
            faktura["dinero_fejl"] = str(e)[:200]

    t['fakturaer'].append(faktura)

    save_data(TILBUD_FILE, all_data)
    return redirect(url_for('admin_panel'))


@app.route('/faktura/slet/<tilbud_id>/<int:index>')
def faktura_slet(tilbud_id, index):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    if tilbud_id in all_data:
        fakturaer = all_data[tilbud_id].get('fakturaer', [])
        if 0 <= index < len(fakturaer):
            fakturaer.pop(index)
        save_data(TILBUD_FILE, all_data)

    if request.args.get('redir') == 'projekt':
        return redirect(url_for('projekt_side', tilbud_id=tilbud_id))
    return redirect(url_for('admin_panel'))


@app.route('/admin/indstillinger', methods=['POST'])
def save_indstillinger():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    try:
        nok_kurs = float(request.form.get('nok_kurs', 0.63))
        eur_kurs = float(request.form.get('eur_kurs', 7.46))
        sek_kurs = float(request.form.get('sek_kurs', 0.67))
        usd_kurs = float(request.form.get('usd_kurs', 6.85))
    except (ValueError, TypeError):
        nok_kurs, eur_kurs, sek_kurs, usd_kurs = 0.63, 7.46, 0.67, 6.85

    save_data(INDSTILLINGER_FILE, {"kurser": {"NOK": nok_kurs, "EUR": eur_kurs,
                                               "SEK": sek_kurs, "USD": usd_kurs}})
    return redirect(url_for('manage_products', fane='indstillinger'))


@app.route('/malte')
def malte():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    data = load_data(MALTE_FILE, _malte_default())
    ind = data['indstillinger']
    idag = datetime.now()

    retainer_beloeb = ind['timer'] * ind['timepris']
    maaned_key = idag.strftime('%Y-%m')
    maaned_slut_dato = _maaned_slut(idag.year, idag.month)
    retainer_nu_faktureret = maaned_key in data.get('retainer_faktureret', {})

    kvartaler = _malte_oversigt(data)

    retainer_historik = sorted(data.get('retainer_faktureret', {}).items(), reverse=True)[:6]

    return render_template('malte.html',
                           ind=ind,
                           salg=list(reversed(data.get('salg', []))),
                           kvartaler=kvartaler,
                           retainer_beloeb=retainer_beloeb,
                           retainer_nu_faktureret=retainer_nu_faktureret,
                           maaned_key=maaned_key,
                           maaned_slut_dato=maaned_slut_dato,
                           retainer_historik=retainer_historik,
                           now_date=idag.strftime('%Y-%m-%d'),
                           idag=idag)


@app.route('/malte/salg/tilfoej', methods=['POST'])
def malte_salg_tilfoej():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    data = load_data(MALTE_FILE, _malte_default())
    try:
        antal     = float(request.form.get('antal', 1))
        enhedspris = float(request.form.get('enhedspris', 0))
        prov_pct  = float(request.form.get('provision_pct', data['indstillinger']['provision_pct']))
    except ValueError:
        return redirect(url_for('malte'))

    dato_raw = request.form.get('dato', '')
    try:
        dato = datetime.strptime(dato_raw, '%Y-%m-%d').strftime('%d-%m-%Y')
    except ValueError:
        dato = datetime.now().strftime('%d-%m-%Y')

    data['salg'].append({
        'id':           str(uuid.uuid4()),
        'dato':         dato,
        'beskrivelse':  request.form.get('beskrivelse', ''),
        'antal':        antal,
        'enhedspris':   enhedspris,
        'provision_pct': prov_pct,
        'valuta':       data['indstillinger']['valuta'],
    })
    save_data(MALTE_FILE, data)
    return redirect(url_for('malte'))


@app.route('/malte/salg/slet/<salg_id>')
def malte_salg_slet(salg_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    data = load_data(MALTE_FILE, _malte_default())
    data['salg'] = [s for s in data['salg'] if s.get('id') != salg_id]
    save_data(MALTE_FILE, data)
    return redirect(url_for('malte'))


@app.route('/malte/provision/markering', methods=['POST'])
def malte_provision_markering():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    data = load_data(MALTE_FILE, _malte_default())
    kvartal = request.form.get('kvartal')
    handling = request.form.get('handling', 'marker')

    if kvartal:
        if handling == 'marker':
            data.setdefault('provision_faktureret', {})[kvartal] = datetime.now().strftime('%d-%m-%Y')
        else:
            data.get('provision_faktureret', {}).pop(kvartal, None)
        save_data(MALTE_FILE, data)
    return redirect(url_for('malte'))


@app.route('/malte/retainer/markering', methods=['POST'])
def malte_retainer_markering():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    data = load_data(MALTE_FILE, _malte_default())
    maaned = request.form.get('maaned')
    handling = request.form.get('handling', 'marker')

    if maaned:
        if handling == 'marker':
            data.setdefault('retainer_faktureret', {})[maaned] = datetime.now().strftime('%d-%m-%Y')
        else:
            data.get('retainer_faktureret', {}).pop(maaned, None)
        save_data(MALTE_FILE, data)
    return redirect(url_for('malte'))


@app.route('/malte/indstillinger', methods=['POST'])
def malte_indstillinger():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    data = load_data(MALTE_FILE, _malte_default())
    try:
        data['indstillinger']['timer']        = float(request.form.get('timer', 20))
        data['indstillinger']['timepris']     = float(request.form.get('timepris', 700))
        data['indstillinger']['provision_pct'] = float(request.form.get('provision_pct', 5))
    except ValueError:
        pass
    save_data(MALTE_FILE, data)
    return redirect(url_for('malte'))


@app.route('/unox')
def unox():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    data = load_data(UNOX_FILE, _unox_default())
    idag = datetime.now()
    uind = data['indstillinger']
    perioder = _unox_perioder(uind.get('start_dato', '02-01-2026'), idag, data.get('perioder_faktureret', {}))

    return render_template('unox.html',
                           ind=uind,
                           perioder=perioder,
                           indkoeb=data.get('indkoeb', []),
                           now_date=idag.strftime('%Y-%m-%d'),
                           idag=idag)


@app.route('/unox/indstillinger', methods=['POST'])
def unox_indstillinger():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    data = load_data(UNOX_FILE, _unox_default())
    try:
        data['indstillinger']['timer_pr_periode'] = float(request.form.get('timer_pr_periode', 74))
        data['indstillinger']['timepris'] = float(request.form.get('timepris', 0))
    except ValueError:
        pass

    dato_raw = request.form.get('start_dato', '')
    try:
        data['indstillinger']['start_dato'] = datetime.strptime(dato_raw, '%Y-%m-%d').strftime('%d-%m-%Y')
    except ValueError:
        pass

    save_data(UNOX_FILE, data)
    return redirect(url_for('unox'))


@app.route('/unox/periode/marker', methods=['POST'])
def unox_periode_marker():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    data = load_data(UNOX_FILE, _unox_default())
    key = request.form.get('key')
    handling = request.form.get('handling', 'marker')

    if key:
        if handling == 'marker':
            data.setdefault('perioder_faktureret', {})[key] = datetime.now().strftime('%d-%m-%Y')
        else:
            data.get('perioder_faktureret', {}).pop(key, None)
        save_data(UNOX_FILE, data)
    return redirect(url_for('unox'))


@app.route('/unox/indkoeb/tilfoej', methods=['POST'])
def unox_indkoeb_tilfoej():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    data = load_data(UNOX_FILE, _unox_default())
    try:
        beloeb = float(request.form.get('beloeb', 0))
    except ValueError:
        beloeb = 0.0

    dato_raw = request.form.get('dato', '')
    try:
        dato = datetime.strptime(dato_raw, '%Y-%m-%d').strftime('%d-%m-%Y')
    except ValueError:
        dato = datetime.now().strftime('%d-%m-%Y')

    data['indkoeb'].append({
        'id': str(uuid.uuid4()),
        'dato': dato,
        'beskrivelse': request.form.get('beskrivelse', ''),
        'beloeb': beloeb,
        'faktureret': False,
    })
    save_data(UNOX_FILE, data)
    return redirect(url_for('unox'))


@app.route('/unox/indkoeb/slet/<indkoeb_id>')
def unox_indkoeb_slet(indkoeb_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    data = load_data(UNOX_FILE, _unox_default())
    data['indkoeb'] = [k for k in data['indkoeb'] if k.get('id') != indkoeb_id]
    save_data(UNOX_FILE, data)
    return redirect(url_for('unox'))


@app.route('/unox/indkoeb/marker/<indkoeb_id>')
def unox_indkoeb_marker(indkoeb_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    data = load_data(UNOX_FILE, _unox_default())
    for k in data['indkoeb']:
        if k.get('id') == indkoeb_id:
            k['faktureret'] = not k.get('faktureret', False)
            if k['faktureret']:
                k['faktureret_dato'] = datetime.now().strftime('%d-%m-%Y')
            else:
                k.pop('faktureret_dato', None)
            break
    save_data(UNOX_FILE, data)
    return redirect(url_for('unox'))


if __name__ == '__main__':
    app.run(port=5005, debug=True)
