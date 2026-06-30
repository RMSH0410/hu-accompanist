"""Music Score PDF -> Note Data Converter

Phase 1: PDF Processing
  - Load PDF pages
  - Render each page as a high-resolution RGB image (300-600 DPI)
  - Preprocess: grayscale, binarization (Otsu), deskew
  Outputs saved to: debug_output/step1/

Phase 2: Music Symbol Recognition
  A:
  - Detect staff lines via horizontal projection profile peaks
  - Remove staff lines; isolate symbols via connected components

Phase 3: Music Parsing
  - Determine pitch from notehead y-position relative to staff and clef
  - Estimate duration from fill ratio, stem presence, and beam count
  - Associate accidentals with noteheads
  - Organize notes into measures using barline x-positions
"""

import os
import numpy as np
import cv2
import fitz            # PyMuPDF
import pytesseract     # Tesseract OCR (local, no API key)


# ---------------------------------------------------------------------------
# Phase 1: PDF Processing
# ---------------------------------------------------------------------------

def load_pdf(pdf_path: str) -> fitz.Document:
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    return fitz.open(pdf_path)


def render_page_rgb(page: fitz.Page, dpi: int = 300) -> np.ndarray:
    """Render a PDF page to an RGB numpy array at the given DPI."""
    zoom = dpi / 72.0  # PyMuPDF default is 72 DPI
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    return img.copy()


def to_grayscale(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def binarize(gray: np.ndarray) -> np.ndarray:
    """Otsu's global thresholding - works well for black ink on white paper."""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def deskew(binary: np.ndarray) -> np.ndarray:
    """
    Find skew angle by maximizing the variance of the horizontal projection profile.
    Staff lines are the dominant horizontal feature; they create sharp row-sum peaks
    at exactly the correct angle. Hough lines are unreliable because beams, barlines,
    and stems introduce competing angles.
    """
    h, w = binary.shape

    # Downsample to ~1000 px wide for speed
    scale = min(1.0, 1000.0 / w)
    sw, sh = int(w * scale), int(h * scale)
    small = cv2.resize(binary, (sw, sh), interpolation=cv2.INTER_AREA)

    best_angle = 0.0
    best_var = -1.0

    for angle in np.arange(-5.0, 5.1, 0.2):
        M = cv2.getRotationMatrix2D((sw // 2, sh // 2), angle, 1.0)
        rotated = cv2.warpAffine(small, M, (sw, sh),
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=255)
        profile = np.sum(rotated < 128, axis=1).astype(np.float64)
        var = float(np.var(profile))
        if var > best_var:
            best_var = var
            best_angle = angle

    # Skip correction if the image is already well-aligned
    if abs(best_angle) < 0.3:
        return binary

    M = cv2.getRotationMatrix2D((w // 2, h // 2), best_angle, 1.0)
    return cv2.warpAffine(binary, M, (w, h),
                           flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT,
                           borderValue=255)


def preprocess_page(rgb: np.ndarray) -> dict:
    """Full Phase 1 pipeline for a single page. Returns all intermediate images."""
    gray = to_grayscale(rgb)
    binary = binarize(gray)
    deskewed = deskew(binary)
    return {
        "rgb": rgb,
        "gray": gray,
        "binary": binary,
        "deskewed": deskewed,
    }


def process_pdf(pdf_path: str, dpi: int = 300, output_dir: str = None) -> list:
    """
    Phase 1 entry point. Processes every page of a PDF.
    If output_dir is given, saves debug images to output_dir/step1/.
    Returns a list of preprocessed page dicts.
    """
    doc = load_pdf(pdf_path)
    results = []

    save_dir = os.path.join(output_dir, "step1") if output_dir else None
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    for page_num in range(len(doc)):
        page = doc[page_num]
        rgb = render_page_rgb(page, dpi=dpi)
        processed = preprocess_page(rgb)
        processed["page_num"] = page_num
        results.append(processed)

        if save_dir:
            base = os.path.join(save_dir, f"page_{page_num + 1:03d}")
            cv2.imwrite(f"{base}_rgb.png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            cv2.imwrite(f"{base}_gray.png", processed["gray"])
            cv2.imwrite(f"{base}_binary.png", processed["binary"])
            cv2.imwrite(f"{base}_deskewed.png", processed["deskewed"])
            print(f"  [Step1] Page {page_num + 1} -> {base}_*.png")

    doc.close()
    return results


# ---------------------------------------------------------------------------
# Phase 2: Music Symbol Recognition
# ---------------------------------------------------------------------------

def detect_staff_lines(binary: np.ndarray) -> dict:
    """
    Detect only staff lines that belong to complete 5-line systems.

    Key insight: staff lines span most of the page width (~2000+ px), while
    ledger lines are short (~20-80 px). A horizontal morphological open with a
    long kernel keeps only pixels that are part of a long continuous run, so
    ledger lines, stems, and beams are eliminated before any row counting.

    Algorithm:
      1. Horizontal MORPH_OPEN with kernel = w//4 to isolate long runs.
      2. Row projection of the filtered image → candidate rows.
      3. Cluster consecutive rows into single line centres.
      4. Estimate staff_space from gaps in a sane range (8–150 px) to avoid
         tiny noise gaps corrupting the estimate.
      5. Sliding window of 5: accept groups where all 4 gaps are within 35%
         of staff_space and don't overlap a previously accepted system.
      6. Return only lines that belong to validated systems.
    """
    h, w = binary.shape
    inverted = cv2.bitwise_not(binary)

    # Step 1 – close micro-gaps (≤8px) left by barline intersections so staff
    # lines appear as one continuous run even across bar lines.
    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (8, 1))
    closed = cv2.morphologyEx(inverted, cv2.MORPH_CLOSE, close_kernel)

    # Step 2 – open with a long kernel (w//12 ≈ 225 px at 300 DPI) to keep
    # only runs spanning at least one full measure width. Ledger lines (~20-80 px)
    # and short dynamics markings are eliminated; staff lines survive.
    kernel_len = max(w // 12, 50)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_len, 1))
    long_horiz = cv2.morphologyEx(closed, cv2.MORPH_OPEN, h_kernel)

    row_profile = np.sum(long_horiz > 0, axis=1)
    candidate_rows = np.where(row_profile > 0)[0]

    if len(candidate_rows) == 0:
        return {"line_ys": [], "systems": [], "staff_space": 20}

    # Cluster consecutive candidate rows → one centre per staff line
    groups, group = [], [int(candidate_rows[0])]
    for r in candidate_rows[1:]:
        if r - group[-1] <= 4:
            group.append(int(r))
        else:
            groups.append(group)
            group = [int(r)]
    groups.append(group)
    all_line_ys = [int(np.median(g)) for g in groups]

    if len(all_line_ys) < 5:
        return {"line_ys": [], "systems": [], "staff_space": 20}

    # Estimate staff_space: median of gaps in a sane range.
    # The 8–150 px window skips noise gaps (thick barlines, doubled rows) and
    # large inter-system gaps, leaving only intra-staff spacing.
    all_gaps = np.diff(all_line_ys)
    reasonable = all_gaps[(all_gaps >= 8) & (all_gaps <= 150)]
    staff_space = int(np.median(reasonable)) if len(reasonable) > 0 else 20

    def _line_fill(y: int) -> float:
        """Max fill of long_horiz in a ±2-row window around y (fraction of width)."""
        y0, y1 = max(0, y - 2), min(h, y + 3)
        return float(np.max(np.sum(long_horiz[y0:y1, :] > 0, axis=1))) / w

    # Sliding window of 5: keep groups where all 4 inner gaps match staff_space
    # AND the system's average fill is above 20%.
    # Checking fill per system (not per line) correctly rejects ledger-line clusters
    # (all 5 lines have ~10% fill) while keeping real systems where one line
    # might have lower fill due to a dense passage or barline break.
    systems = []
    for i in range(len(all_line_ys) - 4):
        window = all_line_ys[i : i + 5]
        gaps = np.diff(window)
        if not all(abs(g - staff_space) / staff_space < 0.35 for g in gaps):
            continue
        if systems and window[0] <= systems[-1][-1]:
            continue
        avg_fill = sum(_line_fill(y) for y in window) / 5
        if avg_fill >= 0.20:
            systems.append(list(window))

    valid_line_ys = sorted(y for sys_ in systems for y in sys_)

    # Keep the pixel-accurate staff mask so remove_staff_lines can erase sloped
    # lines precisely instead of relying on flat horizontal row ranges.
    # Restrict to validated lines only — rows belonging to confirmed systems.
    valid_set = set(valid_line_ys)
    staff_mask = np.zeros_like(long_horiz)
    for r in candidate_rows:
        if any(abs(int(r) - y) <= 4 for y in valid_set):
            staff_mask[r, :] = long_horiz[r, :]

    return {"line_ys": valid_line_ys, "systems": systems,
            "staff_space": staff_space, "staff_mask": staff_mask}


def remove_staff_lines(binary: np.ndarray, line_ys: list,
                        thickness: int = 2,
                        staff_mask: np.ndarray = None) -> np.ndarray:
    """
    White out detected staff line pixels from binary.
    When staff_mask is provided (pixel-accurate mask from detect_staff_lines),
    it is dilated vertically by `thickness` rows to cover the full line
    thickness — this handles slightly sloped lines that flat row erasure misses.
    Falls back to erasing flat horizontal row ranges when no mask is given.
    """
    result = binary.copy()
    if staff_mask is not None:
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, thickness * 2 + 1))
        dilated = cv2.dilate(staff_mask, v_kernel)
        result[dilated > 0] = 255
    else:
        for y in line_ys:
            y0 = max(0, y - thickness)
            y1 = min(binary.shape[0], y + thickness + 1)
            result[y0:y1, :] = 255
    return result


def reconstruct_staff_cuts(no_staff: np.ndarray, line_ys: list,
                            thickness: int = 2,
                            staff_mask: np.ndarray = None) -> np.ndarray:
    """
    Inpaint the strips erased by remove_staff_lines so that fragmented symbols
    are restored before downstream processing.

    When staff_mask is given, the inpaint region matches the exact pixels that
    were erased (same dilated mask as remove_staff_lines used), so sloped staff
    lines are handled correctly.  Falls back to flat row ranges without a mask.
    """
    H, W = no_staff.shape
    if staff_mask is not None:
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, thickness * 2 + 1))
        mask = cv2.dilate(staff_mask, v_kernel)
    else:
        if not line_ys:
            return no_staff
        mask = np.zeros((H, W), dtype=np.uint8)
        for y in line_ys:
            y0 = max(0, y - thickness)
            y1 = min(H, y + thickness + 1)
            mask[y0:y1, :] = 255
    restored = cv2.inpaint(no_staff, mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
    _, binary = cv2.threshold(restored, 127, 255, cv2.THRESH_BINARY)
    return binary


def detect_symbols(no_staff: np.ndarray, staff_space: int) -> list:
    """
    Find musical symbol bounding boxes via connected components on the
    staff-line-removed binary image. Filters out noise and page-border blobs.
    """
    inverted = cv2.bitwise_not(no_staff)
    num_labels, _, stats, centroids = cv2.connectedComponentsWithStats(
        inverted, connectivity=8
    )

    min_area = max(4, int((staff_space * 0.25) ** 2))
    max_area = int((staff_space * 25) ** 2)

    symbols = []
    for i in range(1, num_labels):  # label 0 = background
        x, y, w, h, area = stats[i]
        if min_area <= area <= max_area:
            symbols.append({
                "bbox": (int(x), int(y), int(w), int(h)),
                "area": int(area),
                "centroid": (float(centroids[i][0]), float(centroids[i][1])),
            })

    return symbols


def classify_symbol(bbox: tuple, staff_space: int) -> str:
    """
    Rule-based classification placeholder — replace inner logic with YOLO inference
    once model weights are available. The function signature stays the same.
    """
    _, _, w, h = bbox
    aspect = w / h if h > 0 else 0

    # Barline: tall thin vertical stroke spanning the staff
    if h > staff_space * 3 and aspect < 0.25:
        return "barline"

    # Clef: large complex region taller than 3 staff spaces
    if h > staff_space * 3.5 and w > staff_space * 0.8:
        return "clef"

    # Beam: wide and very flat
    if aspect > 4.0 and h < staff_space * 0.6:
        return "beam"

    # Accidental (sharp/flat/natural): taller than wide, 1.5-3 staff spaces tall
    if staff_space * 1.5 < h < staff_space * 3.5 and aspect < 0.7:
        return "accidental"

    # Notehead / note cluster: roughly square, height <= 1.5 staff spaces
    if 0.4 < aspect < 2.5 and h <= staff_space * 1.5:
        return "notehead"

    return "unknown"


def visualize_staff_lines(rgb: np.ndarray, line_ys: list, systems: list) -> np.ndarray:
    """Draw detected staff lines (blue) and system brackets (green) on RGB image."""
    viz = rgb.copy()
    w = viz.shape[1]
    for y in line_ys:
        cv2.line(viz, (0, y), (w, y), (0, 100, 255), 2)
    for system in systems:
        if len(system) == 5:
            y_top, y_bot = system[0] - 4, system[-1] + 4
            cv2.rectangle(viz, (0, y_top), (w - 1, y_bot), (0, 220, 0), 2)
    return viz


_CLASS_COLORS = {
    "notehead":   (0, 220, 0),
    "barline":    (220, 0, 0),
    "clef":       (0, 0, 220),
    "beam":       (220, 140, 0),
    "accidental": (160, 0, 200),
    "unknown":    (130, 130, 130),
}


def visualize_symbols(rgb: np.ndarray, symbols: list) -> np.ndarray:
    """Draw bounding boxes and class labels on RGB image."""
    viz = rgb.copy()
    for sym in symbols:
        x, y, w, h = sym["bbox"]
        label = sym.get("class", "unknown")
        color = _CLASS_COLORS.get(label, (130, 130, 130))
        cv2.rectangle(viz, (x, y), (x + w, y + h), color, 1)
        cv2.putText(viz, label, (x, max(y - 3, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1, cv2.LINE_AA)
    return viz


def remove_staff(phase1_results: list, output_dir: str = None) -> list:
    """
    Step 2: Detect valid staff systems, remove staff lines, then inpaint the
    erased rows so downstream symbols are not fragmented.
    Saves to output_dir/step2/:
      _staff_detected.png  - blue lines + green system brackets on RGB
      _no_staff_raw.png    - binary with staff lines erased (pre-inpaint)
      _no_staff.png        - inpainted binary (used by all later phases)
    Returns list of dicts: page_num, rgb, staff_info, no_staff.
    """
    save_dir = os.path.join(output_dir, "step2") if output_dir else None
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    results = []
    for page in phase1_results:
        page_num   = page["page_num"]
        binary     = page["deskewed"]
        rgb        = page["rgb"]

        staff_info    = detect_staff_lines(binary)
        smask         = staff_info.get("staff_mask")
        no_staff_raw  = remove_staff_lines(binary, staff_info["line_ys"], staff_mask=smask)
        no_staff      = reconstruct_staff_cuts(no_staff_raw, staff_info["line_ys"], staff_mask=smask)

        results.append({
            "page_num":   page_num,
            "rgb":        rgb,
            "staff_info": staff_info,
            "no_staff":   no_staff,
        })

        if save_dir:
            base = os.path.join(save_dir, f"page_{page_num + 1:03d}")
            staff_viz = visualize_staff_lines(rgb, staff_info["line_ys"], staff_info["systems"])
            cv2.imwrite(f"{base}_staff_detected.png", cv2.cvtColor(staff_viz, cv2.COLOR_RGB2BGR))
            cv2.imwrite(f"{base}_no_staff_raw.png", no_staff_raw)
            cv2.imwrite(f"{base}_no_staff.png", no_staff)
            print(f"  [Step2] Page {page_num + 1}: "
                  f"{len(staff_info['systems'])} systems, "
                  f"{len(staff_info['line_ys'])} lines  "
                  f"(staff_space={staff_info['staff_space']}px)")

    return results


# ---------------------------------------------------------------------------
# Step 3 – Header / Footer Extraction & Removal
# ---------------------------------------------------------------------------

def _ocr_region(gray_crop: np.ndarray,
                x_offset: int = 0, y_offset: int = 0) -> tuple:
    """
    OCR a grayscale crop.
    Returns (text, word_bboxes):
      text       — newline-preserved string (grouped by Tesseract block/line IDs)
      word_bboxes — list of (x, y, w, h) in page coordinates
    PSM 6 = uniform block of text; good for multi-line title/composer/copyright.
    """
    from pytesseract import Output
    if gray_crop.size == 0:
        return "", []
    data = pytesseract.image_to_data(
        gray_crop, config="--psm 6 --oem 3", output_type=Output.DICT
    )

    # Group words by (block_num, line_num) to reconstruct line breaks
    lines: dict = {}
    bboxes = []
    for i in range(len(data["text"])):
        t    = data["text"][i].strip()
        conf = int(data["conf"][i])
        if not t or conf < 20:
            continue
        key = (int(data["block_num"][i]), int(data["line_num"][i]))
        lines.setdefault(key, []).append(t)
        bboxes.append((
            int(data["left"][i])  + x_offset,
            int(data["top"][i])   + y_offset,
            int(data["width"][i]),
            int(data["height"][i]),
        ))

    text = "\n".join(" ".join(words) for words in lines.values())
    return text.strip(), bboxes


def extract_header_footer(binary: np.ndarray,
                           staff_info: dict, page_num: int) -> dict:
    """
    Locate and OCR the two regions that sit outside the music staves:
      - Header: everything above the first staff system (title, composer)
      - Footer: everything below the last staff system (copyright, comments)
    OCR runs on the no_staff binary so staff lines don't interfere with text recognition.
    Returns a dict with the raw OCR text and the pixel bounding boxes.
    """
    h, w = binary.shape
    systems = staff_info["systems"]
    ss = staff_info["staff_space"]

    # Default: no staff → treat whole page as header, empty footer
    first_y = systems[0][0]  if systems else h
    last_y  = systems[-1][-1] if systems else 0

    # Buffer: preserve the region above/below the staves where tempo markings,
    # fingering, bowing, and position annotations live.
    # ss * 8 ≈ 192 px at 300 DPI — enough for 4th-finger / position marks
    # that can sit 4–6 staff spaces away from the outermost staff line.
    buf = max(ss * 8, 80)

    # On pages where the first staff starts close to the top (e.g. page 2+
    # with only an instrument label above it), the full buffer would collapse
    # the header zone to nearly zero. Cap buf so the zone is always at least
    # 2 staff spaces tall — enough to hold one line of text like "Violon".
    MIN_HEADER_ZONE = ss * 2
    buf = min(buf, max(0, first_y - MIN_HEADER_ZONE))

    # Expand zone inward by a small fraction of a staff space so text that
    # sits just inside the buffer edge is still captured by OCR.
    ZONE_EXPAND = 0.30   # fraction of one staff space
    zone_offset = int(ss * ZONE_EXPAND)
    header_y_end   = max(0, first_y - buf + zone_offset)
    footer_y_start = min(h, last_y  + buf - zone_offset)

    # OCR the header on every page — instrument labels (e.g. "Violon"), page
    # numbers, and repeated titles appear above the first system on all pages,
    # not just page 1.
    if header_y_end > 0:
        header_text, header_word_bboxes = _ocr_region(
            binary[0:header_y_end, :], x_offset=0, y_offset=0
        )
    else:
        header_text, header_word_bboxes = "", []

    if footer_y_start < h:
        footer_text, footer_word_bboxes = _ocr_region(
            binary[footer_y_start:h, :], x_offset=0, y_offset=footer_y_start
        )
    else:
        footer_text, footer_word_bboxes = "", []

    return {
        "page":               page_num + 1,
        "header_text":        header_text,
        "footer_text":        footer_text,
        "header_word_bboxes": header_word_bboxes,
        "footer_word_bboxes": footer_word_bboxes,
        # strip extents kept for visualisation reference
        "header_strip":       (0, 0,            w, header_y_end),
        "footer_strip":       (0, footer_y_start, w, h - footer_y_start),
    }


def remove_header_footer(binary: np.ndarray, info: dict, padding: int = 15) -> np.ndarray:
    """
    White out only the areas where text was actually detected, expanded by `padding`
    pixels on every side. Much more precise than erasing the full header/footer strip.
    """
    H, W = binary.shape
    result = binary.copy()
    all_bboxes = info["header_word_bboxes"] + info["footer_word_bboxes"]
    for x, y, w, h in all_bboxes:
        x0 = max(0, x - padding)
        y0 = max(0, y - padding)
        x1 = min(W, x + w + padding)
        y1 = min(H, y + h + padding)
        result[y0:y1, x0:x1] = 255
    return result


def visualize_header_footer(rgb: np.ndarray, info: dict, padding: int = 15) -> np.ndarray:
    """
    Draw the detected text regions on RGB.
      Blue  — header word boxes (with padding shown as dashed outline via alpha overlay)
      Red   — footer word boxes
      Faint strip outlines show the search zone for each region.
    """
    viz  = rgb.copy()
    font = cv2.FONT_HERSHEY_SIMPLEX

    # Light strip outlines so the search zone is still visible
    hs = info["header_strip"]
    fs = info["footer_strip"]
    if hs[3] > 0:
        cv2.rectangle(viz, (hs[0], hs[1]), (hs[0]+hs[2], hs[1]+hs[3]), (160, 200, 255), 1)
        cv2.putText(viz, "HEADER zone", (hs[0]+5, max(hs[1]+20, 20)),
                    font, 0.6, (160, 200, 255), 1, cv2.LINE_AA)
    if fs[3] > 0:
        cv2.rectangle(viz, (fs[0], fs[1]), (fs[0]+fs[2], fs[1]+fs[3]), (255, 160, 160), 1)
        cv2.putText(viz, "FOOTER zone", (fs[0]+5, fs[1]+20),
                    font, 0.6, (255, 160, 160), 1, cv2.LINE_AA)

    # Per-word boxes with padding
    for x, y, w, h in info["header_word_bboxes"]:
        x0, y0 = max(0, x-padding), max(0, y-padding)
        x1, y1 = x+w+padding, y+h+padding
        cv2.rectangle(viz, (x0, y0), (x1, y1), (0, 80, 255), 2)

    for x, y, w, h in info["footer_word_bboxes"]:
        x0, y0 = max(0, x-padding), max(0, y-padding)
        x1, y1 = x+w+padding, y+h+padding
        cv2.rectangle(viz, (x0, y0), (x1, y1), (220, 0, 0), 2)

    return viz


def gather_infos(step2_results: list,
                  output_dir: str = None, info_txt: str = None) -> list:
    """
    Step 3: Detect and OCR header (title/composer) and footer (copyright/comments)
    on each page, remove those regions from the binary image, and write info.txt.
    OCR runs on no_staff so staff lines don't interfere with text recognition.
    Saves to output_dir/step3/:
      _regions.png   - header (blue) and footer (red) boxes overlaid on RGB
      _no_hf.png     - no_staff binary with header+footer erased
    Writes a human-readable info.txt with piece name, composer, and copyright.
    Returns list of dicts: page_num, rgb, staff_info, no_text.
    """
    save_dir = os.path.join(output_dir, "step3") if output_dir else None
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    all_info = []
    results  = []

    for page in step2_results:
        page_num   = page["page_num"]
        rgb        = page["rgb"]
        no_staff   = page["no_staff"]
        staff_info = page["staff_info"]

        # Padding = half a staff space so merged words don't leave ink slivers
        padding = max(10, staff_info["staff_space"] // 2)

        info    = extract_header_footer(no_staff, staff_info, page_num)
        no_text = remove_header_footer(no_staff, info, padding=padding)
        all_info.append(info)

        results.append({
            "page_num":   page_num,
            "rgb":        rgb,
            "staff_info": staff_info,
            "no_text":    no_text,
        })

        n_hw = len(info["header_word_bboxes"])
        n_fw = len(info["footer_word_bboxes"])
        header_preview = info["header_text"].replace("\n", " | ")[:60]
        footer_preview = info["footer_text"].replace("\n", " | ")[:60]
        print(f"  [Step3] Page {page_num + 1}  "
              f"header ({n_hw} words): '{header_preview}'  "
              f"footer ({n_fw} words): '{footer_preview}'")

        if save_dir:
            base = os.path.join(save_dir, f"page_{page_num + 1:03d}")
            viz = visualize_header_footer(rgb, info, padding=padding)
            cv2.imwrite(f"{base}_regions.png", cv2.cvtColor(viz, cv2.COLOR_RGB2BGR))
            cv2.imwrite(f"{base}_no_hf.png", no_text)

    # Write info.txt
    if info_txt:
        def _is_clean(line: str) -> bool:
            alpha = sum(c.isalpha() for c in line)
            return len(line) >= 4 and alpha >= 3

        # Header: page 1 only, first 3 clean lines → title / composer / subtitle
        page1 = next((i for i in all_info if i["page"] == 1), None)
        header_lines = []
        if page1:
            for ln in page1["header_text"].splitlines():
                ln = ln.strip()
                if _is_clean(ln) and ln not in header_lines:
                    header_lines.append(ln)
                    if len(header_lines) == 3:
                        break

        # Footer: any page, lines containing "copyright" or "©"
        copyright_lines = []
        for info in all_info:
            for ln in info["footer_text"].splitlines():
                ln = ln.strip()
                if ("copyright" in ln.lower() or "©" in ln) and ln not in copyright_lines:
                    copyright_lines.append(ln)

        with open(info_txt, "w") as f:
            labels = ["Title", "Composer", "Subtitle"]
            for i, ln in enumerate(header_lines):
                tag = labels[i] if i < len(labels) else "Subtitle"
                f.write(f"{tag+':':12}{ln}\n")
            if copyright_lines:
                f.write("\n")
                for ln in copyright_lines:
                    f.write(f"{'Copyright:':12}{ln}\n")

        print(f"  Saved -> {info_txt}")

    return results


# ---------------------------------------------------------------------------
# Phase 3: Music Parsing
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pdf_path = "test.pdf"
    out_dir  = "debug_output"
    info_txt = "info.txt"

    print("\n=== 10% Processing pdf... ===")
    phase1 = process_pdf(pdf_path, dpi=300, output_dir=out_dir)

    print("\n=== 20% Detecting & Removing Staff Lines... ===")
    phase2 = remove_staff(phase1, output_dir=out_dir)

    print("\n=== 30% Detecting & Removing header... ===")
    phase3 = gather_infos(phase2, output_dir=out_dir, info_txt=info_txt)

    # TODO: Phase 3 music parsing goes here

    print(f"\nDone. Output in {out_dir}/step1|step2|step3/  info -> {info_txt}")
