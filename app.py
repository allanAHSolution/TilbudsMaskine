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


def beregn_statistik_opdelt(all_data, malte_data, unox_data, kurser, aar):
    """Returnerer omsætning opdelt i produkter/timer/kommission per måned."""
    result = {m: {"produkter": 0.0, "timer": 0.0, "kommission": 0.0} for m in MAANEDER}

    # ── Produkter: vundne tilbud ──
    for t in all_data.values():
        if t.get('vundet') is True and not t.get('slettet'):
            try:
                dato = datetime.strptime(t['dato'], '%d-%m-%Y')
                if dato.year == aar:
                    mnd = MAANEDER[dato.month - 1]
                    valuta = t.get('valuta', 'NOK')
                    for p in t.get('produkter', []):
                        beloeb = float(p.get('antal', 1)) * float(p.get('pris', 0))
                        result[mnd]['produkter'] += til_dkk(beloeb, valuta, kurser)
            except Exception:
                pass

    # ── Timer: Malte retainer (fakturerede måneder) ──
    mind = malte_data.get('indstillinger', {})
    retainer_beloeb = mind.get('timer', 0) * mind.get('timepris', 0)
    malte_valuta = mind.get('valuta', 'SEK')
    for maaned_key in malte_data.get('retainer_faktureret', {}):
        try:
            dato = datetime.strptime(maaned_key, '%Y-%m')
            if dato.year == aar:
                result[MAANEDER[dato.month - 1]]['timer'] += til_dkk(retainer_beloeb, malte_valuta, kurser)
        except Exception:
            pass

    # ── Timer: UNO-X perioder (fakturerede) ──
    uind = unox_data.get('indstillinger', {})
    unox_beloeb = uind.get('timer_pr_periode', 74) * uind.get('timepris', 0)
    unox_valuta = uind.get('valuta', 'DKK')
    for key in unox_data.get('perioder_faktureret', {}):
        try:
            dato = datetime.strptime(key, '%Y-%m-%d')
            if dato.year == aar:
                result[MAANEDER[dato.month - 1]]['timer'] += til_dkk(unox_beloeb, unox_valuta, kurser)
        except Exception:
            pass

    # ── Kommission: Malte provision (fakturerede kvartaler) ──
    for kv in _malte_oversigt(malte_data).values():
        if kv.get('faktureret') and kv.get('slut') and kv['slut'].year == aar:
            mnd = MAANEDER[kv['slut'].month - 1]
            result[mnd]['kommission'] += til_dkk(kv['total_provision'], malte_valuta, kurser)

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
    pdf.set_fill_color(*BLUE)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('DejaVu', 'B', 9)
    pdf.cell(88, 7, 'BESKRIVELSE', fill=True)
    pdf.cell(22, 7, 'ANTAL', fill=True, align='C')
    pdf.cell(40, 7, 'ENHEDSPRIS', fill=True, align='R')
    pdf.cell(30, 7, 'TOTAL', fill=True, align='R')
    pdf.ln(7)

    # Produktlinjer
    valuta = tilbud.get('valuta', 'NOK')
    total_sum = 0
    for i, p in enumerate(tilbud.get('produkter', [])):
        try:
            antal = float(p.get('antal', 1))
            pris = float(p.get('pris', 0))
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
        pdf.cell(40, 6, f"{pris:,.0f} {valuta}", fill=fill, align='R')
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
    """Tjek alle fakturaer der er pushet til Dinero, og opdater status (især betaling)."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    if not DINERO_OK:
        return redirect(url_for('admin_panel'))

    all_data = load_data(TILBUD_FILE, {})
    opdateret = 0
    fejl      = 0
    for t in all_data.values():
        for f in t.get('fakturaer', []):
            guid = f.get('dinero_guid')
            if not guid or f.get('dinero_status') == 'Paid':
                continue
            try:
                info = dinero_api.hent_faktura_status(guid)
                ps   = info.get('PaymentStatus') or info.get('paymentStatus') or ''
                f['dinero_status']    = ps if ps else (f.get('dinero_status') or 'Draft')
                f['dinero_timestamp'] = info.get('TimeStamp') or info.get('timeStamp') or f.get('dinero_timestamp')
                opdateret += 1
            except Exception as e:
                f['dinero_fejl'] = str(e)[:200]
                fejl += 1
    save_data(TILBUD_FILE, all_data)
    return f"✓ Opdateret {opdateret} fakturaer ({fejl} fejl). <a href='/admin'>Tilbage</a>"


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
    statistik_opdelt = beregn_statistik_opdelt(all_data, malte_data, unox_data, kurser, aar)
    max_val = max((sum(v.values()) for v in statistik_opdelt.values()), default=1) or 1
    now_date = idag.strftime('%Y-%m-%d')

    # Summariske tal til stat-kort
    afventer_total = sum(
        sum(float(p.get('antal', 1)) * float(p.get('pris', 0)) for p in t.get('produkter', []))
        for t in afventer.values()
    )
    vundne_ufaktureret = sum(
        max(0, sum(float(p.get('antal', 1)) * float(p.get('pris', 0)) for p in t.get('produkter', []))
               - sum(float(f.get('beloeb', 0)) for f in t.get('fakturaer', [])))
        for t in vundne.values()
    )
    faktureringsopgaver = _beregn_faktureringsopgaver(idag)

    return render_template('index.html',
                           produkter=load_data(PRODUKTER_FILE, []),
                           afventer=afventer,
                           vundne=vundne,
                           tabte=tabte,
                           arkiverede=arkiverede,
                           statistik_opdelt=statistik_opdelt,
                           max_val=max_val,
                           aar=aar,
                           kurser=kurser,
                           now_date=now_date,
                           afventer_total=afventer_total,
                           vundne_ufaktureret=vundne_ufaktureret,
                           faktureringsopgaver=faktureringsopgaver)


@app.route('/admin/produkter', methods=['GET', 'POST'])
def manage_products():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    products = load_data(PRODUKTER_FILE, [])
    leverandoerer = load_data(LEVERANDOERER_FILE, [])
    indstillinger = load_data(INDSTILLINGER_FILE, {"kurser": {"NOK": 0.63, "EUR": 7.46}})
    kurser = indstillinger.get("kurser", {"NOK": 0.63, "EUR": 7.46})

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            products.append({
                "navn": request.form.get('navn'),
                "pris": float(request.form.get('pris', 0)),
                "enhed": request.form.get('enhed', ''),
                "leverandoer": request.form.get('leverandoer', '')
            })
        elif action == 'delete':
            index = int(request.form.get('index'))
            products.pop(index)
        elif action == 'edit':
            index = int(request.form.get('index'))
            products[index] = {
                "navn": request.form.get('navn'),
                "pris": float(request.form.get('pris', 0)),
                "enhed": request.form.get('enhed', ''),
                "leverandoer": request.form.get('leverandoer', '')
            }
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
    navne = request.form.getlist('p_navn')
    antal_list = request.form.getlist('p_antal')
    priser = request.form.getlist('p_pris')
    beskrivelser = request.form.getlist('p_beskrivelse')

    produkter = []
    for i, navn in enumerate(navne):
        if navn.strip():
            produkter.append({
                "navn": navn,
                "antal": antal_list[i] if i < len(antal_list) else "1",
                "pris": priser[i] if i < len(priser) else "0",
                "beskrivelse": beskrivelser[i] if i < len(beskrivelser) else ""
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
        # Nyt tilbud
        tilbud_id = str(uuid.uuid4())
        nummer = get_next_nummer()
        tilbud = {
            "id": tilbud_id,
            "nummer": nummer,
            "dato": datetime.now().strftime('%d-%m-%Y'),
            "arkiveret": False,
            "vundet": None,
            "slettet": False,
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


@app.route('/status/<tilbud_id>/<status>')
def set_status(tilbud_id, status):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    all_data = load_data(TILBUD_FILE, {})
    if tilbud_id in all_data:
        if status == 'vundet':
            all_data[tilbud_id]['vundet'] = True
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


@app.route('/nyt-tilbud')
def nyt_tilbud():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    rediger_id = request.args.get('rediger')
    all_data = load_data(TILBUD_FILE, {})
    rediger_data = all_data.get(rediger_id) if rediger_id else None
    indstillinger = load_data(INDSTILLINGER_FILE, {"kurser": {"NOK": 0.63, "EUR": 7.46, "SEK": 0.67}})
    kurser = indstillinger.get("kurser", {"NOK": 0.63, "EUR": 7.46, "SEK": 0.67})

    return render_template('nyt_tilbud.html',
                           produkter=load_data(PRODUKTER_FILE, []),
                           rediger_data=rediger_data,
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
        total = float(s.get('antal', 1)) * float(s.get('enhedspris', 0))
        prov = total * float(s.get('provision_pct', 5)) / 100
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
    for t in all_tilbud.values():
        if t.get('vundet') is not True or t.get('slettet') or t.get('arkiveret'):
            continue

        total = sum(
            float(p.get('antal', 1)) * float(p.get('pris', 0))
            for p in t.get('produkter', [])
        )
        if total <= 0:
            continue

        faktureret = sum(float(f.get('beloeb', 0)) for f in t.get('fakturaer', []))
        valuta = t.get('valuta', 'NOK')
        betaling = t.get('betaling', '5050')

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

        # Vis rater der endnu ikke er dækket af faktureret beløb
        akkumuleret = 0.0
        for rate_label, rate_pct, rate_dage in rater:
            rate_beloeb = round(total * rate_pct, 2)
            akkumuleret += rate_beloeb
            if faktureret < akkumuleret - 0.01:
                opgaver.append({
                    'titel':   f"#{t.get('nummer')} {t.get('kunde', '')} – {rate_label}",
                    'beloeb':  rate_beloeb,
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

    faktura = {
        "dato":        dato,
        "beskrivelse": beskrivelse,
        "beloeb":      beloeb,
    }

    # Auto-push til Dinero som kladde
    if push_dinero and DINERO_OK and beloeb > 0:
        try:
            linjer = [{
                "beskrivelse": beskrivelse or f"Faktura til {t.get('kunde', '')}",
                "antal":       1,
                "enhedspris":  beloeb,
            }]
            guid, ts = dinero_api.opret_faktura(
                kunde_navn=t.get('kunde', ''),
                linjer=linjer,
                dato=dato_iso,
                valuta=t.get('valuta', 'DKK'),
                kommentar=f"Tilbud #{t.get('nummer')} — {beskrivelse}",
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
    return redirect(url_for('admin_panel'))


@app.route('/admin/indstillinger', methods=['POST'])
def save_indstillinger():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    try:
        nok_kurs = float(request.form.get('nok_kurs', 0.63))
        eur_kurs = float(request.form.get('eur_kurs', 7.46))
        sek_kurs = float(request.form.get('sek_kurs', 0.67))
    except (ValueError, TypeError):
        nok_kurs, eur_kurs, sek_kurs = 0.63, 7.46, 0.67

    save_data(INDSTILLINGER_FILE, {"kurser": {"NOK": nok_kurs, "EUR": eur_kurs, "SEK": sek_kurs}})
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
