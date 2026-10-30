# DXA Data Extractor

Batch-extracts body composition and bone density results from **Hologic DXA** PDF reports into one CSV, one row per participant and timepoint. Built for longitudinal studies with pre/post or multi-timepoint scan designs.

Developed at the **Cardiovascular and Applied Physiology (CAP) Lab**, Florida State University.

---

## What it does

- **Reads every report table**: whole-body composition (BMC, fat, lean, lean + BMC, total mass, % fat by region), bone density (area, BMC, BMD by region, total T- and Z-score), android and gynoid fat, VAT mass, volume and area, and the adipose and lean indices.
- **Keeps the precise copy of each number.** Hologic prints regional masses on more than one page, to whole grams on the summary page and to 0.1 g on the composition page. The tool takes the most precise copy, and it raises a warning if two pages disagree by more than rounding.
- **Checks the report's own arithmetic.** Limbs + trunk = Subtotal, Subtotal + Head = Total, Fat + Lean + BMC = Total, and total mass against the weight entered at the scanner. A failed check means a number was misread, and it is reported rather than hidden.
- **Flags mirrored limbs.** When a limb falls outside the scan field, Hologic copies the opposite side and marks the region with a superscript (`L Arm¹`). Those values are estimated rather than measured, and the tool lists them in `Mirrored_Regions`.
- **Handles overprinted reports.** Some exports draw every character twice, so plain text extraction reads `RReeggiioonn`. Duplicate glyphs are removed by position before the text is read.
- Pages are identified by their content, so PDF filenames and page order do not matter, and order forms or other paperwork in the same folder are skipped.

---

## Install

Python 3.9 or newer.

```bash
git clone https://github.com/FSUcaplab/dxa-extractor.git
cd dxa-extractor
pip install -r requirements.txt
```

---

## Run

### Interactive

```bash
python dxa_extract.py
```

The tool asks three questions:

1. **The root folder.** Drag the folder that holds the participant folders into the terminal window, then press Enter.
2. **Participant IDs.** Type them separated by commas or spaces (`130, 131, 134`), or press Enter for everyone.
3. **Where to save the CSV.** Press Enter to save `DXA_Extraction_Results.csv` in the root folder.

### Command line

```bash
python dxa_extract.py "D:/Study/Participant Data"
python dxa_extract.py "D:/Study/Participant Data" --ids 130 131 134 -o dxa_results.csv
```

| Option | Meaning |
|---|---|
| `root` | folder holding one folder per participant |
| `--ids` | participant IDs or folder names; default is every participant |
| `-o`, `--output` | CSV to write; default is `DXA_Extraction_Results.csv` in the root folder |

The exit status is 0 when at least one timepoint was written, so the tool can run inside a script.

A numeric ID matches the whole number in a folder name: `130` matches `IMST_130` and `IMST_0130`, never `IMST_1300`. Any other entry matches a folder name exactly, or else any folder whose name contains it.

---

## Folder structure

Inside each participant folder, every subfolder whose name contains `DXA` or `DEXA` (any case) is one timepoint, and the PDFs directly inside it are that timepoint's report pages.

```
Participant Data/
├── IMST_130/
│   ├── W0/
│   │   └── DXA/
│   │       ├── IMST_130_W0_Page1.pdf
│   │       ├── IMST_130_W0_Page2.pdf
│   │       ├── IMST_130_W0_Page3.pdf
│   │       └── IMST_130_W0_Page4.pdf
│   └── W8/
│       └── DXA/
│           └── ...
└── IMST_131/
    └── Pre/
        └── DEXA/
            └── ...
```

These give the timepoints `W0_DXA` and `W8_DXA` for `IMST_130`, and `Pre_DEXA` for `IMST_131`.

---

## Output

One row per participant × timepoint, with a fixed set of columns so that files from different runs line up:

| Columns | Content |
|---|---|
| `Participant_ID`, `Timepoint`, `Scan_Date`, `Scan_ID`, `Sex`, `Age`, `Ethnicity`, `Height_in`, `Weight_lb` | identification, as printed on the report |
| `{Region}_BMC_g`, `_Fat_Mass_g`, `_Lean_Mass_g`, `_Lean_plus_BMC_g`, `_Total_Mass_g`, `_Pct_Fat` | L Arm, R Arm, Trunk, L Leg, R Leg, Subtotal, Head, Total |
| `Android_*`, `Gynoid_*` | fat mass, lean + BMC, total mass, % fat |
| `Total_Body_Pct_Fat`, `Fat_Mass_Height2_kg_m2`, `Android_Gynoid_Ratio`, `Pct_Fat_Trunk_Legs_Ratio`, `Trunk_Limb_Fat_Ratio` | adipose indices |
| `VAT_Mass_g`, `VAT_Volume_cm3`, `VAT_Area_cm2` | estimated visceral adipose tissue |
| `Lean_Height2_kg_m2`, `Appen_Lean_Height2_kg_m2` | lean indices |
| `{Region}_Area_cm2`, `{Region}_BMD_g_cm2`, `Total_T_score`, `Total_Z_score` | bone density, including ribs, spine and pelvis |
| `Analysis_Version` | Hologic software version that analysed the scan |
| `Mirrored_Regions` | regions the scanner copied from the opposite side |
| `Warnings` | every failed check for that timepoint; empty when the report is internally consistent |
| `Source_Folder` | folder the values came from |

The patient name and date of birth printed on the report are not written to the CSV.

**Read the `Warnings` column before using the data.** Typical entries are a folder that holds more than one scan, two pages that disagree, a report with no composition page, or a total mass more than 5% from the entered weight.

---

## Accuracy

The tool was checked on 77 whole-body scans from two CAP Lab studies, against an independent parser that reproduces the studies' hand-entered values exactly. All 4,466 values the two parsers share matched, and so did every mirrored-limb flag.

Tested on a Hologic Horizon W, software version 13.6. Other models and software versions may lay out their reports differently. If a table is missing from the output, the terminal log shows which tables were found in each PDF.

To run the tests:

```bash
pip install pytest
python -m pytest tests
```

---

## Changes in 2.0

Version 1 was a desktop window (`dxa_extractor_gui.py`). On the Horizon W reports above it missed BMC and lean mass, because it did not recognise the composition page. It also took regional total mass from the whole-gram summary page. Version 2 replaces it with a terminal tool, reads every table, and adds the checks and warnings described above. Column names are unchanged for the values version 1 extracted.

---

## License

MIT License. See `LICENSE`.

---

## How to cite

This repository includes a [`CITATION.cff`](CITATION.cff) file, so GitHub shows a
**"Cite this repository"** button in the sidebar (APA and BibTeX).

For reference managers, use the ready-made export files:

| File | Use with |
|---|---|
| [`CITATION.ris`](CITATION.ris) | EndNote, Zotero, Mendeley, RefWorks |
| [`CITATION.bib`](CITATION.bib) | BibTeX / LaTeX |
| [`CITATION.cff`](CITATION.cff) | the source of truth the other two are generated from |

Please cite the software record. If this tool contributed to a publication,
citing it here is what makes the analysis reproducible by others.

`CITATION.cff` is the source of truth; the `.ris` and `.bib` are generated from
it with the CAP Lab `cff2ris.py` converter. Regenerate them after any edit:

```bash
python cff2ris.py CITATION.cff
```

(The general-purpose [`cffconvert`](https://github.com/citation-file-format/cffconvert)
tool will also read this file, but it ignores `preferred-citation` and so emits
only the software record, without the journal, volume, pages, or DOI.)
