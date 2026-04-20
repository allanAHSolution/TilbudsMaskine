"""
Sikker merge af tilbud_arkiv_lokal.json → tilbud_arkiv.json på prod.

Regler:
 - Eksisterende tilbud (samme 'nummer') på prod bevares UÆNDRET
 - Nye tilbud fra local (numre prod ikke kender) tilføjes
 - Hvis et eksisterende tilbud mangler 'projekt'-feltet, tilføjes det
   (uden at røre andre felter)
 - nummer.txt sættes til max(prod, local) så nye tilbud får korrekt nr

Kør med --dry-run først for at se hvad der sker.

Brug:
    python3 merge_tilbud.py --dry-run     # vis plan
    python3 merge_tilbud.py               # udfør (backup oprettes automatisk)
"""

import json
import os
import shutil
import sys
from datetime import datetime

PROD_FILE   = 'tilbud_arkiv.json'
LOCAL_FILE  = 'tilbud_arkiv_lokal.json'
NUMMER_FILE = 'nummer.txt'
LOCAL_NUM   = 'nummer_lokal.txt'


def main():
    dry_run = '--dry-run' in sys.argv

    if not os.path.exists(LOCAL_FILE):
        print(f"FEJL: {LOCAL_FILE} findes ikke. scp den fra lokal først.")
        sys.exit(1)

    prod  = json.load(open(PROD_FILE))  if os.path.exists(PROD_FILE)  else {}
    local = json.load(open(LOCAL_FILE))

    prod_by_nr  = {t.get('nummer'): (tid, t) for tid, t in prod.items()}
    local_by_nr = {t.get('nummer'): (tid, t) for tid, t in local.items()}

    tilfoejes = []        # helt nye tilbud
    bevares   = []        # prod-versionen beholdes uændret
    projekt_tilfoejes = [] # eksisterende prod-tilbud får 'projekt'-felt

    for nr, (lid, lt) in local_by_nr.items():
        if nr in prod_by_nr:
            pid, pt = prod_by_nr[nr]
            if 'projekt' not in pt and 'projekt' in lt:
                projekt_tilfoejes.append((pid, pt, lt.get('projekt')))
            else:
                bevares.append((nr, pt.get('kunde', '')))
        else:
            tilfoejes.append((lid, lt))

    print("=== MERGE-PLAN ===\n")
    print(f"{len(tilfoejes)} nye tilbud tilføjes:")
    for tid, t in tilfoejes:
        nr = t.get('nummer')
        print(f"  + #{nr} {t.get('kunde','')}"
              f"{' · ' + t['site'] if t.get('site') else ''}")

    print(f"\n{len(bevares)} prod-tilbud bevares uændret:")
    for nr, k in sorted(bevares):
        print(f"  = #{nr} {k}")

    print(f"\n{len(projekt_tilfoejes)} eksisterende får nyt 'projekt'-felt:")
    for pid, pt, proj in projekt_tilfoejes:
        print(f"  ~ #{pt.get('nummer')} {pt.get('kunde','')}")

    # Ny nummer-værdi
    nummer_prod  = int(open(NUMMER_FILE).read().strip())  if os.path.exists(NUMMER_FILE) else 1
    nummer_local = int(open(LOCAL_NUM).read().strip())    if os.path.exists(LOCAL_NUM)   else nummer_prod
    nyt_nummer   = max(nummer_prod, nummer_local)
    print(f"\nnummer.txt: {nummer_prod} → {nyt_nummer}")

    if dry_run:
        print("\n(--dry-run: intet skrevet. Kør uden flag for at udføre.)")
        return

    # BACKUP
    if os.path.exists(PROD_FILE):
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup = f"{PROD_FILE}.backup.{ts}"
        shutil.copy(PROD_FILE, backup)
        print(f"\n✓ Backup: {backup}")

    # UDFØR MERGE
    for tid, t in tilfoejes:
        prod[tid] = t
    for pid, pt, proj in projekt_tilfoejes:
        prod[pid]['projekt'] = proj

    with open(PROD_FILE, 'w') as f:
        json.dump(prod, f, indent=2, ensure_ascii=False)
    with open(NUMMER_FILE, 'w') as f:
        f.write(str(nyt_nummer))

    # Ryd op
    os.remove(LOCAL_FILE)
    if os.path.exists(LOCAL_NUM):
        os.remove(LOCAL_NUM)

    print("✓ Merge færdig. Husk at reloade web-app.")


if __name__ == '__main__':
    main()
