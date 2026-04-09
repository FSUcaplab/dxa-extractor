"""
DXA Extractor GUI
==================
Extracts Hologic DXA data from participant folders.

- Point at any root folder containing participant subfolders
- Auto-detects all participant folder names (no prefix needed)
- Finds any subfolder with "DXA" in the name (W0/DXA, W8/DXA, Pre/DXA, etc.)
- Labels each row: ParticipantFolderName + timepoint path

Requirements:
    pip install pdfplumber pandas
"""

import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
import threading
import os
import re
import pdfplumber
import pandas as pd
from pathlib import Path


# ============================================================
# DXA PARSING LOGIC
# ============================================================

def fix_doubled_chars(text):
    result = []
    i = 0
    while i < len(text):
        if i + 1 < len(text) and text[i] == text[i + 1]:
            result.append(text[i])
            i += 2
        else:
            result.append(text[i])
            i += 1
    return "".join(result)

def needs_dedup(text):
    return bool(re.search(r"(.)\1{3,}", text))

def clean_text(text):
    return fix_doubled_chars(text) if needs_dedup(text) else text

def detect_report_type(text):
    t = clean_text(text)
    if "Est. VAT" in t or "Android" in t:
        return "body_comp_vat"
    if "BMD" in t and ("T-score" in t or "T -" in t):
        return "bmd"
    if "Lean Mass" in t and "BMC" in t:
        return "full_body_comp"
    return "lean_fat_summary"

def parse_body_comp_vat(text):
    data = {}
    region_pattern = re.compile(
        r"(L Arm|R Arm|Trunk|L Leg|R Leg|Subtotal|Head|Total|Android \(A\)|Gynoid \(G\))"
        r"\s+([\d\.]+)\s+([\d\.]+)\s+([\d\.]+)\s+([\d\.]+)"
    )
    for m in region_pattern.finditer(text):
        name = m.group(1).replace(" (A)", "").replace(" (G)", "")
        prefix = name.replace(" ", "_")
        data[f"{prefix}_Fat_Mass_g"]      = float(m.group(2))
        data[f"{prefix}_Lean_plus_BMC_g"] = float(m.group(3))
        data[f"{prefix}_Total_Mass_g"]    = float(m.group(4))
        data[f"{prefix}_Pct_Fat"]         = float(m.group(5))
    for label, key in [
        (r"Est\. VAT Mass \(g\)",       "VAT_Mass_g"),
        (r"Est\. VAT Volume \(cm.?\)",  "VAT_Volume_cm3"),
        (r"Est\. VAT Area \(cm.?\)",    "VAT_Area_cm2"),
        (r"Android/Gynoid Ratio",       "Android_Gynoid_Ratio"),
        (r"% Fat Trunk/% Fat Legs",     "Pct_Fat_Trunk_Legs_Ratio"),
        (r"Trunk/Limb Fat Mass Ratio",  "Trunk_Limb_Fat_Ratio"),
    ]:
        m = re.search(label + r"\s+([\d\.]+)", text)
        if m:
            data[key] = float(m.group(1))
    m = re.search(r"Appen\. Lean/Height.?\s*\(kg/m.?\)\s+([\d\.]+)", text)
    if m:
        data["Appen_Lean_Height2_kg_m2"] = float(m.group(1))
    m = re.search(r"(?<!Appen\. )Lean/Height.?\s*\(kg/m.?\)\s+([\d\.]+)", text)
    if m:
        data["Lean_Height2_kg_m2"] = float(m.group(1))
    return data

def parse_full_body_comp(text):
    data = {}
    text = clean_text(text)
    region_pattern = re.compile(
        r"(L Arm|R Arm|Trunk|L Leg|R Leg|Subtotal|Head|Total)"
        r"\s+([\d\.]+)\s+([\d\.]+)\s+([\d\.]+)\s+([\d\.]+)\s+([\d\.]+)\s+([\d\.]+)"
    )
    for m in region_pattern.finditer(text):
        p = m.group(1).replace(" ", "_")
        data[f"{p}_BMC_g"]           = float(m.group(2))
        data[f"{p}_Fat_Mass_g"]      = float(m.group(3))
        data[f"{p}_Lean_Mass_g"]     = float(m.group(4))
        data[f"{p}_Lean_plus_BMC_g"] = float(m.group(5))
        data[f"{p}_Total_Mass_g"]    = float(m.group(6))
        data[f"{p}_Pct_Fat"]         = float(m.group(7))
    return data

def parse_bmd(text):
    data = {}
    text = clean_text(text)
    m = re.search(
        r"Total\s+([\d\.]+)\s+([\d\.]+)\s+([\d\.]+)\s+([-\d\.]+)\s+([-\d\.]+)", text)
    if m:
        data["Total_Area_cm2"]  = float(m.group(1))
        data["Total_BMC_g"]     = float(m.group(2))
        data["Total_BMD_g_cm2"] = float(m.group(3))
        data["Total_T_score"]   = float(m.group(4))
        data["Total_Z_score"]   = float(m.group(5))
    region_pattern = re.compile(
        r"(L Arm|R Arm|L Ribs|R Ribs|T Spine|L Spine|Pelvis|L Leg|R Leg|Subtotal|Head)"
        r"\s+([\d\.]+)\s+([\d\.]+)\s+([\d\.]+)"
    )
    for m in region_pattern.finditer(text):
        p = m.group(1).replace(" ", "_")
        data[f"{p}_Area_cm2"]  = float(m.group(2))
        data[f"{p}_BMC_g"]     = float(m.group(3))
        data[f"{p}_BMD_g_cm2"] = float(m.group(4))
    return data

def parse_lean_fat_summary(text):
    data = {}
    text = clean_text(text)
    region_pattern = re.compile(
        r"(L Arm|R Arm|Trunk|L Leg|R Leg|Subtotal|Head|Total)"
        r"\s+([\d\.]+)\s+([\d\.]+)\s+([\d\.]+)"
    )
    for m in region_pattern.finditer(text):
        p = m.group(1).replace(" ", "_")
        data[f"{p}_Fat_Mass_g"]      = float(m.group(2))
        data[f"{p}_Lean_plus_BMC_g"] = float(m.group(3))
        data[f"{p}_Pct_Fat"]         = float(m.group(4))
    return data

def parse_metadata(text):
    text = clean_text(text)
    meta = {}
    for pattern, key, cast in [
        (r"Name:\s*(.+?)(?:\s{2,}|Sex:)",  "Name",      str),
        (r"DOB:\s*(.+?)(?:\s{2,}|Age:)",   "DOB",       str),
        (r"Sex:\s*(\w+)",                   "Sex",       str),
        (r"Ethnicity:\s*(\w+)",             "Ethnicity", str),
        (r"Height:\s*([\d\.]+)\s*in",       "Height_in", float),
        (r"Weight:\s*([\d\.]+)\s*lb",       "Weight_lb", float),
        (r"Age:\s*(\d+)",                   "Age",       int),
        (r"Scan Date:\s*(.+?)\s{2,}",       "Scan_Date", str),
        (r"Scan Date:.*?ID:\s*(\S+)",       "Scan_ID",   str),
    ]:
        m = re.search(pattern, text)
        if m:
            try:
                meta[key] = cast(m.group(1).strip())
            except Exception:
                pass
    return meta

PARSERS = {
    "body_comp_vat":    parse_body_comp_vat,
    "full_body_comp":   parse_full_body_comp,
    "bmd":              parse_bmd,
    "lean_fat_summary": parse_lean_fat_summary,
}

def find_dxa_folders(participant_folder):
    """Walk participant folder, return all subfolders with 'dxa' in name that contain PDFs."""
    results = []
    participant_folder = Path(participant_folder)
    for root, dirs, files in os.walk(participant_folder):
        root_path = Path(root)
        if "dxa" in root_path.name.lower():
            pdfs = sorted([f for f in files if f.lower().endswith(".pdf")])
            if pdfs:
                rel = root_path.relative_to(participant_folder)
                label = "_".join(rel.parts)
                results.append((label, str(root_path), pdfs))
    return results

def extract_dxa_folder(folder_path, pdf_files, log_fn=None):
    all_data = {}
    metadata_captured = False
    seen_types = set()
    for fname in pdf_files:
        fpath = os.path.join(folder_path, fname)
        try:
            with pdfplumber.open(fpath) as pdf:
                text = pdf.pages[0].extract_text() or ""
        except Exception as e:
            if log_fn:
                log_fn(f"      ⚠ Could not read {fname}: {e}")
            continue
        if not text.strip():
            continue
        if not metadata_captured:
            all_data.update(parse_metadata(text))
            metadata_captured = True
        rtype = detect_report_type(text)
        if rtype in seen_types and rtype != "body_comp_vat":
            continue
        seen_types.add(rtype)
        if log_fn:
            log_fn(f"      ✓ {fname} → {rtype}")
        all_data.update(PARSERS[rtype](text))
    return all_data


# ============================================================
# GUI
# ============================================================

class DXAExtractorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("DXA Data Extractor — CAP Lab")
        self.root.geometry("800x700")
        self.root.resizable(True, True)
        self.root.configure(bg="#1a1d23")

        # ── Colour palette ──────────────────────────────────
        self.c = {
            "bg":       "#1a1d23",
            "card":     "#22262f",
            "accent":   "#4f8ef7",
            "fg":       "#e8eaf0",
            "fg_dim":   "#7a8099",
            "entry_bg": "#2a2f3d",
            "border":   "#343a4a",
            "success":  "#4caf7d",
            "warning":  "#f5a623",
            "error":    "#e05c5c",
        }

        # ── Folder path storage (initialised HERE, never reset) ──
        self.root_folder_var   = tk.StringVar()
        self.output_folder_var = tk.StringVar()
        self.output_name_var   = tk.StringVar(value="DXA_Extraction_Results.csv")

        self._build_ui()

    # ── UI Construction ─────────────────────────────────────

    def _build_ui(self):
        c = self.c

        # Header
        hdr = tk.Frame(self.root, bg=c["bg"], pady=16)
        hdr.pack(fill="x", padx=20)
        tk.Label(hdr, text="DXA DATA EXTRACTOR",
                 font=("Courier New", 16, "bold"),
                 bg=c["bg"], fg=c["fg"]).pack(anchor="w")
        tk.Label(hdr, text="Hologic Horizon  ·  CAP Lab  ·  Florida State University",
                 font=("Courier New", 9), bg=c["bg"], fg=c["fg_dim"]).pack(anchor="w")
        tk.Frame(self.root, bg=c["border"], height=1).pack(fill="x", padx=20)

        main = tk.Frame(self.root, bg=c["bg"], padx=20, pady=12)
        main.pack(fill="both", expand=True)

        # ── 01 Folder Paths ──────────────────────────────────
        self._section(main, "01 / FOLDER PATHS")
        card1 = self._card(main)

        self._folder_row(card1, "Root Data Folder",
                         "Folder that contains all participant subfolders  (e.g. IMST_27, CAP_05 …)",
                         self.root_folder_var)
        tk.Frame(card1, bg=c["border"], height=1).pack(fill="x", pady=8)
        self._folder_row(card1, "Output Folder",
                         "Where to save the results CSV",
                         self.output_folder_var)

        # ── 02 Participant IDs ───────────────────────────────
        self._section(main, "02 / PARTICIPANT IDs")
        card2 = self._card(main)

        tk.Label(card2,
                 text="Enter participant IDs or full folder names (comma / newline separated).\n"
                      "Partial IDs like '130' will match any folder containing that number (e.g. IMST_130).\n"
                      "Or click AUTO-DETECT to load all folders from the root:",
                 bg=c["card"], fg=c["fg_dim"],
                 font=("Courier New", 9), wraplength=680, justify="left"
                 ).pack(anchor="w", pady=(0, 6))

        self.id_text = tk.Text(
            card2, height=4,
            bg=c["entry_bg"], fg=c["fg"],
            insertbackground=c["fg"],
            font=("Courier New", 10),
            relief="flat", bd=6, wrap="word"
        )
        self.id_text.pack(fill="x")

        btn_row = tk.Frame(card2, bg=c["card"])
        btn_row.pack(fill="x", pady=(8, 0))
        self._ghost_btn(btn_row, "CLEAR",
                        lambda: self.id_text.delete("1.0", "end"))
        self._ghost_btn(btn_row, "AUTO-DETECT FROM ROOT FOLDER",
                        self._auto_detect_ids, padx_left=8)

        # ── 03 Options ───────────────────────────────────────
        self._section(main, "03 / OPTIONS")
        card3 = self._card(main)
        self._option_row(card3, "Output filename:", self.output_name_var)

        # ── Run ──────────────────────────────────────────────
        run_row = tk.Frame(main, bg=c["bg"], pady=6)
        run_row.pack(fill="x")

        self.run_btn = tk.Button(
            run_row,
            text="▶   EXTRACT DXA DATA",
            command=self._run,
            bg=c["accent"], fg="#ffffff",
            font=("Courier New", 11, "bold"),
            relief="flat", bd=0, padx=24, pady=10,
            cursor="hand2",
            activebackground="#3a7ae8", activeforeground="#ffffff",
        )
        self.run_btn.pack(side="left")

        self.status_lbl = tk.Label(run_row, text="",
                                    bg=c["bg"], fg=c["fg_dim"],
                                    font=("Courier New", 9))
        self.status_lbl.pack(side="left", padx=16)

        # ── Log ──────────────────────────────────────────────
        tk.Frame(main, bg=c["border"], height=1).pack(fill="x", pady=(8, 0))
        self._section(main, "LOG")

        self.log_box = scrolledtext.ScrolledText(
            main, height=10,
            bg="#111318", fg="#a0ffb0",
            font=("Courier New", 9),
            relief="flat", bd=0, wrap="word",
            insertbackground="#a0ffb0",
        )
        self.log_box.pack(fill="both", expand=True, pady=(4, 12))
        self.log_box.configure(state="disabled")
        self.log("Ready. Set the root folder, add participant IDs, then click EXTRACT.")

    # ── Widget helpers ───────────────────────────────────────

    def _section(self, parent, text):
        c = self.c
        f = tk.Frame(parent, bg=c["bg"])
        f.pack(fill="x", pady=(8, 2))
        tk.Label(f, text=text, bg=c["bg"], fg=c["accent"],
                 font=("Courier New", 9, "bold")).pack(anchor="w")

    def _card(self, parent):
        c = self.c
        card = tk.Frame(parent, bg=c["card"], padx=16, pady=14)
        card.pack(fill="x", pady=(2, 10))
        return card

    def _folder_row(self, parent, label, hint, string_var):
        """One folder-picker row wired directly to the given StringVar."""
        c = self.c
        row = tk.Frame(parent, bg=c["card"])
        row.pack(fill="x", pady=4)

        tk.Label(row, text=label, bg=c["card"], fg=c["fg"],
                 font=("Courier New", 10, "bold"),
                 width=18, anchor="w").pack(side="left")

        tk.Entry(row, textvariable=string_var,
                 bg=c["entry_bg"], fg=c["fg"],
                 insertbackground=c["fg"],
                 font=("Courier New", 9),
                 relief="flat", bd=6
                 ).pack(side="left", fill="x", expand=True, padx=(8, 6))

        tk.Button(row, text="BROWSE",
                  command=lambda v=string_var: self._browse(v),
                  bg=c["entry_bg"], fg=c["fg_dim"],
                  font=("Courier New", 8), relief="flat", bd=0,
                  padx=10, pady=4, cursor="hand2").pack(side="left")

        if hint:
            tk.Label(parent, text=hint,
                     bg=c["card"], fg=c["fg_dim"],
                     font=("Courier New", 8)).pack(anchor="w", padx=2)

    def _option_row(self, parent, label, var):
        c = self.c
        row = tk.Frame(parent, bg=c["card"])
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=c["card"], fg=c["fg_dim"],
                 font=("Courier New", 9), width=20, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=var,
                 bg=c["entry_bg"], fg=c["fg"],
                 insertbackground=c["fg"],
                 font=("Courier New", 9),
                 relief="flat", bd=4, width=36).pack(side="left", padx=(6, 0))

    def _ghost_btn(self, parent, text, cmd, padx_left=0):
        c = self.c
        tk.Button(parent, text=text, command=cmd,
                  bg=c["entry_bg"], fg=c["fg_dim"],
                  font=("Courier New", 9), relief="flat", bd=0,
                  padx=10, pady=4, cursor="hand2"
                  ).pack(side="left", padx=(padx_left, 0))

    def _browse(self, string_var):
        path = filedialog.askdirectory()
        if path:
            string_var.set(path)

    # ── Auto-detect participant folders ──────────────────────

    def _auto_detect_ids(self):
        root = self.root_folder_var.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showwarning("No Folder",
                                   "Set a valid Root Data Folder first.")
            return
        folders = sorted(
            name for name in os.listdir(root)
            if os.path.isdir(os.path.join(root, name))
            and not name.startswith(".")
        )
        self.id_text.delete("1.0", "end")
        self.id_text.insert("1.0", "\n".join(folders))
        self.log(f"Auto-detected {len(folders)} folder(s) in: {root}")

    # ── Logging / status ─────────────────────────────────────

    def log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _status(self, msg, color=None):
        self.status_lbl.configure(text=msg,
                                   fg=color or self.c["fg_dim"])

    # ── Extraction ───────────────────────────────────────────

    def _resolve_folder_names(self, root_folder, raw_entries):
        """
        For each entry the user typed, find matching subfolders in root_folder.
        - If the entry exactly matches a folder name → use it directly.
        - Otherwise treat it as a partial ID and match any folder whose name
          contains that string (e.g. '130' matches 'IMST_130').
        Returns list of (entered_term, resolved_folder_name) tuples.
        """
        all_subdirs = [
            name for name in os.listdir(root_folder)
            if os.path.isdir(os.path.join(root_folder, name))
            and not name.startswith(".")
        ]
        resolved = []
        for entry in raw_entries:
            if entry in all_subdirs:
                resolved.append((entry, entry))
            else:
                # Partial match: folder name contains the entry string
                matches = [d for d in all_subdirs if entry in d]
                if len(matches) == 1:
                    resolved.append((entry, matches[0]))
                elif len(matches) > 1:
                    # Multiple matches — use all of them
                    for m in matches:
                        resolved.append((entry, m))
                else:
                    resolved.append((entry, None))  # not found
        return resolved

    def _run(self):
        root_folder   = self.root_folder_var.get().strip()
        output_folder = self.output_folder_var.get().strip()
        output_name   = self.output_name_var.get().strip() or "DXA_Extraction_Results.csv"
        raw_ids       = self.id_text.get("1.0", "end").strip()

        if not root_folder or not os.path.isdir(root_folder):
            messagebox.showerror("Error", "Please set a valid Root Data Folder.")
            return
        if not output_folder or not os.path.isdir(output_folder):
            messagebox.showerror("Error", "Please set a valid Output Folder.")
            return
        if not raw_ids:
            messagebox.showerror("Error", "Please enter at least one participant ID.")
            return

        raw_entries = [x.strip() for x in re.split(r"[,\n]+", raw_ids) if x.strip()]
        resolved    = self._resolve_folder_names(root_folder, raw_entries)

        self.run_btn.configure(state="disabled")
        self._status("Running…", self.c["warning"])

        def worker():
            try:
                self._extract(root_folder, output_folder, resolved, output_name)
            except Exception as e:
                self.log(f"\n✗ Unexpected error: {e}")
                self._status("Error — see log", self.c["error"])
            finally:
                self.run_btn.configure(state="normal")

        threading.Thread(target=worker, daemon=True).start()

    def _extract(self, root_folder, output_folder, resolved, output_name):
        rows = []
        skipped = 0

        self.log(f"\n{'─'*55}")
        self.log(f"Processing {len(resolved)} participant(s)")
        self.log(f"Root: {root_folder}")
        self.log(f"{'─'*55}")

        for entry, fname in resolved:
            if fname is None:
                self.log(f"\n⚠  '{entry}' — no matching folder found in root, skipping")
                skipped += 1
                continue

            # Show matched name if it differs from what was typed
            label = fname if fname == entry else f"{fname}  (matched '{entry}')"
            p_path = os.path.join(root_folder, fname)

            self.log(f"\n→ {label}")
            dxa_folders = find_dxa_folders(p_path)

            if not dxa_folders:
                self.log(f"   ⚠ No DXA subfolders found inside '{fname}'")
                self.log(f"      (looking for any subfolder with 'dxa' in its name)")
                skipped += 1
                continue

            for timepoint_label, dxa_path, pdf_files in dxa_folders:
                self.log(f"   📁 {timepoint_label}  ({len(pdf_files)} PDF{'s' if len(pdf_files)!=1 else ''})")
                row_data = extract_dxa_folder(dxa_path, pdf_files, self.log)

                if not row_data:
                    self.log("      ⚠ No data extracted from this folder")
                    continue

                row_data["Participant_ID"] = fname
                row_data["Timepoint"]      = timepoint_label
                row_data["Source_Folder"]  = dxa_path
                rows.append(row_data)

        self.log(f"\n{'─'*55}")

        if not rows:
            self.log("✗ No data extracted.")
            self.log("  Check that your DXA folders contain PDFs and have 'DXA' somewhere in the path.")
            self._status("Nothing extracted", self.c["error"])
            return

        df = pd.DataFrame(rows)
        front = ["Participant_ID", "Timepoint", "Scan_Date", "Scan_ID",
                 "Sex", "Age", "Height_in", "Weight_lb", "Source_Folder"]
        present_front = [col for col in front if col in df.columns]
        rest = [col for col in df.columns if col not in present_front]
        df = df[present_front + rest]

        out_path = os.path.join(output_folder, output_name)
        df.to_csv(out_path, index=False)

        processed = len(resolved) - skipped
        self.log(f"✓ {len(rows)} row(s) from {processed} participant(s)  ({len(df.columns)} columns)")
        self.log(f"✓ Saved → {out_path}")
        self._status(f"Done — {len(rows)} rows saved", self.c["success"])


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    DXAExtractorApp(root)
    root.mainloop()
