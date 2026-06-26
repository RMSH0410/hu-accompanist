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
  B:
  - Classify each symbol with rule-based heuristics (YOLO placeholder)
  Outputs saved to: debug_output/step2/

Phase 3-5: TBD
"""

import os
import json
import numpy as np
import cv2
import fitz  # PyMuPDF


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

    # Sliding window of 5: keep groups where all 4 inner gaps match staff_space
    systems = []
    for i in range(len(all_line_ys) - 4):
        window = all_line_ys[i : i + 5]
        gaps = np.diff(window)
        if all(abs(g - staff_space) / staff_space < 0.35 for g in gaps):
            if not systems or window[0] > systems[-1][-1]:
                systems.append(list(window))

    valid_line_ys = sorted(y for sys_ in systems for y in sys_)
    return {"line_ys": valid_line_ys, "systems": systems, "staff_space": staff_space}


def remove_staff_lines(binary: np.ndarray, line_ys: list, thickness: int = 2) -> np.ndarray:
    """
    White out rows around each detected staff line center.
    thickness=2 means rows [y-2 .. y+2] are cleared.
    """
    result = binary.copy()
    for y in line_ys:
        y0 = max(0, y - thickness)
        y1 = min(binary.shape[0], y + thickness + 1)
        result[y0:y1, :] = 255
    return result


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


def process_step2(phase1_results: list, output_dir: str = None) -> list:
    """
    Step 2: Detect valid staff systems and remove staff lines.
    Saves to output_dir/step2/:
      _staff_detected.png  - blue lines + green system brackets on RGB
      _no_staff.png        - binary image with staff lines erased
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

        staff_info = detect_staff_lines(binary)
        no_staff   = remove_staff_lines(binary, staff_info["line_ys"])

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
            cv2.imwrite(f"{base}_no_staff.png", no_staff)
            print(f"  [Step2] Page {page_num + 1}: "
                  f"{len(staff_info['systems'])} systems, "
                  f"{len(staff_info['line_ys'])} lines  "
                  f"(staff_space={staff_info['staff_space']}px)")

    return results


# ---------------------------------------------------------------------------
# Step 3 – Text Label Extraction & Removal
# ---------------------------------------------------------------------------

def extract_text_labels(doc: fitz.Document, page_num: int, dpi: int = 300) -> list:
    """
    Extract all text spans from a PDF page using PyMuPDF's native text engine
    (no OCR needed — the PDF contains embedded text vectors).
    Returns a list of dicts with the text content and bounding box in image pixels.
    'measure' is left null here; barline detection (a later step) will fill it in.
    """
    zoom = dpi / 72.0
    page = doc[page_num]
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

    labels = []
    for block in blocks:
        if block.get("type") != 0:   # skip embedded-image blocks
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                text = span["text"].strip()
                if not text:
                    continue
                x0, y0, x1, y1 = span["bbox"]   # PDF points, (0,0) = top-left
                labels.append({
                    "name":      text,
                    "page":      page_num + 1,
                    "measure":   None,            # filled in during barline detection
                    "font_size": round(span["size"], 1),
                    "bbox":      (
                        int(x0 * zoom),
                        int(y0 * zoom),
                        int((x1 - x0) * zoom),
                        int((y1 - y0) * zoom),
                    ),
                })
    return labels


def remove_text_from_binary(binary: np.ndarray, text_labels: list) -> np.ndarray:
    """White out every text bounding box in the binary image (3 px padding)."""
    result = binary.copy()
    pad = 3
    for lbl in text_labels:
        x, y, w, h = lbl["bbox"]
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(binary.shape[1], x + w + pad)
        y1 = min(binary.shape[0], y + h + pad)
        result[y0:y1, x0:x1] = 255
    return result


def visualize_text_labels(rgb: np.ndarray, text_labels: list) -> np.ndarray:
    """Draw text bounding boxes (orange) and names on the RGB image."""
    viz = rgb.copy()
    for lbl in text_labels:
        x, y, w, h = lbl["bbox"]
        cv2.rectangle(viz, (x, y), (x + w, y + h), (255, 140, 0), 1)
        cv2.putText(viz, lbl["name"], (x, max(y - 3, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, (255, 100, 0), 1, cv2.LINE_AA)
    return viz


def process_step3(step2_results: list, pdf_path: str,
                  output_dir: str = None, results_txt: str = None) -> list:
    """
    Step 3: Extract text labels from the PDF, remove them from the binary image.
    Saves to output_dir/step3/:
      _text_detected.png  - orange boxes over every text span on RGB
      _no_text.png        - no_staff binary with text regions also erased
      _text_labels.json   - [{name, page, measure, font_size, bbox}, ...]
    Also appends every label (one JSON object per line) to results_txt.
    Returns list of dicts: page_num, rgb, staff_info, no_text, text_labels.
    """
    save_dir = os.path.join(output_dir, "step3") if output_dir else None
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    doc = fitz.open(pdf_path)
    all_labels = []
    results = []

    for page in step2_results:
        page_num   = page["page_num"]
        rgb        = page["rgb"]
        no_staff   = page["no_staff"]
        staff_info = page["staff_info"]

        text_labels = extract_text_labels(doc, page_num)
        no_text     = remove_text_from_binary(no_staff, text_labels)
        all_labels.extend(text_labels)

        results.append({
            "page_num":    page_num,
            "rgb":         rgb,
            "staff_info":  staff_info,
            "no_text":     no_text,
            "text_labels": text_labels,
        })

        if save_dir:
            base = os.path.join(save_dir, f"page_{page_num + 1:03d}")
            txt_viz = visualize_text_labels(rgb, text_labels)
            cv2.imwrite(f"{base}_text_detected.png", cv2.cvtColor(txt_viz, cv2.COLOR_RGB2BGR))
            cv2.imwrite(f"{base}_no_text.png", no_text)
            with open(f"{base}_text_labels.json", "w") as f:
                json.dump(text_labels, f, indent=2)
            print(f"  [Step3] Page {page_num + 1}: {len(text_labels)} text labels extracted")

    doc.close()

    # Write all labels to results.txt (one JSON object per line)
    if results_txt:
        with open(results_txt, "w") as f:
            for lbl in all_labels:
                # Write only the fields the user cares about
                f.write(json.dumps({
                    "name":    lbl["name"],
                    "page":    lbl["page"],
                    "measure": lbl["measure"],
                }) + "\n")
        print(f"  Saved {len(all_labels)} labels -> {results_txt}")

    return results


# ---------------------------------------------------------------------------
# Step 4 – Symbol Detection & Classification
# ---------------------------------------------------------------------------

def process_step4(step3_results: list, output_dir: str = None) -> list:
    """
    Step 4: Detect and classify musical symbols from the text-and-staff-free image.
    Input image is no_text (staff lines + text both removed).
    Saves to output_dir/step4/:
      _symbols.png   - bounding boxes with class labels overlaid on RGB
      _symbols.json  - list of {bbox, class, area, centroid} per symbol
    Returns list of dicts: page_num, symbols.
    """
    save_dir = os.path.join(output_dir, "step4") if output_dir else None
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    results = []
    for page in step3_results:
        page_num    = page["page_num"]
        rgb         = page["rgb"]
        no_text     = page["no_text"]
        staff_space = page["staff_info"]["staff_space"]

        symbols = detect_symbols(no_text, staff_space)
        for sym in symbols:
            sym["class"] = classify_symbol(sym["bbox"], staff_space)

        results.append({"page_num": page_num, "symbols": symbols})

        if save_dir:
            base = os.path.join(save_dir, f"page_{page_num + 1:03d}")
            sym_viz = visualize_symbols(rgb, symbols)
            cv2.imwrite(f"{base}_symbols.png", cv2.cvtColor(sym_viz, cv2.COLOR_RGB2BGR))
            json_data = [{"bbox": s["bbox"], "class": s["class"],
                          "area": s["area"], "centroid": s["centroid"]}
                         for s in symbols]
            with open(f"{base}_symbols.json", "w") as f:
                json.dump(json_data, f, indent=2)

            counts = {}
            for s in symbols:
                counts[s["class"]] = counts.get(s["class"], 0) + 1
            print(f"  [Step4] Page {page_num + 1}: {len(symbols)} symbols {counts}")

    return results


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    pdf_path    = sys.argv[1] if len(sys.argv) > 1 else "test.pdf"
    out_dir     = sys.argv[2] if len(sys.argv) > 2 else "debug_output"
    results_txt = sys.argv[3] if len(sys.argv) > 3 else "results.txt"

    print("\n=== Phase 1 ===")
    phase1 = process_pdf(pdf_path, dpi=300, output_dir=out_dir)

    print("\n=== Step 2: Staff Line Detection & Removal ===")
    phase2 = process_step2(phase1, output_dir=out_dir)

    print("\n=== Step 3: Text Label Extraction & Removal ===")
    phase3 = process_step3(phase2, pdf_path, output_dir=out_dir, results_txt=results_txt)

    print("\n=== Step 4: Symbol Detection & Classification ===")
    phase4 = process_step4(phase3, output_dir=out_dir)

    print(f"\nDone. Output in {out_dir}/step1|step2|step3|step4/  labels -> {results_txt}")
