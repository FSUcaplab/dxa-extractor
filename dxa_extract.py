"""Extract body composition and bone density results from Hologic DXA PDF reports.

Run it with no arguments and it asks for the folder (drag the folder into the
terminal window), or pass everything on the command line:

    python dxa_extract.py
    python dxa_extract.py "D:/Study/Participant Data"
    python dxa_extract.py "D:/Study/Participant Data" --ids 130 131 -o dxa.csv

The root folder holds one folder per participant. Inside each participant
folder, every subfolder whose name contains "DXA" or "DEXA" is one timepoint,
and the PDFs directly inside it are that timepoint's report pages.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

__version__ = "2.0.0"

COMPOSITION_REGIONS = ["L Arm", "R Arm", "Trunk", "L Leg", "R Leg", "Subtotal", "Head", "Total"]
FAT_DISTRIBUTION_REGIONS = ["Android (A)", "Gynoid (G)"]
BMD_REGIONS = ["L Arm", "R Arm", "L Ribs", "R Ribs", "T Spine", "L Spine", "Pelvis",
               "L Leg", "R Leg", "Subtotal", "Head", "Total"]
LIMBS_AND_TRUNK = ["L Arm", "R Arm", "Trunk", "L Leg", "R Leg"]
BMD_PARTS = ["L Arm", "R Arm", "L Ribs", "R Ribs", "T Spine", "L Spine", "Pelvis", "L Leg", "R Leg"]

COMPOSITION_COLUMNS = ["BMC_g", "Fat_Mass_g", "Lean_Mass_g", "Lean_plus_BMC_g", "Total_Mass_g", "Pct_Fat"]
MASS_COLUMNS = COMPOSITION_COLUMNS[:5]
SUMMARY_COLUMNS = ["Fat_Mass_g", "Lean_plus_BMC_g", "Total_Mass_g", "Pct_Fat"]
BMD_COLUMNS = ["Area_cm2", "BMC_g", "BMD_g_cm2"]


@dataclass(frozen=True)
class ReportTable:
    name: str
    header: re.Pattern
    regions: list[str]
    columns: list[str]
    rank: int  # when two tables report the same value, the higher rank wins


# One Hologic export prints the same numbers on several pages at different
# precision: the indices page rounds masses to whole grams, the composition
# page gives one decimal. Ranking the tables keeps the precise copy.
BMD_HEADER = re.compile(r"Region\s+Area\s+BMC\s+BMD")

TABLES = [
    ReportTable("composition",
                re.compile(r"Region\s+BMC\s+Fat\s+Lean\s+Lean\s*\+\s*Total\s+%\s*Fat"),
                COMPOSITION_REGIONS,
                COMPOSITION_COLUMNS,
                rank=3),
    ReportTable("bone density",
                BMD_HEADER,
                BMD_REGIONS,
                BMD_COLUMNS,
                rank=3),
    ReportTable("fat summary",
                re.compile(r"Region\s+Fat\s+Lean\s*\+\s*%\s*Fat"),
                COMPOSITION_REGIONS,
                ["Fat_Mass_g", "Lean_plus_BMC_g", "Pct_Fat"],
                rank=2),
    ReportTable("body composition results",
                re.compile(r"Body Composition Results"),
                COMPOSITION_REGIONS + FAT_DISTRIBUTION_REGIONS,
                SUMMARY_COLUMNS,
                rank=1),
]

INDICES = [
    ("Total_Body_Pct_Fat", r"Total Body % Fat"),
    ("Fat_Mass_Height2_kg_m2", r"Fat Mass/Height\S*\s*\(kg/m\S*\)"),
    ("Android_Gynoid_Ratio", r"Android/Gynoid Ratio"),
    ("Pct_Fat_Trunk_Legs_Ratio", r"% Fat Trunk/% Fat Legs"),
    ("Trunk_Limb_Fat_Ratio", r"Trunk/Limb Fat Mass Ratio"),
    ("VAT_Mass_g", r"Est\. VAT Mass \(g\)"),
    ("VAT_Volume_cm3", r"Est\. VAT Volume \(cm\S*\)"),
    ("VAT_Area_cm2", r"Est\. VAT Area \(cm\S*\)"),
    ("Lean_Height2_kg_m2", r"(?<!Appen\. )Lean/Height\S*\s*\(kg/m\S*\)"),
    ("Appen_Lean_Height2_kg_m2", r"Appen\. Lean/Height\S*\s*\(kg/m\S*\)"),
]

SCAN_ID = re.compile(r"Scan Date:.*?\bID:\s*(\S+)")

METADATA = [
    ("Sex", r"Sex:\s*([A-Za-z]+)"),
    ("Age", r"Age:\s*(\d+)"),
    ("Ethnicity", r"Ethnicity:\s*([A-Za-z]+)"),
    ("Height_in", r"Height:\s*(\d+(?:\.\d+)?)\s*in"),
    ("Weight_lb", r"Weight:\s*(\d+(?:\.\d+)?)\s*lb"),
    ("Scan_Date", r"Scan Date:\s*([A-Za-z]+ \d{1,2}, \d{4})"),
    ("Scan_ID", SCAN_ID.pattern),
    ("Analysis_Version", r"Version\s+(\d[\d.:]*)"),
]

NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
# Hologic marks a region it copied from the opposite side, because that limb
# fell outside the scan field, with a superscript: "L Arm¹".
FOOTNOTE_MARKS = "¹²³*†‡"
LB_TO_G = 453.59237


def _key(region: str, column: str) -> str:
    return f"{region.split(' (')[0].replace(' ', '_')}_{column}"


def _columns() -> list[str]:
    cols = ["Participant_ID", "Timepoint", "Scan_Date", "Scan_ID", "Sex", "Age",
            "Ethnicity", "Height_in", "Weight_lb"]
    for region in COMPOSITION_REGIONS:
        cols += [_key(region, c) for c in COMPOSITION_COLUMNS]
    for region in FAT_DISTRIBUTION_REGIONS:
        cols += [_key(region, c) for c in SUMMARY_COLUMNS]
    cols += [name for name, _ in INDICES]
    for region in BMD_REGIONS:
        for c in BMD_COLUMNS:
            if _key(region, c) not in cols:
                cols.append(_key(region, c))
    cols += ["Total_T_score", "Total_Z_score",
             "Analysis_Version", "Mirrored_Regions", "Warnings", "Source_Folder"]
    return cols


COLUMNS = _columns()


@dataclass
class Reading:
    value: float
    decimals: int
    rank: int
    source: str


@dataclass
class Scan:
    """Everything read from one timepoint's report pages."""
    metadata: dict[str, str] = field(default_factory=dict)
    readings: dict[str, list[Reading]] = field(default_factory=dict)
    mirrored: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    scan_ids: set[str] = field(default_factory=set)
    tables_found: set[str] = field(default_factory=set)

    def add(self, key: str, token: str, rank: int, source: str) -> None:
        decimals = len(token.split(".")[1]) if "." in token else 0
        self.readings.setdefault(key, []).append(Reading(float(token), decimals, rank, source))

    def best(self, key: str) -> Reading:
        return max(self.readings[key], key=lambda r: (r.rank, r.decimals))

    def values(self) -> dict[str, float]:
        """The most precise reading of each value."""
        return {key: self.best(key).value for key in self.readings}

    def disagreements(self) -> list[str]:
        """Values printed on two pages that differ by more than rounding."""
        out = []
        for key, readings in self.readings.items():
            best = self.best(key)
            for r in readings:
                tolerance = 0.5 * 10 ** -r.decimals + 0.5 * 10 ** -best.decimals + 1e-9
                if abs(r.value - best.value) > tolerance:
                    out.append(f"{key}: {best.source} reads {best.value:g} but {r.source} reads {r.value:g}")
        return out


def total_scores(page) -> dict[str, str]:
    """The bone density Total row's T- and Z-score, placed by column position.

    Hologic leaves the T-score blank for anyone under 20, so a Total row that
    carries one score can only be read by where that score sits.
    """
    lines: dict[int, list[dict]] = {}
    for w in page.extract_words():
        lines.setdefault(round(w["top"]), []).append(w)
    rows = [sorted(ws, key=lambda w: w["x0"]) for _, ws in sorted(lines.items())]

    centres = {}
    for row in rows:
        texts = [w["text"] for w in row]
        if "BMD" in texts and "T" in texts and "Z" in texts:
            for w in row:
                if w["text"] in ("T", "Z"):
                    centres[f"{w['text']}_score"] = (w["x0"] + w["x1"]) / 2
            break
    if len(centres) < 2:
        return {}

    for row in rows:
        if row[0]["text"] != "Total":
            continue
        numbers = [w for w in row[1:] if NUMBER.match(w["text"])]
        if len(numbers) < 3:
            continue
        scores = {}
        for w in numbers[3:]:
            x = (w["x0"] + w["x1"]) / 2
            scores[min(centres, key=lambda k: abs(centres[k] - x))] = w["text"]
        return scores
    return {}


def table_rows(text: str, regions: list[str], n_values: int) -> dict[str, tuple[list[str], bool]]:
    """Each region's leading run of numbers, and whether the region carries a footnote mark.

    A row can share its line with a second table printed alongside it, or with
    image captions printed before it, so the region label is searched for
    anywhere in the line and only the numbers directly after it are taken.
    """
    labels = "|".join(re.escape(r) for r in sorted(regions, key=len, reverse=True))
    label_rx = re.compile(rf"(?<!\S)({labels})\s?([{re.escape(FOOTNOTE_MARKS)}]*)(?=\s)")
    rows: dict[str, tuple[list[str], bool]] = {}
    for line in text.splitlines():
        for m in label_rx.finditer(line):
            numbers = []
            for token in line[m.end():].split():
                if not NUMBER.match(token):
                    break
                numbers.append(token)
            if len(numbers) >= n_values:
                rows.setdefault(m.group(1), (numbers, bool(m.group(2))))
                break
    return rows


def read_page(scan: Scan, text: str, source: str, scores: dict[str, str] | None = None) -> None:
    """Add one page's values to the scan.

    `scores` is the bone density Total row's T- and Z-score from `total_scores`.
    Without it, the scores are taken only when both are printed.
    """
    for name, pattern in METADATA:
        m = re.search(pattern, text)
        if m and name not in scan.metadata:
            scan.metadata[name] = m.group(1)
    m = SCAN_ID.search(text)
    if m:
        scan.scan_ids.add(m.group(1))

    for table in TABLES:
        if not table.header.search(text):
            continue
        scan.tables_found.add(table.name)
        for region, (numbers, footnoted) in table_rows(text, table.regions, len(table.columns)).items():
            for column, token in zip(table.columns, numbers):
                scan.add(_key(region, column), token, table.rank, source)
            if table.name == "bone density" and region == "Total":
                if scores is None and len(numbers) >= 5:
                    scores = {"T_score": numbers[3], "Z_score": numbers[4]}
                for column, token in (scores or {}).items():
                    scan.add(_key(region, column), token, table.rank, source)
            if footnoted:
                scan.mirrored.add(region)

    if "Adipose Indices" in text or "Lean Indices" in text:
        for name, label in INDICES:
            m = re.search(label + r"\s+(-?\d+(?:\.\d+)?)", text)
            if m:
                scan.add(name, m.group(1), 1, source)


def read_pdfs(pdfs: list[Path], log=print) -> Scan:
    scan = Scan()
    for pdf_path in pdfs:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                pages = []
                for page in pdf.pages:
                    # Some exports draw every glyph twice at the same position, which
                    # plain extraction returns as "RReeggiioonn"; dedupe_chars drops the copy.
                    page = page.dedupe_chars()
                    text = page.extract_text() or ""
                    scores = total_scores(page) if BMD_HEADER.search(text) else None
                    pages.append((text, scores))
        except Exception as exc:
            scan.warnings.append(f"{pdf_path.name}: could not be opened ({exc})")
            log(f"      {pdf_path.name}: could not be opened ({exc})")
            continue
        if not any(text.strip() for text, _ in pages):
            log(f"      {pdf_path.name}: no text layer (a scanned image?), skipped")
            continue
        before = set(scan.tables_found)
        for text, scores in pages:
            read_page(scan, text, pdf_path.name, scores)
        found = sorted(scan.tables_found - before)
        log(f"      {pdf_path.name}: {', '.join(found) if found else 'no results table'}")
    check(scan)
    return scan


def _close(got: float, want: float, tolerance: float) -> bool:
    return abs(got - want) <= max(tolerance, 0.001 * abs(want))


def check(scan: Scan) -> None:
    """Arithmetic the report must satisfy. A failure means a number was misread."""
    v = scan.values()
    scan.warnings += scan.disagreements()

    if len(scan.scan_ids) > 1:
        scan.warnings.append(f"folder holds more than one scan: {', '.join(sorted(scan.scan_ids))}")
    if scan.mirrored:
        scan.warnings.append(
            f"{', '.join(sorted(scan.mirrored))} copied by the scanner from the opposite side")

    for column in MASS_COLUMNS:
        parts = [v.get(_key(r, column)) for r in LIMBS_AND_TRUNK]
        subtotal, head, total = (v.get(_key(r, column)) for r in ("Subtotal", "Head", "Total"))
        if None not in parts and subtotal is not None and not _close(sum(parts), subtotal, 1.0):
            scan.warnings.append(f"{column}: limbs + trunk ({sum(parts):g}) != Subtotal ({subtotal:g})")
        if None not in (subtotal, head, total) and not _close(subtotal + head, total, 1.0):
            scan.warnings.append(f"{column}: Subtotal + Head ({subtotal + head:g}) != Total ({total:g})")

    for region in COMPOSITION_REGIONS:
        bmc, fat, lean, lean_bmc, total = (v.get(_key(region, c)) for c in MASS_COLUMNS)
        if None not in (bmc, lean, lean_bmc) and not _close(bmc + lean, lean_bmc, 0.2):
            scan.warnings.append(f"{region}: BMC + Lean ({bmc + lean:g}) != Lean+BMC ({lean_bmc:g})")
        if None not in (fat, lean_bmc, total) and not _close(fat + lean_bmc, total, 1.0):
            scan.warnings.append(f"{region}: Fat + Lean+BMC ({fat + lean_bmc:g}) != Total ({total:g})")

    for column in ["Area_cm2", "BMC_g"]:
        parts = [v.get(_key(r, column)) for r in BMD_PARTS]
        subtotal = v.get(_key("Subtotal", column))
        if None not in parts and subtotal is not None and not _close(sum(parts), subtotal, 0.1):
            scan.warnings.append(f"{column}: bone regions ({sum(parts):g}) != Subtotal ({subtotal:g})")

    weight_lb, total_mass = scan.metadata.get("Weight_lb"), v.get("Total_Total_Mass_g")
    if weight_lb and total_mass:
        weight_g = float(weight_lb) * LB_TO_G
        if abs(total_mass - weight_g) / weight_g > 0.05:
            scan.warnings.append(
                f"Total mass {total_mass:.0f} g is more than 5% from the entered weight ({weight_g:.0f} g)")

    if scan.tables_found and "composition" not in scan.tables_found:
        scan.warnings.append("no composition page (BMC and lean mass by region)")


DXA_FOLDER = re.compile(r"d\s*e?\s*xa", re.I)


def find_timepoints(participant_dir: Path) -> list[tuple[str, Path, list[Path]]]:
    """(label, folder, PDFs) for every DXA-named folder under a participant that holds PDFs."""
    found = []
    for folder in sorted(p for p in participant_dir.rglob("*") if p.is_dir()):
        if not DXA_FOLDER.search(folder.name):
            continue
        pdfs = sorted(f for f in folder.iterdir() if f.is_file() and f.suffix.lower() == ".pdf")
        if pdfs:
            found.append(("_".join(folder.relative_to(participant_dir).parts), folder, pdfs))
    return found


def participant_root(root: Path) -> Path:
    """The folder that actually holds the participant folders.

    A study folder is easy to drag in by mistake. If only one folder inside it
    holds DXA folders, and they sit two or more levels down in several of its
    subfolders (participant / visit / DXA), that folder is the real root.
    """
    while True:
        with_scans = [(child, find_timepoints(child)) for child in sorted(root.iterdir())
                      if child.is_dir() and not child.name.startswith(".")]
        with_scans = [(child, tps) for child, tps in with_scans if tps]
        if len(with_scans) != 1:
            return root
        child, timepoints = with_scans[0]
        parts = [folder.relative_to(child).parts for _, folder, _ in timepoints]
        if len({p[0] for p in parts}) < 2 or min(len(p) for p in parts) < 3:
            return root
        root = child


def match_participants(root: Path, ids: list[str]) -> tuple[list[Path], list[str]]:
    """Participant folders matching each ID. "130" matches IMST_130 but not IMST_1300."""
    folders = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    if not ids:
        return folders, []
    chosen, unmatched = [], []
    for entry in ids:
        if entry.isdigit():
            rx = re.compile(rf"(?<!\d)0*{int(entry)}(?!\d)")
            hits = [f for f in folders if rx.search(f.name)]
        else:
            hits = ([f for f in folders if f.name.lower() == entry.lower()]
                    or [f for f in folders if entry.lower() in f.name.lower()])
        if not hits:
            unmatched.append(entry)
        chosen += [h for h in hits if h not in chosen]
    return chosen, unmatched


def _format(value) -> str:
    if isinstance(value, float):
        return f"{value:.10g}"
    return "" if value is None else str(value)


def extract(root: Path, ids: list[str], out_path: Path, log=print) -> int:
    found = participant_root(root)
    if found != root:
        log(f"The participant folders are inside '{found.relative_to(root)}'; reading from there.\n")
    participants, unmatched = match_participants(found, ids)
    for entry in unmatched:
        log(f"No participant folder matches '{entry}'")

    rows = []
    for participant in participants:
        timepoints = find_timepoints(participant)
        if not timepoints:
            log(f"{participant.name}: no DXA folder")
            continue
        log(participant.name)
        for label, folder, pdfs in timepoints:
            log(f"   {label} ({len(pdfs)} PDF{'s' if len(pdfs) != 1 else ''})")
            scan = read_pdfs(pdfs, log)
            values = scan.values()
            if not values:
                log("      no results found")
                continue
            for warning in scan.warnings:
                log(f"      warning: {warning}")
            rows.append({
                **scan.metadata, **values,
                "Participant_ID": participant.name,
                "Timepoint": label,
                "Mirrored_Regions": "; ".join(sorted(scan.mirrored)),
                "Warnings": "; ".join(scan.warnings),
                "Source_Folder": str(folder),
            })

    if not rows:
        log("Nothing extracted. Check that the DXA folders hold the report PDFs.")
        return 0
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: _format(row.get(c)) for c in COLUMNS})
    flagged = sum(1 for r in rows if r["Warnings"])
    log(f"\n{len(rows)} timepoint(s) written to {out_path}")
    if flagged:
        log(f"{flagged} of them carry warnings; see the Warnings column.")
    return len(rows)


def _clean_dropped_path(raw: str) -> Path:
    # Windows wraps a dragged path in quotes; macOS escapes its spaces instead.
    raw = raw.strip().strip("'\"")
    if sys.platform != "win32":
        raw = raw.replace("\\ ", " ")
    return Path(raw).expanduser()


def _ask() -> tuple[Path, list[str], Path]:
    print(f"DXA extractor {__version__}\n")
    while True:
        root = _clean_dropped_path(input(
            "Drag the folder that holds the participant folders into this window,\n"
            "then press Enter:\n> "))
        if root.is_dir():
            break
        print(f"Not a folder: {root}\n")
    ids = input("\nParticipant IDs to extract, separated by commas or spaces\n"
                "(press Enter for every participant):\n> ")
    out = input(f"\nSave the CSV as (press Enter for {root / 'DXA_Extraction_Results.csv'}):\n> ").strip()
    out_path = _clean_dropped_path(out) if out else root / "DXA_Extraction_Results.csv"
    print()
    return root, re.split(r"[,\s]+", ids.strip()) if ids.strip() else [], out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract Hologic DXA report values from participant folders into one CSV.")
    parser.add_argument("root", nargs="?", type=Path,
                        help="folder holding one folder per participant (omit to be asked)")
    parser.add_argument("--ids", nargs="+", default=[],
                        help="participant IDs or folder names to extract (default: all)")
    parser.add_argument("-o", "--output", type=Path,
                        help="CSV to write (default: DXA_Extraction_Results.csv in the root folder)")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    interactive = args.root is None
    if interactive:
        root, ids, out_path = _ask()
    else:
        if not args.root.is_dir():
            parser.error(f"not a folder: {args.root}")
        root, out_path = args.root, args.output or args.root / "DXA_Extraction_Results.csv"
        ids = [i for entry in args.ids for i in re.split(r"[,\s]+", entry) if i]

    written = extract(root, ids, out_path)
    if interactive:
        input("\nPress Enter to close.")
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
