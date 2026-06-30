"""
Music Score PDF → Note Data  (oemer + music21 edition)

Pipeline:
  1. fitz     — render each PDF page to a PNG image
  2. oemer    — deep-learning OMR: PNG → MusicXML   (ONNX, no TensorFlow needed)
  3. music21  — parse MusicXML → structured note events

ONNX model weights (~100 MB total) are downloaded automatically on first run.

Output:
  list[{"hz": float, "start": float, "duration": float, "dynamic": float}]
    hz       — frequency in Hz  (A4 = 440)
    start    — onset in seconds from the start of the piece
    duration — duration in seconds
    dynamic  — 0.0 (ppp) … 1.0 (fff),  default 0.60 (mf)

Usage:
    python test.py                 # converts test.pdf in the same directory
    python test.py <path/to/score.pdf>
"""

import os
import sys
import ssl
import json
import types
import shutil
import tempfile
import urllib.request

import numpy as np
# oemer 0.1.5 uses np.int / np.float / np.bool which were removed in NumPy 1.24.
# Patch them back before oemer is imported anywhere.
for _alias, _builtin in (("int", int), ("float", float), ("bool", bool),
                          ("complex", complex), ("object", object), ("str", str)):
    if not hasattr(np, _alias):
        setattr(np, _alias, _builtin)

import fitz          # PyMuPDF  (pip install pymupdf)
import music21
from music21 import note as m21note, chord as m21chord
from music21 import tempo as m21tempo, dynamics as m21dyn

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_BPM     = 120
DEFAULT_DYNAMIC = 0.60   # mf

DYNAMIC_MAP = {
    "ppp": 0.05, "pp": 0.15, "p": 0.25, "mp": 0.40,
    "mf":  0.60,
    "f":   0.75, "ff": 0.88, "fff": 0.95,
}


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — PDF → per-page PNG images
# ─────────────────────────────────────────────────────────────────────────────

def pdf_to_images(pdf_path: str, dpi: int = 200) -> tuple:
    """
    Render each page of the PDF to a PNG file in a fresh temp directory.
    Returns (list_of_png_paths, temp_dir).
    200 DPI is enough for oemer's CNN and keeps inference fast.
    """
    doc    = fitz.open(pdf_path)
    tmpdir = tempfile.mkdtemp(prefix="score_omr_")
    zoom   = dpi / 72.0
    mat    = fitz.Matrix(zoom, zoom)
    paths  = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
        out = os.path.join(tmpdir, f"page_{i+1:03d}.png")
        pix.save(out)
        paths.append(out)
    doc.close()
    return paths, tmpdir


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — PNG → MusicXML via oemer
# ─────────────────────────────────────────────────────────────────────────────

def _download(url: str, dest: str) -> None:
    """Download url → dest, bypassing macOS SSL cert verification issues."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    with urllib.request.urlopen(url, context=ctx) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        done  = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)   # 1 MB chunks
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    print(f"\r    {pct:3d}%  ({done // (1<<20)} MB / {total // (1<<20)} MB)",
                          end="", flush=True)
    print()


def _ensure_checkpoints() -> None:
    """
    Download the oemer ONNX weights on first run.
    Files go into the oemer package's checkpoints/ directory (~100 MB total).
    Subsequent runs skip this step entirely.
    """
    from oemer.ete import MODULE_PATH, CHECKPOINTS_URL

    unet_onnx = os.path.join(MODULE_PATH, "checkpoints", "unet_big", "model.onnx")
    if os.path.exists(unet_onnx):
        return

    print("  [setup] Downloading oemer ONNX checkpoints (~100 MB, one-time only)...")
    for title, url in CHECKPOINTS_URL.items():
        if not title.endswith(".onnx"):
            continue
        sub_dir   = "unet_big" if title.startswith("1st") else "seg_net"
        save_dir  = os.path.join(MODULE_PATH, "checkpoints", sub_dir)
        os.makedirs(save_dir, exist_ok=True)
        file_name = title.split("_", 1)[1]          # "1st_model.onnx" → "model.onnx"
        save_path = os.path.join(save_dir, file_name)
        if not os.path.exists(save_path):
            print(f"  [setup]   {title} ...")
            _download(url, save_path)
    print("  [setup] Checkpoints ready.")


def image_to_musicxml(img_path: str, output_dir: str) -> str:
    """
    Run oemer on one PNG image.
    Returns the path to the generated .musicxml file.
    """
    from oemer.ete import extract, clear_data

    os.makedirs(output_dir, exist_ok=True)
    args = types.SimpleNamespace(
        img_path      = img_path,
        output_path   = output_dir,
        use_tf        = False,    # use ONNX runtime (no TensorFlow required)
        save_cache    = False,
        without_deskew= False,
    )
    clear_data()
    return extract(args)


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — MusicXML → note events via music21
# ─────────────────────────────────────────────────────────────────────────────

def musicxml_to_notes(xml_path: str,
                       time_offset: float = 0.0,
                       default_bpm: float = DEFAULT_BPM) -> list:
    """
    Parse a MusicXML file with music21 and return note events.
    time_offset is added to all start times (used when stacking multiple pages).
    """
    score = music21.converter.parse(xml_path)

    # Tempo — use the first MetronomeMark found, fall back to default
    tempos  = list(score.flatten().getElementsByClass(m21tempo.MetronomeMark))
    bpm     = float(tempos[0].number) if tempos else default_bpm
    q_secs  = 60.0 / bpm                   # seconds per quarter note

    # Dynamic markings — sorted by offset so we can track the current level
    dyn_events = sorted(
        ((float(d.offset), DYNAMIC_MAP.get(d.value, DEFAULT_DYNAMIC))
         for d in score.flatten().getElementsByClass(m21dyn.Dynamic)),
        key=lambda x: x[0],
    )

    def dynamic_at(offset: float) -> float:
        val = DEFAULT_DYNAMIC
        for doff, dval in dyn_events:
            if doff <= offset:
                val = dval
            else:
                break
        return val

    notes = []
    for part in score.parts:
        for el in part.flatten().notesAndRests:
            if isinstance(el, m21note.Rest):
                continue

            offset   = float(el.offset)
            start    = round(time_offset + offset * q_secs, 4)
            duration = round(float(el.quarterLength) * q_secs, 4)
            dynamic  = round(dynamic_at(offset), 3)

            if isinstance(el, m21note.Note):
                notes.append({
                    "hz":       round(el.pitch.frequency, 3),
                    "start":    start,
                    "duration": duration,
                    "dynamic":  dynamic,
                })
            elif isinstance(el, m21chord.Chord):
                for pitch in el.pitches:
                    notes.append({
                        "hz":       round(pitch.frequency, 3),
                        "start":    start,
                        "duration": duration,
                        "dynamic":  dynamic,
                    })

    return sorted(notes, key=lambda n: (n["start"], n["hz"]))


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def convert(pdf_path: str, bpm: float = DEFAULT_BPM) -> list:
    """
    Full pipeline: PDF path → list[{"hz", "start", "duration", "dynamic"}]
    """
    print(f"\n{'─'*60}")
    print("Step 1/3  Rendering PDF pages to PNG...")
    img_paths, tmpdir = pdf_to_images(pdf_path)
    print(f"         {len(img_paths)} page(s) → {tmpdir}")

    print("Step 2/3  Checking oemer ONNX checkpoints...")
    _ensure_checkpoints()

    all_notes   = []
    time_offset = 0.0

    for i, img_path in enumerate(img_paths):
        label = f"page {i+1}/{len(img_paths)}"
        xml_dir = os.path.join(tmpdir, f"mxl_p{i+1}")

        print(f"\n         OMR {label}  ({os.path.basename(img_path)})")
        try:
            xml_path = image_to_musicxml(img_path, xml_dir)
        except Exception as exc:
            print(f"         ✗  oemer failed on {label}: {exc}")
            continue
        print(f"         ✓  MusicXML → {xml_path}")

        print(f"Step 3/3  Parsing MusicXML ({label})...")
        page_notes = musicxml_to_notes(xml_path,
                                        time_offset=time_offset,
                                        default_bpm=bpm)
        if page_notes:
            time_offset = max(n["start"] + n["duration"] for n in page_notes)

        all_notes.extend(page_notes)
        print(f"         {len(page_notes)} notes extracted from {label}")

    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"\n{'─'*60}")
    return sorted(all_notes, key=lambda n: (n["start"], n["hz"]))


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pdf = (sys.argv[1] if len(sys.argv) > 1
           else os.path.join(os.path.dirname(os.path.abspath(__file__)), "test.pdf"))

    if not os.path.exists(pdf):
        print(f"PDF not found: {pdf}")
        sys.exit(1)

    print(f"Converting: {pdf}")
    notes = convert(pdf)

    print(f"Total notes: {len(notes)}")
    print("First 10:")
    for n in notes[:10]:
        print(f"  {n}")
    if len(notes) > 10:
        print(f"  … and {len(notes) - 10} more")

    out = os.path.splitext(pdf)[0] + "_notes.json"
    with open(out, "w") as f:
        json.dump(notes, f, indent=2)
    print(f"\nSaved → {out}")
