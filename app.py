from flask import Flask, render_template, request, send_file, redirect, url_for, session
import json
import os
import uuid
from datetime import datetime
from fpdf import FPDF

app = Flask(__name__)
app.secret_key = "ahsolution_secret_2026"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUKTER_FILE     = os.path.join(BASE_DIR, 'produkter.json')
TILBUD_FILE        = os.path.join(BASE_DIR, 'tilbud_arkiv.json')
NUMMER_FILE        = os.path.join(BASE_DIR, 'nummer.txt')
LEVERANDOERER_FILE = os.path.join(BASE_DIR, 'leverandoerer.json')

ADMIN_USER = "allan"
ADMIN_PASS = "ahsolution2026-"

MAANEDER = ["Jan", "Feb", "Mar", "Apr", "Maj", "Jun",
            "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]


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


def beregn_statistik(all_data):
    statistik = {m: 0 for m in MAANEDER}
    aar = datetime.now().year
    for t in all_data.values():
        if t.get('vundet') is True and not t.get('slettet'):
            try:
                dato = datetime.strptime(t['dato'], '%d-%m-%Y')
                if dato.year == aar:
                    mnd = MAANEDER[dato.month - 1]
                    for p in t.get('produkter', []):
                        statistik[mnd] += float(p.get('antal', 1)) * float(p.get('pris', 0))
            except Exception:
                pass
    return statistik


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


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form['username'] == ADMIN_USER and request.form['password'] == ADMIN_PASS:
            session['logged_in'] = True
            return redirect(url_for('admin_panel'))
        error = "Forkert brugernavn eller adgangskode"
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('forside'))


@app.route('/admin')
def admin_panel():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    rediger_id = request.args.get('rediger')
    all_data = load_data(TILBUD_FILE, {})
    rediger_data = all_data.get(rediger_id) if rediger_id else None

    aktive = {k: v for k, v in all_data.items() if not v.get('arkiveret') and not v.get('slettet')}
    arkiverede = {k: v for k, v in all_data.items() if v.get('arkiveret') and not v.get('slettet')}

    statistik = beregn_statistik(all_data)
    max_val = max(statistik.values()) if any(statistik.values()) else 1
    aar = datetime.now().year

    return render_template('index.html',
                           produkter=load_data(PRODUKTER_FILE, []),
                           aktive=aktive,
                           arkiverede=arkiverede,
                           rediger_data=rediger_data,
                           statistik=statistik,
                           max_val=max_val,
                           aar=aar)


@app.route('/admin/produkter', methods=['GET', 'POST'])
def manage_products():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    products = load_data(PRODUKTER_FILE, [])
    leverandoerer = load_data(LEVERANDOERER_FILE, [])

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
                           aktiv_fane=request.args.get('fane', 'produkter'))


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


if __name__ == '__main__':
    app.run(port=5000, debug=True)
