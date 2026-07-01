"""
Music Score PDF → Note Data  (oemer + music21 edition)

Pipeline:
  1. fitz     — render each PDF page to a PNG image
  2. oemer    — deep-learning OMR: PNG → MusicXML   (ONNX, no TensorFlow needed)
               layer data (NoteHead bboxes) is captured immediately after inference
  3. music21  — parse MusicXML → structured note events
  4. (opt)    — annotated PDF showing every detected notehead, color-coded by duration

ONNX model weights (~100 MB total) are downloaded automatically on first run.

Output:
  list[{"hz": float, "start": float, "duration": float, "dynamic": float}]
    hz       — frequency in Hz  (A4 = 440)
    start    — onset in quarter-note beats from the start of the piece
    duration — length in quarter-note beats  (quarter=1, half=2, eighth=0.5)
    dynamic  — 0.0 (ppp) … 1.0 (fff),  default 0.60 (mf)

Usage:
    python test.py                           # converts test.pdf → notes JSON + annotated PDF
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
for _alias, _builtin in (("int", int), ("float", float), ("bool", bool), ("complex", complex)):
    if not hasattr(np, _alias):
        setattr(np, _alias, _builtin)

import cv2
import fitz          # PyMuPDF  (pip install pymupdf)
import music21
import music21.stream as m21stream
from music21 import note as m21note, chord as m21chord
from music21 import tempo as m21tempo, dynamics as m21dyn, meter as m21meter
from collections import defaultdict

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

_DYN_LEVELS = sorted(DYNAMIC_MAP.values())   # ordered list: 0.05 … 0.95


def _adjacent_dyn(val: float, louder: bool) -> float:
    """Return the next louder (or softer) standard dynamic level."""
    if louder:
        above = [v for v in _DYN_LEVELS if v > val + 0.01]
        return above[0] if above else _DYN_LEVELS[-1]
    else:
        below = [v for v in _DYN_LEVELS if v < val - 0.01]
        return below[-1] if below else _DYN_LEVELS[0]

# BGR colors for each note duration (used in annotation)
_DUR_COLOR = {
    "WHOLE":      (50,  50,  255),   # red
    "HALF":       (0,   165, 255),   # orange
    "QUARTER":    (0,   200, 50 ),   # green
    "EIGHTH":     (255, 180, 0  ),   # cyan
    "SIXTEENTH":  (255, 0,   180),   # magenta
}
_DEFAULT_COLOR = (150, 150, 150)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — PDF → per-page PNG images
# ─────────────────────────────────────────────────────────────────────────────

def pdf_to_images(pdf_path: str, dpi: int = 200) -> tuple:
    """
    Render each page of the PDF to a PNG file in a fresh temp directory.
    Returns (list_of_png_paths, temp_dir).
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
# Step 2 — PNG → MusicXML via oemer  (also captures layer data for annotation)
# ─────────────────────────────────────────────────────────────────────────────

def _download(url: str, dest: str) -> None:
    """Download url → dest, bypassing macOS SSL cert verification."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    with urllib.request.urlopen(url, context=ctx) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        done  = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    print(f"\r    {pct:3d}%  ({done//(1<<20)} MB / {total//(1<<20)} MB)",
                          end="", flush=True)
    print()


def _ensure_checkpoints() -> None:
    """Download oemer ONNX weights on first run (~100 MB, cached permanently)."""
    from oemer.ete import MODULE_PATH, CHECKPOINTS_URL
    unet = os.path.join(MODULE_PATH, "checkpoints", "unet_big", "model.onnx")
    if os.path.exists(unet):
        return
    print("  [setup] Downloading oemer ONNX checkpoints (~100 MB, one-time only)...")
    for title, url in CHECKPOINTS_URL.items():
        if not title.endswith(".onnx"):
            continue
        sub  = "unet_big" if title.startswith("1st") else "seg_net"
        sdir = os.path.join(MODULE_PATH, "checkpoints", sub)
        os.makedirs(sdir, exist_ok=True)
        dest = os.path.join(sdir, title.split("_", 1)[1])
        if not os.path.exists(dest):
            print(f"  [setup]   {title} ...")
            _download(url, dest)
    print("  [setup] Checkpoints ready.")


def image_to_musicxml(img_path: str, output_dir: str) -> dict:
    """
    Run oemer on one PNG image.
    Returns a dict with:
      "mxl_path"   — path to the generated .musicxml file
      "oemer_img"  — BGR numpy array (oemer's internal image, same coords as bboxes)
      "notes"      — list of NoteHead-derived dicts  {bbox, label_name, color, sfn_name}
    """
    from oemer.ete import extract, clear_data
    from oemer import layers as oemer_layers

    os.makedirs(output_dir, exist_ok=True)
    args = types.SimpleNamespace(
        img_path      = img_path,
        output_path   = output_dir,
        use_tf        = False,
        save_cache    = False,
        without_deskew= False,
    )
    clear_data()
    mxl_path = extract(args)

    # Capture layer data immediately — clear_data() would erase it
    raw_notes  = oemer_layers.get_layer("notes")          # array of NoteHead objects
    oemer_img  = oemer_layers.get_layer("original_image") # BGR, same resolution as bboxes

    note_data = []
    if raw_notes is not None:
        for nh in raw_notes:
            if nh is None or getattr(nh, "invalid", True):
                continue
            label = getattr(nh, "label", None)
            lname = label.name if (label is not None and hasattr(label, "name")) else None
            sfn   = getattr(nh, "sfn",   None)
            sname = sfn.name   if (sfn   is not None and hasattr(sfn,   "name")) else None
            color = _DUR_COLOR.get(lname, _DEFAULT_COLOR)
            note_data.append({
                "bbox":       list(nh.bbox),   # [x1, y1, x2, y2]
                "label_name": lname,
                "sfn_name":   sname,
                "color":      color,
            })

    # Also capture barlines for dynamic detection
    barlines_layer = oemer_layers.get_layer("barlines")
    barlines_data  = list(barlines_layer) if barlines_layer is not None else []

    return {
        "mxl_path":  mxl_path,
        "oemer_img": oemer_img,
        "notes":     note_data,
        "barlines":  barlines_data,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step 2b — Image-based dynamic detection  (oemer outputs no dynamics)
# ─────────────────────────────────────────────────────────────────────────────

def _find_interstaff_y(gray_strip: np.ndarray) -> tuple:
    """
    Given a grayscale crop of a system (both treble and bass staves),
    find the y range of the gap between the two staves.
    Returns (gap_y_start, gap_y_end) relative to the strip.
    """
    h = gray_strip.shape[0]
    row_dark = np.array([(gray_strip[y] < 128).sum() for y in range(h)],
                        dtype=np.float32)
    # Smooth to suppress individual staff lines
    kernel   = np.ones(max(1, h // 15), dtype=np.float32) / max(1, h // 15)
    smoothed = np.convolve(row_dark, kernel, mode="same")

    # Look for the global minimum in the middle 50 % of the strip
    mid_s = h // 4
    mid_e = 3 * h // 4
    if mid_e <= mid_s:
        return (h // 3, 2 * h // 3)
    gap_y = mid_s + int(np.argmin(smoothed[mid_s:mid_e]))
    half  = max(15, h // 10)
    return (max(0, gap_y - half), min(h - 1, gap_y + half))


def _hough_hairpins(gray_strip: np.ndarray,
                    min_len: int = 40,
                    max_angle_deg: float = 40.0) -> list:
    """
    Detect crescendo / decrescendo hairpin wedges in a grayscale strip.
    Returns list of (x_start, x_end, is_crescendo: bool).
    """
    edges = cv2.Canny(gray_strip, 30, 90)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=18,
                            minLineLength=min_len, maxLineGap=15)
    if lines is None:
        return []

    diagonal = []
    for x1, y1, x2, y2 in lines[:, 0]:
        if x2 == x1:
            continue
        angle = abs(np.degrees(np.arctan2(abs(y2 - y1), abs(x2 - x1))))
        if 6.0 <= angle <= max_angle_deg:
            if x1 > x2:
                x1, y1, x2, y2 = x2, y2, x1, y1
            diagonal.append((x1, y1, x2, y2))

    if len(diagonal) < 2:
        return []

    diagonal.sort(key=lambda l: l[0])

    def y_at(line, x):
        x1, y1, x2, y2 = line
        if x2 == x1:
            return (y1 + y2) / 2.0
        t = (x - x1) / (x2 - x1)
        return y1 + t * (y2 - y1)

    wedges = []
    used   = set()
    for i, la in enumerate(diagonal):
        if i in used:
            continue
        for j, lb in enumerate(diagonal[i + 1:], i + 1):
            if j in used:
                continue
            # Lines must substantially overlap in x
            x_ov_s = max(la[0], lb[0])
            x_ov_e = min(la[2], lb[2])
            if x_ov_e - x_ov_s < min_len * 0.5:
                continue
            x_s = min(la[0], lb[0])
            x_e = max(la[2], lb[2])
            if x_e - x_s > 700:       # too wide for a single hairpin
                continue
            d_start = abs(y_at(la, x_s) - y_at(lb, x_s))
            d_end   = abs(y_at(la, x_e) - y_at(lb, x_e))
            close   = min(d_start, d_end)
            far     = max(d_start, d_end)
            if close > 25 or far < 20:  # not a proper wedge shape
                continue
            is_cresc = (d_start < d_end)   # diverging = crescendo
            wedges.append((x_s, x_e, is_cresc))
            used.add(i)
            used.add(j)
            break

    return wedges


def detect_dynamics(orig_bgr: np.ndarray,
                    barlines_data: list,
                    xml_path: str,
                    oemer_notes_layer) -> list:
    """
    Detect dynamic markings from the score image.

    Combines two approaches:
      1. Connected-component text detection in the inter-staff gap
         → finds pp / p / f / ff etc. at specific offsets
      2. Hough-line hairpin detection within staff regions
         → finds crescendo / decrescendo spans

    Returns list of event dicts:
      {'type': 'mark',    'offset': float,        'value': float}
      {'type': 'hairpin', 'offset_start': float,
                          'offset_end':   float,
                          'value_start':  float,
                          'value_end':    float}
    """
    gray  = cv2.cvtColor(orig_bgr, cv2.COLOR_BGR2GRAY)
    score = music21.converter.parse(xml_path)

    # ── Build note-group bounds (y_min, y_max, x_min) per group ──────────────
    group_bounds = {}
    for n in oemer_notes_layer:
        if n is None or getattr(n, "invalid", True):
            continue
        g = n.group
        y1, y2 = n.bbox[1], n.bbox[3]
        x1      = n.bbox[0]
        if g not in group_bounds:
            group_bounds[g] = [y1, y2, x1]
        else:
            group_bounds[g][0] = min(group_bounds[g][0], y1)
            group_bounds[g][1] = max(group_bounds[g][1], y2)
            group_bounds[g][2] = min(group_bounds[g][2], x1)

    # ── Build x → beat-offset anchors via barlines + music21 measures ────────
    blines_by_group = defaultdict(list)
    for bl in barlines_data:
        blines_by_group[bl.group].append((bl.bbox[0] + bl.bbox[2]) // 2)
    for g in blines_by_group:
        blines_by_group[g].sort()

    # music21 measure start offsets (first part only)
    m21_measure_offsets = []
    for part in score.parts[:1]:
        for m in part.getElementsByClass(m21stream.Measure):
            m21_measure_offsets.append(float(m.offset))
    m21_measure_offsets.sort()

    # Assign measures to groups in order
    anchors_by_group = {}
    cursor = 0
    for g in sorted(blines_by_group.keys()):
        blines    = blines_by_group[g]
        n_bars    = len(blines)
        grp_offs  = m21_measure_offsets[cursor: cursor + n_bars]
        cursor   += n_bars
        if not grp_offs or g not in group_bounds:
            continue
        x_left = group_bounds[g][2]
        anchors = [(x_left, grp_offs[0])]
        for bl_x, off in zip(blines, grp_offs):
            anchors.append((bl_x, off))
        anchors_by_group[g] = anchors

    def x_to_offset(x: int, g: int) -> float:
        anch = anchors_by_group.get(g)
        if not anch:
            return 0.0
        if x <= anch[0][0]:
            return float(anch[0][1])
        if x >= anch[-1][0]:
            return float(anch[-1][1])
        for k in range(len(anch) - 1):
            x0, o0 = anch[k]
            x1, o1 = anch[k + 1]
            if x0 <= x <= x1:
                t = (x - x0) / max(1, x1 - x0)
                return o0 + t * (o1 - o0)
        return float(anch[-1][1])

    events = []

    # ── 1. Detect dynamic text in the inter-staff gap ─────────────────────────
    for g, (y_min, y_max, x_left) in sorted(group_bounds.items()):
        strip     = gray[y_min:y_max, :]
        gap_h     = strip.shape[0]
        gs, ge    = _find_interstaff_y(strip)
        gap_height = max(1, ge - gs)
        # Widen the scan region slightly to catch markings above the gap
        gs_wide   = max(0, gs - 20)
        ge_wide   = min(gap_h, ge + 10)
        scan_h    = ge_wide - gs_wide

        # Only inspect the left side (first ~550 px) for dynamic markings
        scan_w    = min(550, strip.shape[1])
        region    = strip[gs_wide:ge_wide, :scan_w]
        _, gap_bw = cv2.threshold(region, 128, 255, cv2.THRESH_BINARY_INV)

        n_lab, _, stats, centroids = cv2.connectedComponentsWithStats(gap_bw)
        blobs = []
        for i in range(1, n_lab):
            bx, by, bw, bh, area = stats[i][:5]
            # Filter 1: barlines / system brackets — span most of the scan height
            if bh >= scan_h * 0.60:
                continue
            # Filter 2: long horizontal strokes (staff lines, long slurs)
            if bw > max(5 * bh, 60):
                continue
            # Filter 3: blobs too large to be a single letter
            if area > 700:
                continue
            # Keep text-sized blobs
            if area >= 25 and bh >= 7 and bw >= 4:
                cx  = int(centroids[i][0])
                cy  = int(centroids[i][1])    # y-centroid within scan region
                ar  = bh / max(bw, 1)          # aspect ratio h/w
                blobs.append((cx, area, bw, bh, ar, cy))

        # Cluster blobs within 60 px horizontally
        blobs.sort(key=lambda b: b[0])
        clusters = []
        for blob in blobs:
            cx = blob[0]
            if clusters and cx - clusters[-1][-1][0] < 60:
                clusters[-1].append(blob)
            else:
                clusters.append([blob])

        # Only take the LEFTMOST cluster (initial system dynamic, within 250 px
        # of the system's first note x-position).  Later blobs in the scan are
        # usually false positives from slurs, beams, or other symbols.
        leftmost = None
        for cluster in clusters:
            avg_x = int(np.mean([b[0] for b in cluster]))
            if avg_x <= x_left + 250:
                if leftmost is None or avg_x < leftmost[0]:
                    leftmost = (avg_x, cluster)

        if leftmost is None:
            continue
        avg_x, cluster = leftmost
        total_area  = sum(b[1] for b in cluster)
        n_letters   = len(cluster)
        # Average aspect ratio (h/w) — "f" family: tall/narrow (ar > 1.1)
        #                              "p" family: squat/square (ar ≤ 1.1)
        avg_ar      = np.mean([b[4] for b in cluster])
        is_f_family = (avg_ar > 1.10)

        # Two closely-matched blobs → double letter (ff or pp).
        # Single blob → single letter (f or p).
        # Pixel area thresholds are tuned for 200 DPI print.
        if n_letters >= 2:
            per_letter = total_area / n_letters
            if per_letter >= 150:
                val = DYNAMIC_MAP["ff"] if is_f_family else DYNAMIC_MAP["pp"]
            else:
                val = DYNAMIC_MAP["pp"]
        else:
            # Single letter: use aspect ratio to pick f vs p family,
            # then area to pick single vs mf/mp.
            if is_f_family:
                val = DYNAMIC_MAP["f"] if total_area >= 150 else DYNAMIC_MAP["mf"]
            else:
                val = DYNAMIC_MAP["p"]  if total_area >= 80  else DYNAMIC_MAP["pp"]

        off = x_to_offset(avg_x, g)
        events.append({"type": "mark", "offset": off, "value": val})

    # ── 2. Detect hairpin wedges within each system ───────────────────────────
    for g, (y_min, y_max, x_left) in sorted(group_bounds.items()):
        system_strip = gray[y_min:y_max, :]
        wedges = _hough_hairpins(system_strip, min_len=40)

        for x_s, x_e, is_cresc in wedges:
            off_s = x_to_offset(x_s, g)
            off_e = x_to_offset(x_e, g)
            if off_e <= off_s + 0.1:
                continue
            # Dynamic value at start of hairpin from detected marks
            val_s = DEFAULT_DYNAMIC
            for ev in sorted(events, key=lambda e: e.get("offset",
                                                          e.get("offset_start", 0))):
                if ev["type"] == "mark" and ev["offset"] <= off_s:
                    val_s = ev["value"]
            val_e = _adjacent_dyn(val_s, louder=is_cresc)
            events.append({
                "type":         "hairpin",
                "offset_start": off_s,
                "offset_end":   off_e,
                "value_start":  val_s,
                "value_end":    val_e,
            })

    return sorted(events, key=lambda e: e.get("offset", e.get("offset_start", 0.0)))


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — MusicXML → note events via music21
# ─────────────────────────────────────────────────────────────────────────────

def musicxml_to_notes(xml_path: str,
                       time_offset:  float = 0.0,
                       default_bpm:  float = DEFAULT_BPM,
                       dyn_events:   list  = None) -> tuple:
    """
    Parse MusicXML → (notes, meta).

    notes: list[{hz, start, duration, dynamic}]
      start    — onset in quarter-note beats from the beginning of the piece
      duration — length in quarter-note beats (quarter=1, half=2, eighth=0.5, etc.)
    meta:  {"bpm": float, "bpm_beat": float, "time_signature": str}

    dyn_events: optional list from detect_dynamics().
    Dotted notes are handled automatically: music21 encodes augmentation
    dots in el.quarterLength (dotted quarter = 1.5 beats), so no extra
    logic is needed here.
    """
    score  = music21.converter.parse(xml_path)
    tempos = list(score.flatten().getElementsByClass(m21tempo.MetronomeMark))
    if tempos:
        bpm      = float(tempos[0].number)
        ref      = getattr(tempos[0], "referent", None)
        bpm_beat = float(ref.quarterLength) if ref is not None else 1.0
    else:
        bpm      = default_bpm
        bpm_beat = 1.0
    qsecs  = 60.0 / bpm

    ts_list = list(score.flatten().getElementsByClass(m21meter.TimeSignature))
    time_sig = (f"{ts_list[0].numerator}/{ts_list[0].denominator}"
                if ts_list else "4/4")

    # ── music21 snapshot dynamics (from MusicXML, if any) ────────────────────
    m21_marks = sorted(
        ((float(d.offset), DYNAMIC_MAP.get(d.value, DEFAULT_DYNAMIC))
         for d in score.flatten().getElementsByClass(m21dyn.Dynamic)),
        key=lambda x: x[0],
    )
    # ── music21 hairpins (Crescendo / Diminuendo), if oemer emits them ───────
    m21_hairpins = []
    for cls in (m21dyn.Crescendo, m21dyn.Diminuendo):
        for h in score.flatten().getElementsByClass(cls):
            spanned = h.getSpannedElements()
            if len(spanned) < 2:
                continue
            off_s = float(spanned[0].offset)
            off_e = float(spanned[-1].offset) + float(spanned[-1].quarterLength)
            is_c  = isinstance(h, m21dyn.Crescendo)
            m21_hairpins.append((off_s, off_e, is_c))

    # ── merge image-based events with music21 events ──────────────────────────
    # music21 marks take priority (if oemer ever outputs them in the future)
    all_marks    = list(m21_marks)
    all_hairpins = list(m21_hairpins)

    if dyn_events:
        for ev in dyn_events:
            if ev["type"] == "mark":
                all_marks.append((ev["offset"], ev["value"]))
            elif ev["type"] == "hairpin":
                all_hairpins.append((
                    ev["offset_start"], ev["offset_end"],
                    ev["value_end"] > ev["value_start"],  # is_crescendo
                ))
        all_marks.sort(key=lambda x: x[0])

    def dynamic_at(offset: float) -> float:
        # Latest snapshot mark at or before this offset
        val = DEFAULT_DYNAMIC
        for doff, dval in all_marks:
            if doff <= offset:
                val = dval
            else:
                break
        # Override with a hairpin if this offset falls inside one
        for (hs, he, is_c) in all_hairpins:
            if hs <= offset <= he and he > hs:
                t     = (offset - hs) / (he - hs)
                # Find dynamic at hairpin start/end from snapshot marks
                v_s = DEFAULT_DYNAMIC
                for doff, dval in all_marks:
                    if doff <= hs: v_s = dval
                v_e = _adjacent_dyn(v_s, louder=is_c)
                # Check if there's an explicit mark near the end
                for doff, dval in all_marks:
                    if hs < doff <= he: v_e = dval
                val = v_s + t * (v_e - v_s)
                break
        return val

    notes = []
    for part in score.parts:
        for el in part.flatten().notesAndRests:
            if isinstance(el, m21note.Rest):
                continue
            offset   = float(el.offset)
            start    = round(time_offset + offset, 4)
            # el.quarterLength already includes augmentation dots
            # (dotted quarter = 1.5, dotted eighth = 0.75, etc.)
            duration = round(float(el.quarterLength), 4)
            dynamic  = round(dynamic_at(offset), 3)

            if isinstance(el, m21note.Note):
                notes.append({"hz": round(el.pitch.frequency, 3),
                               "start": start, "duration": duration, "dynamic": dynamic})
            elif isinstance(el, m21chord.Chord):
                for p in el.pitches:
                    notes.append({"hz": round(p.frequency, 3),
                                  "start": start, "duration": duration, "dynamic": dynamic})

    meta = {"bpm": bpm, "bpm_beat": bpm_beat, "time_signature": time_sig}
    return sorted(notes, key=lambda n: (n["start"], n["hz"])), meta


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Annotated PDF
# ─────────────────────────────────────────────────────────────────────────────

_DUR_ABBR = {
    "WHOLE": "W", "HALF": "H", "QUARTER": "Q",
    "EIGHTH": "8", "SIXTEENTH": "16",
}
_SFN_ABBR = {"SHARP": "#", "FLAT": "b", "NATURAL": "n"}


def draw_annotations(oemer_img: np.ndarray, note_data: list) -> np.ndarray:
    """
    Draw a colored rectangle + duration label on each detected notehead.
    Returns an annotated BGR copy.
    """
    ann  = oemer_img.copy()
    font = cv2.FONT_HERSHEY_SIMPLEX

    for nd in note_data:
        x1, y1, x2, y2 = nd["bbox"]
        color = nd["color"]
        cv2.rectangle(ann, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)

        # Small label above the box
        parts = []
        if nd["sfn_name"]:
            parts.append(_SFN_ABBR.get(nd["sfn_name"], "?"))
        parts.append(_DUR_ABBR.get(nd["label_name"], "?") if nd["label_name"] else "?")
        label = "".join(parts)
        cv2.putText(ann, label, (x1, max(y1 - 3, 12)),
                    font, 0.30, color, 1, cv2.LINE_AA)

    # Legend — bottom-left corner
    lx, ly = 10, ann.shape[0] - 10
    for dur_name, color in reversed(list(_DUR_COLOR.items())):
        abbr = _DUR_ABBR.get(dur_name, dur_name)
        cv2.rectangle(ann, (lx, ly - 14), (lx + 14, ly), color, -1)
        cv2.putText(ann, f"{abbr} = {dur_name.capitalize()}",
                    (lx + 18, ly - 2), font, 0.40, color, 1, cv2.LINE_AA)
        ly -= 22

    return ann


def images_to_pdf(bgr_pages: list, out_path: str, dpi: int = 200) -> None:
    """Embed a list of BGR numpy arrays into a PDF, one image per page."""
    doc = fitz.open()
    for bgr in bgr_pages:
        h, w   = bgr.shape[:2]
        pw, ph = w * 72 / dpi, h * 72 / dpi
        page   = doc.new_page(width=pw, height=ph)
        _, buf = cv2.imencode(".png", bgr)
        page.insert_image(fitz.Rect(0, 0, pw, ph), stream=buf.tobytes())
    doc.save(out_path)
    doc.close()


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def convert(pdf_path: str, bpm: float = DEFAULT_BPM,
            annotated_pdf: str = None) -> dict:
    """
    Full pipeline: PDF → {name, bpm, time_signature, notes}
    If annotated_pdf is given (or set to True → auto-name), also saves an
    annotated PDF showing every detected notehead coloured by duration type.
    """
    if annotated_pdf is True:
        annotated_pdf = os.path.splitext(pdf_path)[0] + "_annotated.pdf"

    print(f"\n{'─'*60}")
    print("Step 1/3  Rendering PDF pages to PNG...")
    img_paths, tmpdir = pdf_to_images(pdf_path)
    print(f"         {len(img_paths)} page(s) → {tmpdir}")

    print("Step 2/4  Checking oemer ONNX checkpoints...")
    _ensure_checkpoints()

    all_notes    = []
    ann_pages    = []
    time_offset  = 0.0
    score_meta   = {"bpm": bpm, "time_signature": "4/4"}
    piece_name   = os.path.splitext(os.path.basename(pdf_path))[0]

    for i, img_path in enumerate(img_paths):
        label   = f"page {i+1}/{len(img_paths)}"
        xml_dir = os.path.join(tmpdir, f"mxl_p{i+1}")

        print(f"\n         OMR (step 2/4) {label}  ({os.path.basename(img_path)})")
        try:
            result = image_to_musicxml(img_path, xml_dir)
        except Exception as exc:
            print(f"         oemer failed on {label}: {exc}")
            continue

        xml_path   = result["mxl_path"]
        oemer_img  = result["oemer_img"]
        note_data  = result["notes"]
        barlines   = result["barlines"]
        print(f"         MusicXML  → {xml_path}")
        print(f"         Noteheads detected: {len(note_data)}")

        # ── Detect dynamics from image (oemer emits no dynamic markings) ──────
        print(f"         Detecting dynamics from image...")
        from oemer import layers as _oemer_layers
        _oemer_notes_layer = _oemer_layers.get_layer("notes")
        try:
            dyn_ev = detect_dynamics(oemer_img, barlines, xml_path,
                                     _oemer_notes_layer)
            marks   = [e for e in dyn_ev if e["type"] == "mark"]
            hairpins = [e for e in dyn_ev if e["type"] == "hairpin"]
            print(f"         Dynamic marks: {len(marks)}  Hairpins: {len(hairpins)}")
        except Exception as exc:
            print(f"         Dynamic detection failed ({exc}); using defaults")
            dyn_ev = None

        print(f"Step 4/4  Parsing MusicXML ({label})...")
        page_notes, page_meta = musicxml_to_notes(xml_path, time_offset=time_offset,
                                                   default_bpm=bpm, dyn_events=dyn_ev)
        if i == 0:
            score_meta = page_meta   # use first page for global metadata
        if page_notes:
            time_offset = max(n["start"] + n["duration"] for n in page_notes)
        all_notes.extend(page_notes)
        print(f"         {len(page_notes)} note events extracted")

        # Build annotated image for this page
        if annotated_pdf and oemer_img is not None:
            ann_pages.append(draw_annotations(oemer_img, note_data))

    shutil.rmtree(tmpdir, ignore_errors=True)

    if annotated_pdf and ann_pages:
        images_to_pdf(ann_pages, annotated_pdf)
        print(f"\n{'─'*60}")
        print(f"Annotated PDF → {annotated_pdf}")

    print(f"{'─'*60}")
    sorted_notes = sorted(all_notes, key=lambda n: (n["start"], n["hz"]))
    return {
        "name":           piece_name,
        "bpm":            score_meta["bpm"],
        "bpm_beat":       score_meta["bpm_beat"],
        "time_signature": score_meta["time_signature"],
        "notes":          sorted_notes,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pdf = (sys.argv[1] if len(sys.argv) > 1
           else os.path.join(os.path.dirname(os.path.abspath(__file__)), "test.pdf"))

    if not os.path.exists(pdf):
        print(f"PDF not found: {pdf}")
        sys.exit(1)

    stem    = os.path.splitext(pdf)[0]
    ann_pdf = stem + "_annotated.pdf"
    out_json = stem + "_notes.json"

    import time as _time
    t0 = _time.time()

    print(f"Converting: {pdf}")
    result = convert(pdf, annotated_pdf=ann_pdf)

    elapsed = _time.time() - t0
    notes   = result["notes"]

    print(f"\nPiece name  : {result['name']}")
    print(f"BPM         : {result['bpm']}  (beat = {result['bpm_beat']} quarter lengths)")
    print(f"Time sig    : {result['time_signature']}")
    print(f"Total notes : {len(notes)}")
    if notes:
        piece_start  = min(n["start"] for n in notes)
        piece_end    = max(n["start"] + n["duration"] for n in notes)
        piece_beats  = piece_end - piece_start
        bpm_val      = result["bpm"]
        piece_dur_s  = piece_beats * (60.0 / bpm_val)
        dyn_vals     = [n["dynamic"] for n in notes]

        print(f"Piece start : {piece_start:.3f} beats")
        print(f"Piece end   : {piece_end:.3f} beats")
        print(f"Total time  : {piece_beats:.1f} beats  ({piece_dur_s:.1f} s / {piece_dur_s/60:.2f} min)")
        print(f"Dynamic     : min={min(dyn_vals):.3f}  max={max(dyn_vals):.3f}"
              f"  avg={sum(dyn_vals)/len(dyn_vals):.3f}")

    print(f"Process time: {elapsed:.1f} s")
    print()
    print("First 10 notes:")
    for n in notes[:10]:
        print(f"  {n}")
    if len(notes) > 10:
        print(f"  … and {len(notes) - 10} more")

    with open(out_json, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Notes JSON  → {out_json}")
