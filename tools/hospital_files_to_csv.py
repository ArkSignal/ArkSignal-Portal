"""Turn the two Arkansas hospital files into CSVs for Admin > Hospitals > Import Hospital Data.

  python tools/hospital_files_to_csv.py emresource "Arkansas - EMResource.pdf" out.csv
      Reads the EMResource region screen (a PDF print of the Bed Reporting table) by text position and writes name, city, state, services, source.
      A capability counts when its cell says anything except "Not Provided" or "No Capability" (Open, Delay, ICU Max ...): "Not Provided" means nobody
      reported it, so it is never read as "does not have it". Real-time bed status is dropped; only what a hospital can do is kept.
  python tools/hospital_files_to_csv.py trauma designated.csv out.csv
      The state's Designated Trauma Centers PDF is laid out in two scrambled columns, so its rows are typed into a small CSV (name;city;state;level;date),
      one line per hospital, and this command only adds the source column. See arkansas_trauma_designations_2026-09-09.csv for the format.

Needs `pip install pymupdf` for the emresource command. The portal itself reads only CSV, so nothing here ships in the image."""
import csv
import sys

# EMResource's short names -> (name as the state list writes it, city, state); a row not listed here is skipped and reported
NAMES = {
    "Chambers Memorial Hosp": ("Chambers Memorial Hospital", "Danville", "AR"),
    "Dardanelle Regional Medical Center": ("Dardanelle Regional Medical Center", "Dardanelle", "AR"),
    "Johnson Reg Med Cntr": ("Johnson Regional Medical Center", "Clarksville", "AR"),
    "Mena Reg Health System": ("Mena Regional Health System", "Mena", "AR"),
    "Mercy Hosp - Booneville": ("Mercy Hospital - Booneville", "Booneville", "AR"),
    "Mercy Hosp - Fort Smith": ("Mercy Hospital - Fort Smith", "Fort Smith", "AR"),
    "Mercy Hosp - Ozark": ("Mercy Hospital - Ozark", "Ozark", "AR"),
    "Mercy Hosp - Paris": ("Mercy Hospital - Paris", "Paris", "AR"),
    "Mercy Hosp - Waldron": ("Mercy Hospital - Waldron", "Waldron", "AR"),
    "St Marys Reg Med Cntr": ("Saint Mary's Regional Hospital", "Russellville", "AR"),
    "Arkansas Childrens Hosp": ("Arkansas Children's Hospital", "Little Rock", "AR"),
    "Baptist Health - Conway": ("Baptist Health Medical Center - Conway", "Conway", "AR"),
    "Baptist Health - LR": ("Baptist Health Medical Center", "Little Rock", "AR"),
    "Baptist Health - NLR": ("Baptist Health Medical Center - North Little Rock", "North Little Rock", "AR"),
    "CHI St Vincent - LR": ("CHI St. Vincent Infirmary", "Little Rock", "AR"),
    "CHI St Vincent - Morrilton": ("CHI St. Vincent Morrilton", "Morrilton", "AR"),
    "CHI St Vincent - Sherwood": ("CHI St. Vincent North", "Sherwood", "AR"),
    "Conway Reg Med Cntr": ("Conway Regional Medical Center", "Conway", "AR"),
    "Saline Memorial Hospital": ("Saline Memorial Hospital", "Benton", "AR"),
    "UAMS": ("University of Arkansas for Medical Sciences (UAMS)", "Little Rock", "AR"),
    "Baxter Health - Fulton Co Hosp": ("Baxter Health Fulton County Hospital", "Salem", "AR"),
    "Baxter Reg Med Cntr": ("Baxter Health", "Mountain Home", "AR"),
    "Ozark Health Med Cntr": ("Ozark Health Medical Center", "Clinton", "AR"),
    "Stone Co Med Cntr": ("Stone County Medical Center", "Mountain View", "AR"),
    "Unity Health - White Co Med Cntr": ("Unity Health White County Medical Center", "Searcy", "AR"),
    "White River Health System": ("White River Medical Center", "Batesville", "AR"),
    "Baptist Memorial Hospital - Paragould": ("Baptist Memorial Health - Paragould", "Paragould", "AR"),
    "Five Rivers Med Cntr": ("St. Bernards Five Rivers Medical Center", "Pocahontas", "AR"),
    "Great River Med Cntr": ("Great River Medical Center", "Blytheville", "AR"),
    "Le Bonheur Childrens Hosp": ("Le Bonheur Children's Hospital", "Memphis", "TN"),
    "NE Ark Baptist Memorial Hosp": ("NEA Baptist Memorial Hospital", "Jonesboro", "AR"),
    "Regional Health One": ("Regional One Health", "Memphis", "TN"),
    "SMC Reg Med Cntr": ("South Mississippi County Regional Medical Center", "Osceola", "AR"),
    "St Bernards Med Cntr": ("Saint Bernards Medical Center", "Jonesboro", "AR"),
    "Arkansas Children's Northwest": ("Arkansas Children's Hospital - Northwest", "Springdale", "AR"),
    "Cox South": ("Cox Medical Center South", "Springfield", "MO"),
    "Eureka Springs Hosp": ("Eureka Springs Hospital", "Eureka Springs", "AR"),
    "Freeman Health Bentonville Medical Center": ("Freeman Health Bentonville Medical Center", "Bentonville", "AR"),
    "Freeman Health Reg. Med. Ctr. – Springdale": ("Freeman Health Regional Medical Center", "Springdale", "AR"),
}
# CMS certification numbers where the CMS name differs too much from the state's name to match by name and city (an exact match, no guessing)
CCN = {"CoxHealth": "260040", "Cox Medical Center South": "260040", "Baptist Health Medical Center - NLR": "040036", "Baptist Health Medical Center - North Little Rock": "040036",
       "NEA Baptist Memorial Hospital": "040118", "Baptist Memorial Health - Paragould": "040039"}
# column header -> the service it shows (Not Provided and No Capability never count)
COLUMNS = {"Gen. Surgeon": "surgery", "ICU (Trauma)": "icu", "CT": "ct", "Ortho": "ortho", "Neuro-Head": "neuro", "Neuro-Spine": "neuro",
           "Peripheral Vasc.": "vascular", "Great Vessel": "vascular", "IR": "ir", "Isolated Facial": "facial", "Isolated Eye": "eye", "OB": "obstetric",
           "Adult Burn": "burn"}
NO = {"Not Provided", "No Capability", ""}


def emresource(pdf, out):
    import pymupdf
    page = pymupdf.open(pdf)[0]
    lines = {}
    for x0, y0, x1, y1, w, *_ in page.get_text("words"):
        lines.setdefault(round(y0 / 2), []).append((x0, x1, w))
    heads, rows, skipped = {}, [], []
    for k in sorted(lines):
        ph = []
        for x0, x1, w in sorted(lines[k]):
            if ph and x0 - ph[-1][1] < 4:
                ph[-1] = (ph[-1][0], x1, ph[-1][2] + " " + w)
            else:
                ph.append((x0, x1, w))
        texts = [t for _, _, t in ph]
        if "Trauma Center" in texts and "OR" in texts:
            heads = {t: x for x, _, t in ph}
            continue
        if not heads or len(ph) < 8 or ph[0][0] > 60:
            continue
        name = ph[0][2]
        cells = {}
        for x, _, t in ph[1:]:
            col = min(heads, key=lambda h: abs(heads[h] - x))
            cells[col] = t
        svc = []
        for col, tag in COLUMNS.items():
            if cells.get(col, "") not in NO and tag not in svc:
                svc.append(tag)
        if cells.get("Trauma Center", "").startswith("Pedi") and "pediatric" not in svc:
            svc.append("pediatric")
        if name not in NAMES:
            skipped.append(name)
            continue
        full, city, st = NAMES[name]
        rows.append([full, CCN.get(full, ""), city, st, ";".join(svc), "Arkansas EMResource capability screen, 7 Oct 2026"])
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "ccn", "city", "state", "services", "source"])
        w.writerows(rows)
    print(f"{len(rows)} hospitals written; skipped (no match in NAMES): {skipped}")


def trauma(src, out):
    rows = list(csv.DictReader(open(src, encoding="utf-8")))
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "ccn", "city", "state", "level", "designated", "source"])
        for r in rows:
            w.writerow([r["name"], CCN.get(r["name"], ""), r["city"], r["state"], r["level"], r["designated"], "Arkansas Department of Health, Designated Trauma Centers, updated Sept 9, 2026"])
    print(f"{len(rows)} hospitals written")


if __name__ == "__main__":
    cmd, a, b = sys.argv[1:4]
    {"emresource": emresource, "trauma": trauma}[cmd](a, b)
