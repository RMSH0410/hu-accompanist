"""
Music Score PDF → Note Data Converter

Produces: list[{"hz": float, "start": float, "duration": float, "dynamic": float}]
  hz       — frequency in Hz (A4 = 440)
  start    — note onset in seconds from the beginning of the piece
  duration — note duration in seconds
  dynamic  — 0.0 (silent) … 1.0 (fortissimo); default 0.6 (mf)

Usage:
    python test.py               # processes test.pdf → notes JSON + annotated PDF
    python test.py <path.pdf>    # processes a custom PDF
"""

import os
import sys
import json
import numpy as np
import cv2
import fitz  # PyMuPDF

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_BPM       = 120
BEATS_PER_MEASURE = 4       # assumed 4/4
DEFAULT_DYNAMIC   = 0.60    # mf

# Diatonic semitone offsets from C: C=0 D=2 E=4 F=5 G=7 A=9 B=11
DIATONIC   = [0, 2, 4, 5, 7, 9, 11]
NOTE_NAMES = ["C", "D", "E", "F", "G", "A", "B"]

DYNAMIC_MAP = {
    "ppp": 0.05, "pp": 0.15, "p": 0.25,
    "mp":  0.40, "mf": 0.60,
    "f":   0.75, "ff": 0.88, "fff": 0.95,
}

# Duration in beats
DURATION_BEATS = {
    "whole": 4.0, "half": 2.0, "quarter": 1.0,
    "eighth": 0.5, "sixteenth": 0.25,
}

# Key signature: number of sharps (+) / flats (-) → note name → semitone delta
KEY_ACCIDENTALS: dict = {
     0: {},
     1: {"F": 1},
     2: {"F": 1, "C": 1},
     3: {"F": 1, "C": 1, "G": 1},
     4: {"F": 1, "C": 1, "G": 1, "D": 1},
     5: {"F": 1, "C": 1, "G": 1, "D": 1, "A": 1},
     6: {"F": 1, "C": 1, "G": 1, "D": 1, "A": 1, "E": 1},
     7: {"F": 1, "C": 1, "G": 1, "D": 1, "A": 1, "E": 1, "B": 1},
    -1: {"B": -1},
    -2: {"B": -1, "E": -1},
    -3: {"B": -1, "E": -1, "A": -1},
    -4: {"B": -1, "E": -1, "A": -1, "D": -1},
    -5: {"B": -1, "E": -1, "A": -1, "D": -1, "G": -1},
    -6: {"B": -1, "E": -1, "A": -1, "D": -1, "G": -1, "C": -1},
    -7: {"B": -1, "E": -1, "A": -1, "D": -1, "G": -1, "C": -1, "F": -1},
}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 – PDF → binary images
# ─────────────────────────────────────────────────────────────────────────────

def load_pages(pdf_path: str, dpi: int = 300) -> list:
    doc   = fitz.open(pdf_path)
    zoom  = dpi / 72.0
    mat   = fitz.Matrix(zoom, zoom)
    pages = []
    for page in doc:
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, 3)
        pages.append(arr.copy())
    doc.close()
    return pages


def binarize(rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 – Staff detection & removal
# ─────────────────────────────────────────────────────────────────────────────

def find_staff_systems(binary: np.ndarray) -> dict:
    """
    Detect 5-line staff systems via horizontal morphological filtering.
    Returns {"systems": [[y1..y5], ...], "staff_space": int}
    y values are in page (top-down) coordinates.
    """
    h, w    = binary.shape
    inv     = cv2.bitwise_not(binary)
    close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (8, 1))
    closed  = cv2.morphologyEx(inv, cv2.MORPH_CLOSE, close_k)
    open_k  = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 12, 50), 1))
    long_h  = cv2.morphologyEx(closed, cv2.MORPH_OPEN, open_k)

    row_sum = np.sum(long_h > 0, axis=1)
    cands   = np.where(row_sum > 0)[0]
    if len(cands) == 0:
        return {"systems": [], "staff_space": 20}

    grps, grp = [], [int(cands[0])]
    for r in cands[1:]:
        if r - grp[-1] <= 4:
            grp.append(int(r))
        else:
            grps.append(grp); grp = [int(r)]
    grps.append(grp)
    all_ys = [int(np.median(g)) for g in grps]

    if len(all_ys) < 5:
        return {"systems": [], "staff_space": 20}

    gaps      = np.diff(all_ys)
    sane      = gaps[(gaps >= 8) & (gaps <= 150)]
    ss        = int(np.median(sane)) if len(sane) else 20

    systems = []
    for i in range(len(all_ys) - 4):
        win = all_ys[i: i + 5]
        if not all(abs(g - ss) / ss < 0.35 for g in np.diff(win)):
            continue
        if systems and win[0] <= systems[-1][-1]:
            continue
        fill = sum(np.sum(long_h[max(0, y-2):y+3, :] > 0) / w for y in win) / 5
        if fill >= 0.20:
            systems.append(list(win))

    return {"systems": systems, "staff_space": ss}


def erase_staff_lines(binary: np.ndarray, systems: list, ss: int) -> np.ndarray:
    out = binary.copy()
    t   = max(2, ss // 8)
    for sys_ in systems:
        for y in sys_:
            out[max(0, y - t): y + t + 1, :] = 255
    return out


def inpaint_staff_cuts(no_staff: np.ndarray, systems: list, ss: int) -> np.ndarray:
    H, W = no_staff.shape
    t    = max(2, ss // 8)
    mask = np.zeros((H, W), dtype=np.uint8)
    for sys_ in systems:
        for y in sys_:
            mask[max(0, y - t): y + t + 1, :] = 255
    restored = cv2.inpaint(no_staff, mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
    _, binary = cv2.threshold(restored, 127, 255, cv2.THRESH_BINARY)
    return binary


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 – Clef detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_clef(binary: np.ndarray, system: list, ss: int) -> str:
    """
    Treble clef has a tall ascending stroke extending well above the top staff
    line (~3 × staff_space).  Bass clef stays compact within the staff.
    We compare ink density above vs within the system in the first ~4-ss columns.
    """
    H, W   = binary.shape
    y_top  = system[0]
    y_bot  = system[-1]
    x_end  = min(W, ss * 4)
    above  = max(0, y_top - ss * 3)
    region = cv2.bitwise_not(binary[above: y_bot + ss, :x_end])
    ah     = y_top - above
    sh     = y_bot - y_top
    above_ink  = int(np.sum(region[:ah, :] > 0))
    within_ink = int(np.sum(region[ah: ah + sh, :] > 0))
    total      = above_ink + within_ink
    if total > 0 and above_ink / total > 0.12:
        return "treble"
    return "bass"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 – Key signature detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_key_signature(no_staff: np.ndarray, system: list, ss: int) -> int:
    """
    Count accidental symbols (sharps / flats) in the key-signature zone.
    Returns +n for n sharps, -n for n flats, 0 for C major / A minor.
    """
    H, W = no_staff.shape
    y0   = max(0, system[0] - ss)
    y1   = min(H, system[-1] + ss)
    x0   = int(ss * 3.0)
    x1   = min(W, int(ss * 10.0))

    crop = cv2.bitwise_not(no_staff[y0:y1, x0:x1])
    if crop.size == 0:
        return 0

    n_lab, _, stats, cents = cv2.connectedComponentsWithStats(crop, connectivity=8)
    accs = []
    for i in range(1, n_lab):
        bx, by, bw, bh, area = stats[i]
        if not (ss * 1.2 < bh < ss * 4.0):
            continue
        if bw > bh * 0.8 or area < ss * 4:
            continue
        accs.append((float(cents[i][0]), float(cents[i][1])))

    count = len(accs)
    if count == 0 or count > 7:
        return 0

    row_sum = np.sum(crop > 0, axis=1)
    mid     = len(row_sum) // 2
    top_ink = int(np.sum(row_sum[:mid]))
    bot_ink = int(np.sum(row_sum[mid:]))
    if bot_ink > top_ink * 1.4:
        return -count   # flats
    return count        # sharps


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 – Pitch utilities
# ─────────────────────────────────────────────────────────────────────────────

def step_to_midi(staff_step: int, clef: str = "treble") -> int:
    """
    staff_step 0 = bottom staff line, increases going up (each step = one diatonic note).
    Treble clef bottom line = E4 (MIDI 64).
    Bass   clef bottom line = G2 (MIDI 43).
    """
    if clef == "treble":
        base_idx, base_oct = 2, 4   # E4
    else:
        base_idx, base_oct = 4, 2   # G2
    absolute = base_idx + staff_step
    note_idx = absolute % 7
    octave   = base_oct + absolute // 7
    return 12 * (octave + 1) + DIATONIC[note_idx]


def note_name_at_step(staff_step: int, clef: str = "treble") -> str:
    base = 2 if clef == "treble" else 4
    return NOTE_NAMES[(base + staff_step) % 7]


def midi_to_hz(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 – Notehead detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_noteheads(strip: np.ndarray, ss: int, y_offset: int = 0) -> list:
    """
    Find notehead blobs in a staff-line-removed binary strip.
    Returns list of dicts with cx, cy (strip coords), cy_page, x, y, w, h, filled.

    Two passes:
      A – morphological opening removes thin strokes → detects filled noteheads.
      B – full connected-component scan with fill/convexity filters → open noteheads.
    """
    inv     = cv2.bitwise_not(strip)
    min_sz  = int(ss * 0.35)
    max_sz  = int(ss * 1.20)
    found   = []
    used    = []   # (x0, y0, x1, y1) of already-claimed regions

    # ── Pass A: filled noteheads ─────────────────────────────────────────────
    r      = max(2, ss // 5)
    ell_k  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*r+1, 2*r+1))
    opened = cv2.morphologyEx(inv, cv2.MORPH_OPEN, ell_k)

    n, labs, stats, cents = cv2.connectedComponentsWithStats(opened, connectivity=8)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (min_sz < w < max_sz * 1.5 and min_sz < h < max_sz):
            continue
        asp = w / h if h > 0 else 0
        if not (0.5 < asp < 2.2):
            continue
        fill = area / (w * h)
        if fill < 0.40:
            continue
        cx, cy = float(cents[i][0]), float(cents[i][1])
        found.append({
            "cx": cx, "cy": cy, "cy_page": cy + y_offset,
            "x": x, "y": y, "w": w, "h": h,
            "filled": True, "fill_ratio": fill,
        })
        used.append((x, y, x + w, y + h))

    # ── Pass B: open noteheads (half / whole) ────────────────────────────────
    n2, labs2, stats2, cents2 = cv2.connectedComponentsWithStats(inv, connectivity=8)
    for i in range(1, n2):
        x, y, w, h, area = stats2[i]
        if not (min_sz < w < max_sz * 1.8 and min_sz < h < max_sz * 1.2):
            continue
        asp = w / h if h > 0 else 0
        if not (0.5 < asp < 2.5):
            continue
        fill = area / (w * h)
        if not (0.18 < fill < 0.58):
            continue
        cx2, cy2 = float(cents2[i][0]), float(cents2[i][1])
        if any(rx0 < cx2 < rx1 and ry0 < cy2 < ry1 for rx0, ry0, rx1, ry1 in used):
            continue
        # Convexity check: open noteheads have high convex-hull fill
        mask_i = (labs2[y:y+h, x:x+w] == i).astype(np.uint8) * 255
        cnts, _ = cv2.findContours(mask_i, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        hull_area = cv2.contourArea(cv2.convexHull(cnts[0]))
        if hull_area < 1 or area / hull_area < 0.55:
            continue
        found.append({
            "cx": cx2, "cy": cy2, "cy_page": cy2 + y_offset,
            "x": x, "y": y, "w": w, "h": h,
            "filled": False, "fill_ratio": fill,
        })
        used.append((x, y, x + w, y + h))

    return found


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 – Duration classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_duration(nh: dict, inv_strip: np.ndarray, ss: int) -> str:
    x, y, w, h = nh["x"], nh["y"], nh["w"], nh["h"]
    H, W = inv_strip.shape
    cx   = x + w // 2

    def col_density(y0: int, y1: int) -> float:
        seg = inv_strip[max(0, y0):min(H, y1), max(0, cx-2):min(W, cx+3)]
        return float(np.count_nonzero(seg)) / max(seg.size, 1)

    look   = int(ss * 3.5)
    d_up   = col_density(y - look, y)
    d_dn   = col_density(y + h, y + h + look)
    has_stem = max(d_up, d_dn) > 0.25

    if not nh["filled"]:
        return "half" if has_stem else "whole"

    flags = _count_beam_flags(x, y, w, h, inv_strip, ss, stem_up=(d_up >= d_dn))
    if flags >= 2:
        return "sixteenth"
    if flags == 1:
        return "eighth"
    return "quarter"


def _count_beam_flags(x: int, y: int, w: int, h: int,
                       inv: np.ndarray, ss: int, stem_up: bool) -> int:
    H, W = inv.shape
    if stem_up:
        ty0, ty1 = max(0, y - int(ss * 4.5)), max(0, y - int(ss * 1.5))
    else:
        ty0, ty1 = min(H, y + h + int(ss * 1.5)), min(H, y + h + int(ss * 4.5))
    if ty1 <= ty0:
        return 0

    region  = inv[ty0:ty1, max(0, x - ss): min(W, x + w + ss)]
    row_ink = np.sum(region > 0, axis=1)
    thresh  = max(ss * 0.4, 3)
    br      = np.where(row_ink > thresh)[0]
    if len(br) == 0:
        return 0

    grps, grp = [], [br[0]]
    for r in br[1:]:
        if r - grp[-1] <= max(3, ss // 4):
            grp.append(r)
        else:
            grps.append(grp); grp = [r]
    grps.append(grp)
    return min(len(grps), 2)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 – Barline detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_barlines(no_staff: np.ndarray, system: list, ss: int) -> list:
    H, W  = no_staff.shape
    y0    = max(0, system[0] - ss // 2)
    y1    = min(H, system[-1] + ss // 2)
    sys_h = y1 - y0
    inv   = cv2.bitwise_not(no_staff[y0:y1, :])
    vk_h  = max(int(sys_h * 0.60), ss * 3)
    vk    = cv2.getStructuringElement(cv2.MORPH_RECT, (1, vk_h))
    tall  = cv2.morphologyEx(inv, cv2.MORPH_OPEN, vk)
    col_s = np.sum(tall > 0, axis=0)
    cols  = np.where(col_s > sys_h * 0.50)[0]
    if len(cols) == 0:
        return []
    grps, grp = [], [int(cols[0])]
    for c in cols[1:]:
        if c - grp[-1] <= 4:
            grp.append(int(c))
        else:
            grps.append(grp); grp = [int(c)]
    grps.append(grp)
    return [int(np.median(g)) for g in grps]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 – Dynamic detection via OCR
# ─────────────────────────────────────────────────────────────────────────────

def scan_dynamics(gray: np.ndarray, system: list, ss: int) -> list:
    """
    OCR the strip below the staff for dynamic markings (pp, p, mp, mf, f, ff, …).
    Returns [(x_position, dynamic_value), …] sorted by x.
    Returns [] silently if pytesseract is not installed.
    """
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError:
        return []

    H, W = gray.shape
    y0   = min(H, system[-1] + ss // 2)
    y1   = min(H, system[-1] + ss * 4)
    if y1 <= y0:
        return []

    data     = pytesseract.image_to_data(
        gray[y0:y1, :], config="--psm 6 --oem 3",
        output_type=Output.DICT
    )
    markings = []
    for i, t in enumerate(data["text"]):
        t = t.strip().lower()
        if not t:
            continue
        for key in sorted(DYNAMIC_MAP, key=len, reverse=True):
            if key in t:
                xm = int(data["left"][i]) + int(data["width"][i]) // 2
                markings.append((xm, DYNAMIC_MAP[key]))
                break
    return sorted(markings, key=lambda m: m[0])


# ─────────────────────────────────────────────────────────────────────────────
# Annotation helpers
# ─────────────────────────────────────────────────────────────────────────────

# BGR colors per note duration
_DUR_COLOR = {
    "whole":     (255, 80,  80 ),
    "half":      (255, 180, 0  ),
    "quarter":   (0,   200, 0  ),
    "eighth":    (0,   180, 255),
    "sixteenth": (180, 0,   255),
}

_CHROMA_TO_NAME = {
    0: "C", 1: "C#", 2: "D", 3: "D#", 4: "E",
    5: "F", 6: "F#", 7: "G", 8: "G#", 9: "A", 10: "A#", 11: "B",
}
_DUR_ABBR = {
    "whole": "W", "half": "H", "quarter": "Q",
    "eighth": "8", "sixteenth": "16",
}


def _midi_label(midi: int, dur: str) -> str:
    name   = _CHROMA_TO_NAME[midi % 12]
    octave = midi // 12 - 1
    return f"{name}{octave}"


def _draw_note(img: np.ndarray, cx: float, cy: float,
               dur: str, midi: int, ss: int) -> None:
    color = _DUR_COLOR.get(dur, (200, 200, 200))
    r     = max(ss // 2 + 2, 8)
    cv2.circle(img, (int(cx), int(cy)), r, color, 2, cv2.LINE_AA)
    label = _midi_label(midi, dur)
    cv2.putText(img, label, (int(cx) - r // 2, int(cy) - r - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1, cv2.LINE_AA)


def _draw_legend(img: np.ndarray, ss: int) -> None:
    x, y = 10, 10
    for dur, color in _DUR_COLOR.items():
        cv2.rectangle(img, (x, y), (x + 18, y + 18), color, -1)
        cv2.putText(img, dur, (x + 24, y + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        y += 24


def _images_to_pdf(bgr_pages: list, out_path: str, dpi: int = 300) -> None:
    """Save a list of BGR numpy arrays as a single PDF."""
    doc = fitz.open()
    for bgr in bgr_pages:
        h, w     = bgr.shape[:2]
        pw, ph   = w * 72 / dpi, h * 72 / dpi
        page     = doc.new_page(width=pw, height=ph)
        _, buf   = cv2.imencode(".png", bgr)
        page.insert_image(fitz.Rect(0, 0, pw, ph), stream=buf.tobytes())
    doc.save(out_path)
    doc.close()


# ─────────────────────────────────────────────────────────────────────────────
# Main converter
# ─────────────────────────────────────────────────────────────────────────────

def convert(pdf_path: str, bpm: float = DEFAULT_BPM,
            annotate: bool = False, out_pdf: str = None) -> list:
    """
    Convert a music-score PDF to a time-ordered list of note events.
    Returns list[{"hz": float, "start": float, "duration": float, "dynamic": float}]
    When annotate=True, also saves an annotated PDF to out_pdf (or <stem>_annotated.pdf).
    """
    if annotate and out_pdf is None:
        out_pdf = os.path.splitext(pdf_path)[0] + "_annotated.pdf"

    pages_rgb    = load_pages(pdf_path)
    notes        = []
    current_time = 0.0
    ann_pages    = []   # BGR images for annotated PDF (populated when annotate=True)

    for page_num, rgb in enumerate(pages_rgb):
        print(f"[page {page_num + 1}/{len(pages_rgb)}]", end=" ", flush=True)
        gray   = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        binary = binarize(rgb)

        staff_data = find_staff_systems(binary)
        systems    = staff_data["systems"]
        ss         = staff_data["staff_space"]

        if not systems:
            print("no staff found")
            if annotate:
                ann_pages.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            continue
        print(f"{len(systems)} system(s)  ss={ss}px")

        no_staff_raw = erase_staff_lines(binary, systems, ss)
        no_staff     = inpaint_staff_cuts(no_staff_raw, systems, ss)
        inv_full     = cv2.bitwise_not(no_staff)

        ann_img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR) if annotate else None

        for system in systems:
            clef    = detect_clef(binary, system, ss)
            key_n   = detect_key_signature(no_staff, system, ss)
            key_acc = KEY_ACCIDENTALS.get(key_n, {})

            barlines = detect_barlines(no_staff, system, ss)
            barlines = sorted(set([0] + barlines + [binary.shape[1]]))

            # Draw detected barlines on annotation image
            if annotate and ann_img is not None:
                for bx in barlines[1:-1]:  # skip page edges
                    cv2.line(ann_img, (bx, system[0] - ss), (bx, system[-1] + ss),
                             (0, 0, 180), 1, cv2.LINE_AA)
                # Draw staff line guides (thin gray)
                for ly in system:
                    cv2.line(ann_img, (0, ly), (ann_img.shape[1], ly),
                             (180, 180, 180), 1, cv2.LINE_AA)
                # Clef & key label
                cv2.putText(ann_img,
                            f"{clef[0].upper()}  key={'#'*key_n if key_n>0 else 'b'*(-key_n) if key_n<0 else 'C'}",
                            (8, system[0] - ss // 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.50, (100, 60, 200), 1, cv2.LINE_AA)

            dyn_events  = scan_dynamics(gray, system, ss)
            current_dyn = DEFAULT_DYNAMIC

            y0_sys    = max(0, system[0] - ss * 2)
            y1_sys    = min(binary.shape[0], system[-1] + ss * 2)
            strip     = no_staff[y0_sys:y1_sys, :]
            inv_strip = inv_full[y0_sys:y1_sys, :]

            noteheads = detect_noteheads(strip, ss, y_offset=y0_sys)

            if not noteheads:
                n_bars       = max(1, len(barlines) - 1)
                current_time += n_bars * BEATS_PER_MEASURE * 60.0 / bpm
                continue

            noteheads.sort(key=lambda n: n["cx"])

            chords = []
            for nh in noteheads:
                if chords and abs(nh["cx"] - chords[-1][0]["cx"]) < ss * 0.6:
                    chords[-1].append(nh)
                else:
                    chords.append([nh])

            for chord in chords:
                cx_chord = chord[0]["cx"]

                for dyn_x, dyn_val in dyn_events:
                    if dyn_x <= cx_chord:
                        current_dyn = dyn_val

                dur_name = min(
                    (classify_duration(nh, inv_strip, ss) for nh in chord),
                    key=lambda d: DURATION_BEATS[d]
                )
                dur_secs = DURATION_BEATS[dur_name] * 60.0 / bpm

                for nh in chord:
                    raw_step   = (system[-1] - nh["cy_page"]) / (ss / 2.0)
                    staff_step = int(round(raw_step))

                    midi  = step_to_midi(staff_step, clef)
                    name  = note_name_at_step(staff_step, clef)
                    midi += key_acc.get(name, 0)
                    hz    = midi_to_hz(midi)

                    notes.append({
                        "hz":       round(hz, 3),
                        "start":    round(current_time, 4),
                        "duration": round(dur_secs, 4),
                        "dynamic":  round(current_dyn, 3),
                    })

                    if annotate and ann_img is not None:
                        _draw_note(ann_img, nh["cx"], nh["cy_page"],
                                   dur_name, midi, ss)

                current_time += dur_secs

        if annotate and ann_img is not None:
            _draw_legend(ann_img, ss)
            ann_pages.append(ann_img)

    notes.sort(key=lambda n: n["start"])

    if annotate and ann_pages:
        _images_to_pdf(ann_pages, out_pdf)
        print(f"\nAnnotated PDF → {out_pdf}")

    return notes


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pdf = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "test.pdf"
    )
    if not os.path.exists(pdf):
        print(f"PDF not found: {pdf}")
        print("Restore with:  git restore --source=HEAD backend/test.pdf")
        sys.exit(1)

    print(f"Converting: {pdf}\n")
    ann_pdf = os.path.splitext(pdf)[0] + "_annotated.pdf"
    notes   = convert(pdf, annotate=True, out_pdf=ann_pdf)

    print(f"\nExtracted {len(notes)} notes.")
    for n in notes[:10]:
        print(" ", n)
    if len(notes) > 10:
        print(f"  … and {len(notes) - 10} more")

    out = os.path.splitext(pdf)[0] + "_notes.json"
    with open(out, "w") as f:
        json.dump(notes, f, indent=2)
    print(f"Notes JSON  → {out}")
