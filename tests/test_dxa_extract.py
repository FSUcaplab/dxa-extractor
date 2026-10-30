"""Tests for dxa_extract. The page text mirrors Hologic Horizon W report layout; the numbers are made up."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dxa_extract as dx  # noqa: E402

COMPOSITION_PAGE = """\
Scan Date: March 3, 2025 ID: A03032501
Region BMC Fat Lean Lean + Total % Fat
(g) Mass (g) Mass (g) BMC (g) Mass (g)
L Arm\u00b9 150.0 1500.0 2100.0 2250.0 3750.0 40.0
R Arm 150.0 1500.0 2100.0 2250.0 3750.0 40.0
Trunk 650.0 14000.0 24000.0 24650.0 38650.0 36.2
L Leg 400.0 7000.0 8000.0 8400.0 15400.0 45.5
R Leg 400.0 7000.0 8000.0 8400.0 15400.0 45.5
Subtotal 1750.0 31000.0 44200.0 45950.0 76950.0 40.3
Head 550.0 850.0 3250.0 3800.0 4650.0 18.3
Total 2300.0 31850.0 47450.0 49750.0 81600.0 39.0
\u00b9 Data replicated from opposite side. Results using these regions are affected.
"""

INDICES_PAGE = """\
Body Composition Results Adipose Indices
Region Fat Lean + Total % Fat %Fat Percentile Measure Result Percentile
L Arm 1500 2250 3750 40.0 65 65 Total Body % Fat 39.0 80 80
Trunk 14000 24650 38650 36.2 76 75 Android/Gynoid Ratio 0.79
Subtotal 31000 45950 76950 40.3 81 81 Est. VAT Mass (g) 353
Android (A) 2050 3720 5770 35.5
Gynoid (G) 6800 8250 15050 45.2 Lean Indices
Analysis: March 3, 2025 14:42 Version 13.6.1.3 Lean/Height\u00b2 (kg/m\u00b2) 17.4 73 72
Auto Whole Body Appen. Lean/Height\u00b2 (kg/m\u00b2) 7.45 70 70
"""


def read(*pages):
    scan = dx.Scan()
    for i, text in enumerate(pages):
        dx.read_page(scan, text, f"page{i + 1}.pdf")
    return scan


def test_row_beside_a_second_table_keeps_only_its_own_numbers():
    rows = dx.table_rows(INDICES_PAGE, dx.COMPOSITION_REGIONS, 4)
    assert rows["L Arm"][0][:4] == ["1500", "2250", "3750", "40.0"]
    assert "Total" not in rows  # "Total Body % Fat" is an index label, not the Total row


def test_caption_printed_before_a_row_is_skipped():
    rows = dx.table_rows("318 x 150 Subtotal 1768.24 1811.13 1.024", ["Subtotal"], 3)
    assert rows["Subtotal"][0] == ["1768.24", "1811.13", "1.024"]


def test_footnoted_region_is_reported_as_mirrored():
    scan = read(COMPOSITION_PAGE)
    assert scan.mirrored == {"L Arm"}
    assert scan.values()["L_Arm_Fat_Mass_g"] == 1500.0


def test_composition_page_wins_over_rounded_copy():
    page = COMPOSITION_PAGE.replace("Trunk 650.0 14000.0", "Trunk 650.0 14000.4")
    scan = read(INDICES_PAGE, page)
    assert scan.values()["Trunk_Fat_Mass_g"] == 14000.4
    assert scan.disagreements() == []


def test_pages_that_disagree_beyond_rounding_are_reported():
    page = COMPOSITION_PAGE.replace("Trunk 650.0 14000.0", "Trunk 650.0 14100.0")
    scan = read(INDICES_PAGE, page)
    assert any(d.startswith("Trunk_Fat_Mass_g") for d in scan.disagreements())


def test_indices_and_fat_distribution():
    v = read(INDICES_PAGE).values()
    assert v["Android_Fat_Mass_g"] == 2050
    assert v["Gynoid_Pct_Fat"] == 45.2
    assert v["VAT_Mass_g"] == 353
    assert v["Lean_Height2_kg_m2"] == 17.4
    assert v["Appen_Lean_Height2_kg_m2"] == 7.45


def test_metadata():
    scan = read(COMPOSITION_PAGE, INDICES_PAGE)
    assert scan.metadata["Scan_ID"] == "A03032501"
    assert scan.metadata["Scan_Date"] == "March 3, 2025"
    assert scan.metadata["Analysis_Version"] == "13.6.1.3"


def test_consistent_report_passes_its_checks():
    scan = read(COMPOSITION_PAGE)
    dx.check(scan)
    assert scan.warnings == ["L Arm copied by the scanner from the opposite side"]


def test_misread_total_fails_its_checks():
    scan = read(COMPOSITION_PAGE.replace("Total 2300.0 31850.0", "Total 2300.0 38150.0"))
    dx.check(scan)
    assert any("Subtotal + Head" in w for w in scan.warnings)
    assert any(w.startswith("Total: Fat + Lean+BMC") for w in scan.warnings)


def test_numeric_id_matches_whole_number_only(tmp_path):
    for name in ["IMST_130", "IMST_1300", "IMST_013", "XS_13"]:
        (tmp_path / name).mkdir()
    chosen, unmatched = dx.match_participants(tmp_path, ["130", "13", "999"])
    assert [p.name for p in chosen] == ["IMST_130", "IMST_013", "XS_13"]
    assert unmatched == ["999"]


def test_timepoints_come_from_dxa_and_dexa_folders(tmp_path):
    for folder in ["W0/DXA", "W8/DEXA", "W8/Forms"]:
        (tmp_path / folder).mkdir(parents=True)
        (tmp_path / folder / "page1.pdf").write_bytes(b"")
    labels = [label for label, _, _ in dx.find_timepoints(tmp_path)]
    assert labels == ["W0_DXA", "W8_DEXA"]


def test_dropped_path_quotes_are_removed():
    assert dx._clean_dropped_path('"C:/Study Data/IMST"\n') == Path("C:/Study Data/IMST")
