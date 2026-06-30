"""
AS you can see AI vibe code is a horrible idea
PDF music sheet parser — traditional image processing, no ML/AI.

Pipeline:
  PDF page → grayscale image → binarize → detect staff lines →
  remove staff lines → detect symbols (noteheads, stems, beams, barlines) →
  resolve pitch + duration → OCR text for markings → assemble Score
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import cv2
import fitz  # type: ignore  # PyMuPDF
import numpy as np

# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Note:
    pitch: Optional[str]       # "C4", "F#5" — None means rest
    onset_beat: float          # quarter-note beats from score start
    duration_beat: float       # quarter=1.0, half=2.0, whole=4.0, eighth=0.5 …
    dynamic: str = ""          # "p", "f", "mp", etc.
    articulations: list[str] = field(default_factory=list)
    is_rest: bool = False
    tied: bool = False

    def onset_seconds(self, tempo_bpm: int) -> float:
        return self.onset_beat / (tempo_bpm / 60)

    def duration_seconds(self, tempo_bpm: int) -> float:
        return self.duration_beat / (tempo_bpm / 60)


@dataclass
class Marking:
    """A performance directive at a specific position in the score."""
    type: str               # "rit", "accel", "cresc", "dim", "fermata", etc.
    onset_beat: float
    end_beat: Optional[float] = None   # None = point marking; float = span


@dataclass
class Measure:
    number: int
    time_sig: tuple[int, int]          # numerator, denominator
    notes: list[Note] = field(default_factory=list)
    markings: list[Marking] = field(default_factory=list)

    @property
    def beats(self) -> float:
        return self.time_sig[0]


@dataclass
class Part:
    name: str = ""
    clef: str = "treble"              # "treble", "bass", "alto"
    measures: list[Measure] = field(default_factory=list)


@dataclass
class Score:
    title: str = ""
    composer: str = ""
    tempo: int = 120               # BPM
    style: str = ""                # "Allegro", "Andante", …
    key_sig: str = "C"             # tonic note name
    time_sig: tuple[int, int] = (4, 4)
    parts: list[Part] = field(default_factory=list)
    markings: list[Marking] = field(default_factory=list)  # global / score-level


# ─── Constants ────────────────────────────────────────────────────────────────

_DIATONIC = ["C", "D", "E", "F", "G", "A", "B"]

# Diatonic index (octave*7 + note_in_octave) of the bottom staff line at step 8
# Treble: E4 = octave 4, note_idx 2 → 4*7+2 = 30
# Bass:   G2 = octave 2, note_idx 4 → 2*7+4 = 18
_CLEF_BOTTOM_DIATONIC = {"treble": 30, "bass": 18, "alto": 23}

# Key signature tables — which diatonic note names get an accidental.
# Order follows the circle of 5ths.
_FLAT_NOTES: list[list[str]] = [
    [],
    ["B"],
    ["B", "E"],
    ["B", "E", "A"],
    ["B", "E", "A", "D"],
    ["B", "E", "A", "D", "G"],
    ["B", "E", "A", "D", "G", "C"],
    ["B", "E", "A", "D", "G", "C", "F"],
]
_SHARP_NOTES: list[list[str]] = [
    [],
    ["F"],
    ["F", "C"],
    ["F", "C", "G"],
    ["F", "C", "G", "D"],
    ["F", "C", "G", "D", "A"],
    ["F", "C", "G", "D", "A", "E"],
    ["F", "C", "G", "D", "A", "E", "B"],
]

_RENDER_DPI = 200

# Text patterns for OCR output
_RE_DYNAMIC = re.compile(r"\b(pppp|ppp|pp|mp|mf|fff|ff|fp|sfz?|p|f)\b")
_RE_STYLE = re.compile(
    r"\b(Allegro|Andante|Moderato|Adagio|Largo|Vivace|Presto|Lento|Grave|"
    r"Allegretto|Andantino|Maestoso|Cantabile|Dolce|Scherzando|Agitato)\b",
    re.IGNORECASE,
)
_RE_BPM = re.compile(r"[=]\s*(\d{2,3})")
_RE_EXPR = re.compile(
    r"\b(rit(?:ard)?\.?|rall\.?|accel(?:erando)?\.?|cresc(?:endo)?\.?|"
    r"dim(?:inuendo)?\.?|decres(?:c)?\.?|a\s+tempo|poco\s+a\s+poco|"
    r"subito|sempre|molto|poco|fermata|dal\s+segno|da\s+capo|fine)\b",
    re.IGNORECASE,
)
_STYLE_DEFAULT_BPM = {
    "Grave": 40, "Largo": 50, "Lento": 55, "Adagio": 65,
    "Andante": 76, "Andantino": 84, "Moderato": 96,
    "Allegretto": 112, "Allegro": 132, "Vivace": 152, "Presto": 180,
}


# ─── Image Utilities ──────────────────────────────────────────────────────────

def _render_page(page: fitz.Page, dpi: int = _RENDER_DPI) -> np.ndarray:
    """Render a PDF page to a grayscale uint8 numpy array."""
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def _binarize(gray: np.ndarray) -> np.ndarray:
    """Return binary image where black ink pixels = 255, using Otsu's adaptive threshold."""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return binary


# ─── Staff Line Detection ─────────────────────────────────────────────────────

def _detect_staff_lines(binary: np.ndarray) -> list[list[int]]:
    """
    Find staves (groups of 5 horizontal lines) in the binary image.

    Returns a list of staves; each stave is [y0, y1, y2, y3, y4] — the mean
    row index of each of the 5 staff lines, top to bottom.
    """
    width = binary.shape[1]

    # Long horizontal kernel: keeps only lines that span most of the page width.
    h_len = max(1, width // 4)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1))
    staff_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)

    # Row projection — rows with many black pixels are staff lines.
    row_sums = staff_mask.sum(axis=1)
    threshold = width * 0.15 * 255
    candidate_rows = np.where(row_sums > threshold)[0]

    if len(candidate_rows) == 0:
        return []

    # Cluster consecutive rows into individual lines.
    lines: list[int] = []
    group = [int(candidate_rows[0])]
    for r in candidate_rows[1:]:
        if r - group[-1] <= 4:
            group.append(int(r))
        else:
            lines.append(int(np.mean(group)))
            group = [int(r)]
    lines.append(int(np.mean(group)))

    # Group every 5 lines into a stave.
    staves: list[list[int]] = []
    i = 0
    while i + 4 < len(lines):
        candidate = lines[i : i + 5]
        # Sanity check: spacing should be roughly equal.
        spacings = [candidate[k + 1] - candidate[k] for k in range(4)]
        mean_sp = np.mean(spacings)
        if all(abs(s - mean_sp) < mean_sp * 0.4 for s in spacings):
            staves.append(candidate)
            i += 5
        else:
            i += 1  # slide window if spacing is off

    return staves


def _remove_staff_lines(binary: np.ndarray) -> np.ndarray:
    """
    Remove staff lines while preserving notehead centroids.

    Uses morphological opening with a long horizontal kernel to identify only the
    long, continuous runs of ink that form staff lines.  Subtracting the mask
    from the binary image leaves a gap of ≤2 pixels at each line; connectivity-8
    CCs can bridge that gap, so noteheads that sit ON a line keep their centroid
    accurate instead of drifting by ~4 px (= ~0.6 diatonic steps) upward.
    """
    width = binary.shape[1]
    h_len = max(1, width // 5)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1))
    staff_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
    return cv2.subtract(binary, staff_mask)


def _group_staves_into_systems(staves: list[list[int]]) -> list[list[list[int]]]:
    """
    Group staves that belong to the same system (e.g. piano grand staff).

    Finds the threshold by locating the largest jump between consecutive sorted
    gap values.  Within-system gaps (treble→bass, ~2-4×) and between-system
    gaps (~7-12×) naturally form two clusters; the split lands in the valley
    between them regardless of the absolute values.
    """
    if not staves:
        return []
    if len(staves) == 1:
        return [[staves[0]]]

    # Compute normalised gaps (units of line_spacing of the upper stave).
    gaps: list[float] = []
    for i in range(1, len(staves)):
        ls = max((staves[i - 1][4] - staves[i - 1][0]) / 4, 1)
        gaps.append((staves[i][0] - staves[i - 1][4]) / ls)

    unique = sorted(set(gaps))
    if len(unique) == 1:
        return [list(staves)]

    # Find the biggest jump between consecutive unique gap values.
    best_jump, threshold = 0.0, unique[-1]
    for k in range(len(unique) - 1):
        jump = unique[k + 1] - unique[k]
        if jump > best_jump:
            best_jump = jump
            threshold = (unique[k] + unique[k + 1]) / 2

    systems: list[list[list[int]]] = []
    current_system = [staves[0]]
    for i, stave in enumerate(staves[1:]):
        if gaps[i] > threshold:
            systems.append(current_system)
            current_system = [stave]
        else:
            current_system.append(stave)
    systems.append(current_system)

    # Fallback: if the split produced unequal system sizes AND the total stave
    # count is even, the score is almost certainly a keyboard/piano score where
    # each system is one treble + one bass staff.  Pair them up.
    sizes = [len(s) for s in systems]
    if len(set(sizes)) > 1 and len(staves) % 2 == 0:
        systems = [list(staves[i : i + 2]) for i in range(0, len(staves), 2)]

    return systems


# ─── Symbol Detection ─────────────────────────────────────────────────────────

def _stave_roi(
    img: np.ndarray, stave: list[int], extra_above: int = 3, extra_below: int = 3
) -> tuple[np.ndarray, int]:
    """Extract the region of interest for one stave (with ledger-line margin)."""
    line_spacing = (stave[4] - stave[0]) / 4
    y_top = max(0, stave[0] - int(line_spacing * extra_above))
    y_bot = min(img.shape[0], stave[4] + int(line_spacing * extra_below))
    return img[y_top:y_bot, :], y_top


def _detect_noteheads(
    cleaned: np.ndarray,
    stave: list[int],
    clef: str = "treble",
    extra_below: int = 3,
) -> list[dict]:
    """
    Return list of notehead dicts: {cx, cy, w, h, filled}.
    cx/cy are in full-image coordinates.

    extra_below: ledger-line coverage below the bottom staff line (units of line_spacing).
      Use 2 for the top stave of a grand-staff system to avoid picking up dynamic markings
      that sit in the inter-stave gap, 3 for the bottom stave to catch very low notes.
    """
    line_spacing = (stave[4] - stave[0]) / 4
    # Treble/alto can have many ledger notes above; bass notes rarely exceed the top line.
    extra_above = 4 if clef in ("treble", "alto") else 1
    roi, y_offset = _stave_roi(cleaned, stave, extra_above=extra_above, extra_below=extra_below)

    # Strip stems (long thin verticals) before CC analysis so that notehead+stem
    # does NOT form one giant tall component that fails the max_h filter.
    stem_len = max(int(line_spacing * 1.5), 3)
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, stem_len))
    stem_mask = cv2.morphologyEx(roi, cv2.MORPH_OPEN, v_kernel)
    notehead_roi = cv2.subtract(roi, stem_mask)

    num, _, stats, centroids = cv2.connectedComponentsWithStats(notehead_roi, connectivity=8)

    noteheads = []
    min_area = line_spacing ** 2 * 0.15
    max_area = line_spacing ** 2 * 6.0    # notehead + short ledger line can reach ~4×
    min_w = line_spacing * 0.4
    min_h = line_spacing * 0.3
    max_h = line_spacing * 2.0            # relaxed: ledger-notehead combos can be taller

    for i in range(1, num):
        x, y, w, h, area = stats[i]
        if not (min_area < area < max_area):
            continue
        if w < min_w or h < min_h or h > max_h:
            continue
        aspect = w / max(h, 1)
        if not (0.4 < aspect < 4.0):
            continue

        # Fill density from original ROI (stems still present; gives real fill for hollow/filled).
        blob = roi[y : y + h, x : x + w]
        fill = blob.sum() / (255 * max(w * h, 1))
        filled = fill > 0.45

        noteheads.append(
            {
                "cx": int(centroids[i][0]),
                "cy": int(centroids[i][1]) + y_offset,
                "x": x,
                "y": y + y_offset,
                "w": w,
                "h": h,
                "filled": filled,
            }
        )

    # Discard the left-margin zone: clef + key sig (up to 4 flats/sharps) + time sig.
    # 22% comfortably clears all of these on a standard-width page at 200 DPI.
    left_margin = int(cleaned.shape[1] * 0.22)
    noteheads = [nh for nh in noteheads if nh["cx"] > left_margin]

    # Keep only blobs whose centroid falls within the stave's vertical influence zone.
    max_extra = max(extra_above, extra_below) * line_spacing
    noteheads = [
        nh for nh in noteheads
        if stave[0] - max_extra <= nh["cy"] <= stave[4] + max_extra
    ]

    # Deduplicate: when a hollow notehead is split by stem-removal into two arcs,
    # both arcs have nearly identical centroids.  Keep the one with larger area.
    noteheads.sort(key=lambda n: n["cx"])
    deduped: list[dict] = []
    for nh in noteheads:
        if deduped and abs(nh["cx"] - deduped[-1]["cx"]) < line_spacing * 0.5 and \
                abs(nh["cy"] - deduped[-1]["cy"]) < line_spacing * 0.5:
            if nh["w"] * nh["h"] > deduped[-1]["w"] * deduped[-1]["h"]:
                deduped[-1] = nh
        else:
            deduped.append(nh)
    return deduped


def _detect_stems(cleaned: np.ndarray, stave: list[int]) -> list[dict]:
    """
    Return list of stem dicts: {cx, top_y, bot_y}.
    Stems are tall, narrow vertical strokes.
    """
    line_spacing = (stave[4] - stave[0]) / 4
    roi, y_offset = _stave_roi(cleaned, stave)

    min_len = int(line_spacing * 1.5)   # shorter to catch grace-note stems
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_len))
    stem_mask = cv2.morphologyEx(roi, cv2.MORPH_OPEN, v_kernel)

    num, _, stats, centroids = cv2.connectedComponentsWithStats(stem_mask, connectivity=8)

    stems = []
    for i in range(1, num):
        _, y, w, h, _ = stats[i]
        if h < min_len or w > line_spacing * 0.5:
            continue
        stems.append(
            {
                "cx": int(centroids[i][0]),
                "top_y": y + y_offset,
                "bot_y": y + h + y_offset,
            }
        )
    return stems


def _detect_beams(cleaned: np.ndarray, stave: list[int]) -> list[dict]:
    """
    Return list of beam dicts: {x0, x1, y}.
    Beams are wide, moderately thick horizontal/diagonal bars.
    """
    line_spacing = (stave[4] - stave[0]) / 4
    roi, y_offset = _stave_roi(cleaned, stave)

    beam_h = max(2, int(line_spacing * 0.35))
    b_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (int(line_spacing * 1.2), beam_h)
    )
    beam_mask = cv2.morphologyEx(roi, cv2.MORPH_OPEN, b_kernel)

    num, _, stats, _ = cv2.connectedComponentsWithStats(beam_mask, connectivity=8)

    beams = []
    for i in range(1, num):
        x, y, w, h, _ = stats[i]
        if w < line_spacing * 1.2:
            continue
        # Reject noteheads masquerading as beams (too tall relative to width)
        if h > line_spacing * 0.7:
            continue
        beams.append({"x0": x, "x1": x + w, "y": y + y_offset})
    return beams


def _detect_barlines(binary: np.ndarray, stave: list[int]) -> list[int]:
    """
    Return x-coordinates of barlines — vertical strokes spanning the full staff.

    Uses a vertical morphological open whose kernel equals the full staff height.
    Only a stroke that is continuously black from the top staff line to the bottom
    line (i.e. a real barline) survives the open.  Stems are much shorter and are
    eliminated even though they are vertical.
    """
    staff_height = stave[4] - stave[0]
    line_spacing = staff_height / 4

    # Slice exactly the staff rows so stem lengths are measured correctly.
    staff_strip = binary[stave[0] : stave[4] + 1, :]

    # A kernel height of (staff_height - 2) tolerates 1-pixel rendering gaps.
    v_len = max(staff_height - 2, 3)
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len))
    bar_mask = cv2.morphologyEx(staff_strip, cv2.MORPH_OPEN, v_kernel)

    # Any column with a surviving pixel is a barline candidate.
    col_any = bar_mask.max(axis=0)
    bar_cols = np.where(col_any > 0)[0]

    if len(bar_cols) == 0:
        return []

    # Group adjacent columns into single barline x-positions.
    barlines = []
    group = [int(bar_cols[0])]
    for c in bar_cols[1:]:
        if c - group[-1] <= 4:
            group.append(int(c))
        else:
            barlines.append(int(np.mean(group)))
            group = [int(c)]
    barlines.append(int(np.mean(group)))

    # Discard the clef / key-sig / time-sig region at the left edge.
    # Match the notehead left_margin (0.22) so the opening-system barline is excluded
    # and all notes before barlines[0] correctly land in Measure 1 bucket (mi=0).
    first_note_x = int(binary.shape[1] * 0.22)
    barlines = [b for b in barlines if b > first_note_x]

    # A real measure must be at least 15 line-spacings wide.
    # This eliminates repeat dots, thick barlines being read as two barlines, etc.
    min_spacing = int(line_spacing * 15)
    filtered: list[int] = []
    for b in barlines:
        if not filtered or b - filtered[-1] >= min_spacing:
            filtered.append(b)
    return filtered


# ─── Pitch Resolution ─────────────────────────────────────────────────────────

def _cy_to_pitch(cy: int, stave: list[int], clef: str = "treble") -> str:
    """
    Map a notehead's center-y pixel to a pitch string ("C4", "F#5", …).

    Staff positions are counted in half-steps from the top line (position 0).
    Positive = downward. Each diatonic step = one half-position.
    """
    line_spacing = (stave[4] - stave[0]) / 4
    half_step_px = line_spacing / 2

    staff_step = round((cy - stave[0]) / half_step_px)

    # diatonic_index at bottom line (staff_step == 8) = _CLEF_BOTTOM_DIATONIC[clef]
    diatonic_idx = _CLEF_BOTTOM_DIATONIC.get(clef, 30) + (8 - staff_step)

    octave = diatonic_idx // 7
    note_name = _DIATONIC[diatonic_idx % 7]

    return f"{note_name}{octave}"


# ─── Duration Resolution ──────────────────────────────────────────────────────

def _count_beams_on_stem(stem: dict, beams: list[dict]) -> int:
    """Count how many beams cross over the given stem's x-coordinate."""
    count = 0
    for beam in beams:
        if beam["x0"] <= stem["cx"] <= beam["x1"]:
            if stem["top_y"] <= beam["y"] <= stem["bot_y"]:
                count += 1
    return count


def _assign_duration(
    notehead: dict, stems: list[dict], beams: list[dict], line_spacing: float
) -> float:
    """
    Return note duration in quarter-note beats.
    whole=4, half=2, quarter=1, eighth=0.5, sixteenth=0.25
    """
    cx = notehead["cx"]
    filled = notehead["filled"]

    # Find the closest stem (within one line_spacing horizontally).
    nearby_stem = None
    for stem in stems:
        if abs(stem["cx"] - cx) < line_spacing * 0.9:
            nearby_stem = stem
            break

    if not filled:
        return 4.0 if nearby_stem is None else 2.0

    if nearby_stem is None:
        return 1.0  # stem removed during staff-line erasure — assume quarter

    beam_count = _count_beams_on_stem(nearby_stem, beams)
    if beam_count >= 3:
        return 0.125   # 32nd
    if beam_count == 2:
        return 0.25    # 16th
    if beam_count == 1:
        return 0.5     # 8th
    return 1.0         # quarter


# ─── Chord Grouping ───────────────────────────────────────────────────────────

def _group_into_chords(noteheads: list[dict], line_spacing: float) -> list[list[dict]]:
    """
    Group noteheads that share the same beat position (a chord) into sublists.

    Noteheads are sorted left-to-right. Any notehead whose cx is within
    0.75 line_spacings of the first notehead in the current group is
    considered part of the same chord (stacked vertically, same beat).
    """
    if not noteheads:
        return []
    sorted_nh = sorted(noteheads, key=lambda n: n["cx"])
    chords: list[list[dict]] = [[sorted_nh[0]]]
    for nh in sorted_nh[1:]:
        if abs(nh["cx"] - chords[-1][0]["cx"]) <= line_spacing * 1.5:
            chords[-1].append(nh)
        else:
            chords.append([nh])
    return chords


# ─── Measure Assembly ─────────────────────────────────────────────────────────

def _build_measures(
    noteheads: list[dict],
    stems: list[dict],
    beams: list[dict],
    barline_xs: list[int],
    stave: list[int],
    clef: str,
    time_sig: tuple[int, int],
    n_acc: int,
    acc_type: str,
    measure_start_number: int,
    beat_offset: float,
    clef_changes: dict[int, str] | None = None,
) -> list[Measure]:
    """
    Group noteheads by measure (using barline x-positions) and build Measure objects.

    clef_changes: optional {measure_index: new_clef} dict from _detect_clef_changes.
    The active clef is updated at the start of each measure where a change is recorded.
    """
    if not noteheads:
        return []

    line_spacing = (stave[4] - stave[0]) / 4
    barlines = sorted(barline_xs)
    clef_changes = clef_changes or {}

    def _measure_index(cx: int) -> int:
        for i, bx in enumerate(barlines):
            if cx < bx:
                return i
        return len(barlines)

    # Bucket noteheads into measures.
    buckets: dict[int, list[dict]] = {}
    for nh in sorted(noteheads, key=lambda n: n["cx"]):
        mi = _measure_index(nh["cx"])
        buckets.setdefault(mi, []).append(nh)

    measures: list[Measure] = []
    current_beat = beat_offset
    active_clef = clef

    first_mi = min(buckets)  # could be 1 if an opening barline survived the left-margin filter
    for mi in sorted(buckets):
        # Apply any clef change that takes effect at this measure index.
        if mi in clef_changes:
            active_clef = clef_changes[mi]

        m = Measure(
            number=measure_start_number + (mi - first_mi),
            time_sig=time_sig,
        )
        note_beat = current_beat
        chords = _group_into_chords(buckets[mi], line_spacing)
        for chord in chords:
            # All notes in a chord share the same onset; advance by the chord's duration.
            chord_dur = max(_assign_duration(nh, stems, beams, line_spacing) for nh in chord)
            for nh in chord:
                raw_pitch = _cy_to_pitch(nh["cy"], stave, active_clef)
                pitch = _apply_key_sig(raw_pitch, n_acc, acc_type)
                m.notes.append(
                    Note(
                        pitch=pitch,
                        onset_beat=note_beat,
                        duration_beat=chord_dur,
                    )
                )
            note_beat += chord_dur
        measures.append(m)
        current_beat += time_sig[0]  # advance by beats per measure

    return measures


# ─── OCR / Marking Extraction ─────────────────────────────────────────────────

def _ocr_region(gray: np.ndarray, y0: int, y1: int) -> str:
    """Run Tesseract on a horizontal strip of the grayscale image."""
    try:
        import pytesseract  # type: ignore
    except ImportError:
        return ""

    strip = gray[max(0, y0) : min(gray.shape[0], y1), :]
    if strip.size == 0:
        return ""
    # Upscale for better OCR accuracy on small text.
    strip = cv2.resize(strip, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, strip_bin = cv2.threshold(strip, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return pytesseract.image_to_string(strip_bin, config="--psm 6")


def _parse_text_markings(text: str, beat: float) -> tuple[str, int, list[Marking]]:
    """
    Extract style string, BPM, and expression Markings from OCR text.
    Returns (style, bpm, markings).  bpm=0 means not found.
    """
    style = ""
    bpm = 0
    markings: list[Marking] = []

    m = _RE_STYLE.search(text)
    if m:
        style = m.group(0).capitalize()
        bpm = _STYLE_DEFAULT_BPM.get(style, 120)

    m = _RE_BPM.search(text)
    if m:
        bpm = int(m.group(1))

    for m in _RE_EXPR.finditer(text):
        markings.append(Marking(type=m.group(0).rstrip(".").lower(), onset_beat=beat))

    for m in _RE_DYNAMIC.finditer(text):
        markings.append(Marking(type=m.group(0), onset_beat=beat))

    return style, bpm, markings


# ─── Clef Detection (heuristic) ───────────────────────────────────────────────

def _detect_clef(cleaned: np.ndarray, stave: list[int]) -> str:
    """
    Guess the clef for a stave from the shape of the leftmost large symbol.

    Treble clef: tall blob extending 1.5× staff height above the top line.
    Bass clef:   compact blob near top of staff with a dot pattern.
    Falls back to 'treble' when uncertain.
    """
    line_spacing = (stave[4] - stave[0]) / 4
    staff_height = stave[4] - stave[0]

    # Search within the left 10% of the image.
    x_limit = max(1, int(cleaned.shape[1] * 0.10))
    margin = int(line_spacing * 4)
    y0 = max(0, stave[0] - margin)
    y1 = min(cleaned.shape[0], stave[4] + margin)

    roi = cleaned[y0:y1, :x_limit]
    num, _, stats, _ = cv2.connectedComponentsWithStats(roi, connectivity=8)

    for i in range(1, num):
        _, _, w, h, _ = stats[i]
        # Treble clef: extends well above the staff top line
        if h > staff_height * 1.2:
            return "treble"
        # Bass clef: height close to half the staff, wide
        if staff_height * 0.3 < h < staff_height * 0.7 and w > line_spacing * 0.5:
            return "bass"

    return "treble"  # default


# ─── Mid-Staff Clef Change Detection ─────────────────────────────────────────

def _detect_clef_changes(
    cleaned: np.ndarray,
    stave: list[int],
    barline_xs: list[int],
    initial_clef: str,
) -> dict[int, str]:
    """
    Detect mid-staff clef changes and return {barline_index: new_clef}.

    After each barline, the region just to the right may contain a small clef
    symbol indicating the clef changes from that point.  We look for:
      - Treble G-clef: a tall blob (height > 1.0× staff_height) with narrow aspect
      - Bass F-clef:   a medium blob (0.35–0.85× staff_height) with wide aspect

    Returns a dict keyed by barline index (0 = before first barline, i.e. the first
    measure) where the clef changes, mapping to the new clef name.
    """
    staff_height = stave[4] - stave[0]
    line_spacing = staff_height / 4

    # Search window: one to two line_spacings to the right of each barline,
    # within the vertical bounds of the staff ± a small margin.
    search_w = int(line_spacing * 2.5)
    margin = int(line_spacing * 2.0)
    y0 = max(0, stave[0] - margin)
    y1 = min(cleaned.shape[0], stave[4] + margin)

    clef_changes: dict[int, str] = {}
    current_clef = initial_clef

    # Check the initial position too (measure 0 can have a clef already established).
    # We scan from each barline's x+1 through x+search_w.
    for bi, bx in enumerate(barline_xs):
        x0 = bx + 2
        x1 = min(cleaned.shape[1], bx + search_w)
        if x1 <= x0:
            continue

        roi = cleaned[y0:y1, x0:x1]
        num, _, stats, _ = cv2.connectedComponentsWithStats(roi, connectivity=8)

        for i in range(1, num):
            _, _, w, h, area = stats[i]
            if area < line_spacing ** 2 * 0.3:
                continue
            aspect = w / max(h, 1)
            # Treble G-clef: taller than staff, thin
            if h > staff_height * 0.9 and aspect < 1.0:
                if current_clef != "treble":
                    clef_changes[bi + 1] = "treble"
                    current_clef = "treble"
                break
            # Bass F-clef: medium height, wider
            if staff_height * 0.3 < h < staff_height * 0.85 and aspect > 0.5:
                if current_clef != "bass":
                    clef_changes[bi + 1] = "bass"
                    current_clef = "bass"
                break

    return clef_changes


# ─── Key Signature Detection ──────────────────────────────────────────────────

def _detect_key_signature(binary: np.ndarray, cleaned: np.ndarray, stave: list[int]) -> tuple[int, str]:
    """
    Estimate the key signature by counting accidental symbols (flats/sharps) at the
    left edge of the stave between the clef and the time signature.

    Uses a column-projection approach on the staff-line-removed image: each accidental
    creates a distinct ink cluster in the x-projection.  Clusters are separated by
    empty columns.

    Returns (n_accidentals, accidental_type) where type is "flat" or "sharp".
    Falls back to (0, "none") when nothing is found.
    """
    line_spacing = (stave[4] - stave[0]) / 4
    w = cleaned.shape[1]

    # Key-sig region: after the clef body (~13.5%) and before the time sig (~19%).
    # 13.5% is chosen to clear even the lower curl of the treble G-clef.
    x0 = int(w * 0.135)
    x1 = int(w * 0.19)
    # Restrict y to the staff rows plus a small margin so we don't pick up dynamics.
    margin = int(line_spacing * 1.0)
    y0 = max(0, stave[0] - margin)
    y1 = min(cleaned.shape[0], stave[4] + margin)

    roi = cleaned[y0:y1, x0:x1]
    if roi.size == 0:
        return 0, "none"

    # Column ink count (number of non-zero rows per column).
    col_ink = (roi > 0).sum(axis=0)

    # Find contiguous clusters of ink-bearing columns.
    # A cluster is a run where col_ink >= min_ink, separated by >= min_gap zero columns.
    min_ink = max(2, int((y1 - y0) * 0.05))  # at least 5% of ROI height
    min_gap = 2                               # columns of near-zero to break a cluster

    clusters: list[tuple[int, int]] = []
    in_cluster = False
    cs = 0
    zero_run = 0

    for c, v in enumerate(col_ink):
        if v >= min_ink:
            if not in_cluster:
                cs = c
                in_cluster = True
            zero_run = 0
        else:
            if in_cluster:
                zero_run += 1
                if zero_run >= min_gap:
                    clusters.append((cs, c - zero_run))
                    in_cluster = False
                    zero_run = 0
    if in_cluster:
        clusters.append((cs, len(col_ink) - 1))

    # Filter clusters by minimum y-extent: real accidentals span at least 1× line_spacing.
    # This removes stem fragments, clef residuals, and other noise.
    min_y_extent = int(line_spacing * 1.0)
    validated: list[tuple[int, int]] = []
    for s, e in clusters:
        col_strip = roi[:, s : e + 1]
        rows_with_ink = np.where(col_strip.max(axis=1) > 0)[0]
        if len(rows_with_ink) >= 2:
            y_ext = int(rows_with_ink[-1] - rows_with_ink[0] + 1)
            if y_ext >= min_y_extent:
                validated.append((s, e))
    clusters = validated

    # Drop any cluster whose x-center is past the time-sig boundary.
    time_sig_x = int(w * 0.185) - x0
    clusters = [(s, e) for s, e in clusters if (s + e) // 2 < time_sig_x]

    n_acc = len(clusters)
    if n_acc == 0:
        return 0, "none"

    # Flat vs sharp: for each cluster compute the y-extent of its ink.
    # Flat 'b' symbol: a tall thin body with a stem — y-extent ≈ 1.5–2.5× line_spacing.
    # Sharp '#' symbol: two short vertical lines — y-extent ≈ 0.8–1.4× line_spacing.
    y_extents = []
    for s, e in clusters:
        col_strip = roi[:, s : e + 1]
        rows_with_ink = np.where(col_strip.max(axis=1) > 0)[0]
        if len(rows_with_ink) > 0:
            y_extents.append(int(rows_with_ink[-1] - rows_with_ink[0] + 1))

    if not y_extents:
        return n_acc, "flat"

    median_y_ext = sorted(y_extents)[len(y_extents) // 2]
    acc_type = "flat" if median_y_ext > line_spacing * 1.2 else "sharp"

    return min(n_acc, 7), acc_type


def _apply_key_sig(pitch: str, n_acc: int, acc_type: str) -> str:
    """Add the correct accidental suffix to a diatonic pitch based on key sig."""
    if n_acc == 0 or acc_type == "none":
        return pitch
    note = pitch[:-1]  # e.g. "A" from "A4"
    octave = pitch[-1]  # e.g. "4"
    if acc_type == "flat" and note in _FLAT_NOTES[n_acc]:
        return f"{note}b{octave}"
    if acc_type == "sharp" and note in _SHARP_NOTES[n_acc]:
        return f"{note}#{octave}"
    return pitch


# ─── Time Signature Detection ─────────────────────────────────────────────────

def _detect_time_signature(gray: np.ndarray, stave: list[int]) -> tuple[int, int]:
    """
    OCR the time-signature digits at the left edge of a stave.
    Returns (numerator, denominator), defaulting to (4, 4) on failure.
    """
    line_spacing = (stave[4] - stave[0]) / 4
    w = gray.shape[1]
    # Time sig typically lives between 16% and 23% of page width.
    x0 = int(w * 0.16)
    x1 = int(w * 0.23)
    y0 = max(0, stave[0] - int(line_spacing))
    y1 = min(gray.shape[0], stave[4] + int(line_spacing))

    region = gray[y0:y1, x0:x1]
    if region.size == 0:
        return 4, 4

    # Upscale heavily for digit OCR accuracy.
    region = cv2.resize(region, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    _, region_bin = cv2.threshold(region, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    try:
        import pytesseract  # type: ignore
        raw = pytesseract.image_to_string(
            region_bin, config="--psm 6 -c tessedit_char_whitelist=0123456789"
        ).strip()
        digits = re.findall(r"\d+", raw)
        if len(digits) >= 2:
            n, d = int(digits[0]), int(digits[1])
            if n >= 2 and d >= 2:
                return n, d
        elif len(digits) == 1:
            n = int(digits[0])
            if n >= 2:
                return n, n
    except Exception:
        pass
    return 4, 4


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def parse_music_sheet(pdf_path: str) -> Score:
    """
    Parse a scanned PDF music sheet into a Score.

    Uses purely traditional image processing (OpenCV morphology + geometry).
    Requires: PyMuPDF, opencv-python-headless, numpy, pytesseract + Tesseract binary.

    Limitations:
    - Assumes digitally clean scans (≥150 DPI, low noise).
    - Accidentals on individual notes are not yet resolved.
    - Dotted notes, ties, slurs, ornaments require additional heuristics.
    - Key signature detection uses accidental counts (basic).
    """
    score = Score()
    doc = fitz.open(pdf_path)

    # parts[stave_pos] grows as new stave positions are discovered (e.g. 0=treble, 1=bass).
    parts: list[Part] = []
    beat_cursor = 0.0
    total_measures = 0
    n_acc = 0          # accidental count from key signature
    acc_type = "none"  # "flat" | "sharp" | "none"

    for page_num in range(len(doc)):
        page = doc[page_num]
        gray = _render_page(page)
        binary = _binarize(gray)

        staves = _detect_staff_lines(binary)
        print(f"[p{page_num+1}] {len(staves)} staves detected")
        if not staves:
            continue

        cleaned = _remove_staff_lines(binary)

        # OCR the header region (above first stave) on the first page.
        if page_num == 0:
            header_y1 = staves[0][0]
            header_text = _ocr_region(gray, 0, header_y1)
            style, bpm, header_markings = _parse_text_markings(header_text, beat=0.0)
            if style:
                score.style = style
            if bpm:
                score.tempo = bpm
            score.markings.extend(header_markings)

            # Detect key + time signature from the first stave only once.
            n_acc, acc_type = _detect_key_signature(binary, cleaned, staves[0])
            score.key_sig = f"{n_acc}{acc_type[0].upper() if acc_type != 'none' else ''}"
            detected_time = _detect_time_signature(gray, staves[0])
            score.time_sig = detected_time
        systems = _group_staves_into_systems(staves)

        for system in systems:
            # All staves in a system are simultaneous — save the cursor before the system
            # and restore it for each stave so they all start at the same beat.
            system_start_beat = beat_cursor
            system_measure_count = 0

            for stave_pos, stave in enumerate(system):
                # Grow parts list as needed.
                while len(parts) <= stave_pos:
                    clef_name = "treble" if len(parts) == 0 else "bass"
                    parts.append(Part(name=f"Part {len(parts) + 1}", clef=clef_name))

                # Assign clef: within a system, even index → treble, odd → bass.
                if len(system) > 1:
                    clef = "treble" if stave_pos % 2 == 0 else "bass"
                else:
                    clef = _detect_clef(cleaned, stave)

                # Top stave: reduce below-margin to avoid picking up dynamics in inter-stave gap.
                nh_extra_below = 2 if (stave_pos == 0 and len(system) > 1) else 3
                noteheads = _detect_noteheads(cleaned, stave, clef=clef, extra_below=nh_extra_below)
                stems     = _detect_stems(cleaned, stave)
                beams     = _detect_beams(cleaned, stave)
                barline_xs = _detect_barlines(binary, stave)

                # OCR the strip above this stave for expression markings.
                stave_idx = staves.index(stave)
                y_above = staves[stave_idx - 1][4] if stave_idx > 0 else 0
                expr_text = _ocr_region(gray, y_above, stave[0])
                _, _, expr_markings = _parse_text_markings(expr_text, beat=system_start_beat)
                if parts[stave_pos].measures:
                    parts[stave_pos].measures[-1].markings.extend(expr_markings)

                clef_changes = _detect_clef_changes(cleaned, stave, barline_xs, clef)

                print(
                    f"  [p{page_num+1} sys{systems.index(system)+1} "
                    f"stave{stave_pos+1}/{len(system)} {clef}] "
                    f"noteheads={len(noteheads)} barlines={len(barline_xs)}"
                    + (f" clef_changes={clef_changes}" if clef_changes else "")
                )

                measures = _build_measures(
                    noteheads=noteheads,
                    stems=stems,
                    beams=beams,
                    barline_xs=barline_xs,
                    stave=stave,
                    clef=clef,
                    time_sig=score.time_sig,
                    n_acc=n_acc,
                    acc_type=acc_type,
                    # All staves in the system start at the same measure number.
                    measure_start_number=total_measures + 1,
                    beat_offset=system_start_beat,
                    clef_changes=clef_changes,
                )

                parts[stave_pos].measures.extend(measures)
                system_measure_count = max(system_measure_count, len(measures))

            # After all staves are processed, advance beat_cursor once per system.
            beat_cursor = system_start_beat + system_measure_count * score.time_sig[0]
            total_measures += system_measure_count

    score.parts = parts
    doc.close()
    return score


# ─── CLI smoke-test ───────────────────────────────────────────────────────────

def _save_debug_image(pdf_path: str, out_path: str = "debug_page1.png") -> None:
    """
    Save an annotated image of page 1 showing detected stave lines,
    noteheads (green = filled, blue = hollow), and barlines (red).
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    gray = _render_page(page)
    binary = _binarize(gray)
    cleaned = _remove_staff_lines(binary)

    staves = _detect_staff_lines(binary)
    # Convert to 3-channel for colour annotation.
    vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    systems = _group_staves_into_systems(staves)
    stave_clef: dict[int, str] = {}
    for system in systems:
        for pos, stave in enumerate(system):
            if len(system) > 1:
                stave_clef[id(stave)] = "treble" if pos % 2 == 0 else "bass"
            else:
                stave_clef[id(stave)] = _detect_clef(cleaned, stave)

    for stave in staves:
        clef = stave_clef.get(id(stave), "treble")
        # Draw stave lines in yellow.
        for y in stave:
            cv2.line(vis, (0, y), (vis.shape[1], y), (0, 200, 200), 1)

        noteheads = _detect_noteheads(cleaned, stave, clef=clef)
        barline_xs = _detect_barlines(binary, stave)

        for nh in noteheads:
            colour = (0, 200, 0) if nh["filled"] else (200, 100, 0)
            cx, cy = nh["cx"], nh["cy"]
            cv2.circle(vis, (cx, cy), 4, colour, 2)
            pitch = _cy_to_pitch(cy, stave, clef)
            cv2.putText(vis, pitch, (cx + 5, cy), cv2.FONT_HERSHEY_PLAIN, 0.7,
                        colour, 1, cv2.LINE_AA)

        for bx in barline_xs:
            cv2.line(vis, (bx, stave[0] - 10), (bx, stave[4] + 10), (0, 0, 220), 1)

    cv2.imwrite(out_path, vis)
    doc.close()
    print(f"Debug image saved → {out_path}")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    _default = Path(__file__).parent / "test.pdf"

    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = positional[0] if positional else str(_default)

    if "--debug" in flags:
        _save_debug_image(path)
        sys.exit(0)

    score = parse_music_sheet(path)
    print(f"Style : {score.style or '(not detected)'}")
    print(f"Tempo : {score.tempo} BPM")
    print(f"Key   : {score.key_sig or 'C'}")
    print(f"Time  : {score.time_sig[0]}/{score.time_sig[1]}")
    print(f"Parts : {len(score.parts)}")
    for part in score.parts:
        print(f"\n  {part.name} ({part.clef}) — {len(part.measures)} measures")
        for m in part.measures[:2]:
            print(f"    Measure {m.number}: {len(m.notes)} notes")
            for n in m.notes[:8]:
                print(f"      {str(n.pitch):4s}  beat={n.onset_beat:.2f}  dur={n.duration_beat:.3f}  x≈{int(getattr(n,'_cx',0))}")
