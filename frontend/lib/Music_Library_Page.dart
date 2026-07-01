import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:liquid_glass_easy/liquid_glass_easy.dart';
import 'LiquidGlass.dart';

// ─── Plug your real data in here later ────────────────────────────────────────
class MusicSheet {
  final String id;
  final String title;
  final String? thumbnailUrl;
  final String pdfUrl;

  const MusicSheet({
    required this.id,
    required this.title,
    this.thumbnailUrl,
    required this.pdfUrl,
  });
}

// ─── Swap this list out for your API response ──────────────────────────────────
final List<MusicSheet?> _slots = List.generate(12, (_) => null);
// When backend is ready, do something like:
//   final List<MusicSheet?> _slots = sheets.cast<MusicSheet?>();

// ═══════════════════════════════════════════════════════════════════════════════
// PAGE
// ═══════════════════════════════════════════════════════════════════════════════

class Music_Library_Page extends StatefulWidget {
  const Music_Library_Page({super.key});

  @override
  State<Music_Library_Page> createState() => _Music_Library_PageState();
}

class _Music_Library_PageState extends State<Music_Library_Page> {
  final TextEditingController _search = TextEditingController();
  final FocusNode _focus = FocusNode();
  bool _focused = false;

  @override
  void initState() {
    super.initState();
    _focus.addListener(() => setState(() => _focused = _focus.hasFocus));
    _search.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _search.dispose();
    _focus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnnotatedRegion<SystemUiOverlayStyle>(
      // Dark status bar icons since the background is now light.
      value: SystemUiOverlayStyle.dark,
      child: Scaffold(
        backgroundColor: const Color.fromARGB(255, 236, 236, 236),
        body: SafeArea(
          bottom: false,
          // LiquidGlassView gives the CloseButton's LiquidGlassLens something
          // to refract on Skia backends (e.g. macOS desktop) instead of
          // silently falling back to a flat frosted look. backgroundWidget
          // is still rendered normally — it's just also captured for the lens.
          child: LiquidGlassView(
            backgroundWidget: Container(
              color: const Color.fromARGB(255, 255, 255, 255),
            ),
            child: GestureDetector(
              onTap: () => FocusScope.of(context).unfocus(),
              behavior: HitTestBehavior.translucent,
              child: Column(
                children: [
                  // ── Top row: close button + search bar ───────────────────────
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.center,
                    children: [
                      const Padding(
                        padding: EdgeInsets.only(left: 16, top: 24),
                        child: CloseButton(),
                      ),
                      Expanded(
                        child: _SearchBar(
                          controller: _search,
                          focusNode: _focus,
                          focused: _focused,
                        ),
                      ),
                    ],
                  ),

                  // ── Grid ────────────────────────────────────────────────────
                  Expanded(
                    child: GridView.builder(
                      padding: const EdgeInsets.fromLTRB(16, 8, 16, 40),
                      gridDelegate:
                          const SliverGridDelegateWithFixedCrossAxisCount(
                        crossAxisCount: 2,
                        crossAxisSpacing: 12,
                        mainAxisSpacing: 16,
                        childAspectRatio: 0.68,
                      ),
                      itemCount: _slots.length,
                      itemBuilder: (_, i) => _ParkingSlot(sheet: _slots[i]),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// SEARCH BAR  — light "frosted glass" pill
// ═══════════════════════════════════════════════════════════════════════════════

class _SearchBar extends StatelessWidget {
  final TextEditingController controller;
  final FocusNode focusNode;
  final bool focused;

  const _SearchBar({
    required this.controller,
    required this.focusNode,
    required this.focused,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
        height: 48,
        decoration: BoxDecoration(
          // Glass: layered dark transparency over the light bg + subtle border
          color: focused
              ? Colors.black.withValues(alpha: 0.06)
              : Colors.black.withValues(alpha: 0.035),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: focused
                ? Colors.black.withValues(alpha: 0.14)
                : Colors.black.withValues(alpha: 0.08),
            width: 1,
          ),
          boxShadow: focused
              ? [
                  BoxShadow(
                    color: Colors.black.withValues(alpha: 0.04),
                    blurRadius: 20,
                    spreadRadius: 0,
                  ),
                ]
              : [],
        ),
        child: Row(
          children: [
            const SizedBox(width: 14),
            Icon(
              Icons.search_rounded,
              size: 20,
              color: Colors.black.withValues(alpha: focused ? 0.55 : 0.35),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: TextField(
                controller: controller,
                focusNode: focusNode,
                style: const TextStyle(
                  color: Colors.black87,
                  fontSize: 16,
                  fontWeight: FontWeight.w400,
                  letterSpacing: -0.2,
                ),
                decoration: InputDecoration(
                  hintText: 'Search',
                  hintStyle: TextStyle(
                    color: Colors.black.withValues(alpha: 0.30),
                    fontSize: 16,
                    fontWeight: FontWeight.w400,
                  ),
                  border: InputBorder.none,
                  isDense: true,
                ),
                cursorColor: Colors.black87,
                cursorHeight: 18,
              ),
            ),
            // Clear button
            if (controller.text.isNotEmpty)
              GestureDetector(
                onTap: () => controller.clear(),
                child: Padding(
                  padding: const EdgeInsets.only(right: 12),
                  child: Icon(
                    Icons.cancel,
                    size: 17,
                    color: Colors.black.withValues(alpha: 0.30),
                  ),
                ),
              )
            else
              const SizedBox(width: 14),
          ],
        ),
      ),
    );
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Close page button — top-left corner, small and subtle
// ═══════════════════════════════════════════════════════════════════════════════
class CloseButton extends StatelessWidget {
  const CloseButton({super.key});
  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => Navigator.pop(context),
      child: LiquidGlass(
        borderRadius: BorderRadius.circular(16),
        blur: 12,
        tintOpacity: 0.22,
        child: const SizedBox(
          width: 32,
          height: 32,
          child: Center(
            child: Icon(
              Icons.close_rounded,
              size: 18,
              color: Color.fromARGB(255, 180, 180, 180),
            ),
          ),
        ),
      ),
    );
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// PARKING SLOT  — empty or filled card
// ═══════════════════════════════════════════════════════════════════════════════

class _ParkingSlot extends StatelessWidget {
  final MusicSheet? sheet;
  const _ParkingSlot({this.sheet});

  @override
  Widget build(BuildContext context) {
    final isEmpty = sheet == null;

    return GestureDetector(
      onTap: isEmpty ? null : () { /* navigate to ScoreViewerPage */ },
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── PDF thumbnail area ──────────────────────────────────────────
          Expanded(
            child: Container(
              decoration: BoxDecoration(
                // Glass surface — a touch darker than the page bg
                color: isEmpty
                    ? const Color.fromARGB(255, 180, 180, 180).withValues(alpha: 0.06)
                    : const Color.fromARGB(255, 180, 180, 180).withValues(alpha: 0.10),
                borderRadius: BorderRadius.circular(14),
                border: Border.all(
                  color: const Color.fromARGB(255, 180, 180, 180).withValues(
                    alpha: isEmpty ? 0.18 : 0.28,
                  ),
                  width: 1,
                ),
              ),
              child: isEmpty
                  ? _EmptySlotContent()
                  : _FilledSlotContent(sheet: sheet!),
            ),
          ),

          const SizedBox(height: 9),

          // ── Name placeholder ────────────────────────────────────────────
          if (isEmpty) ...[
            // Two stacked pill placeholders like empty text lines
            _PlaceholderLine(width: double.infinity, height: 12),
            const SizedBox(height: 5),
            _PlaceholderLine(width: 80, height: 10),
          ] else ...[
            Text(
              sheet!.title,
              style: const TextStyle(
                color: Color.fromARGB(255, 144, 144, 144),
                fontSize: 13,
                fontWeight: FontWeight.w500,
                letterSpacing: -0.2,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            const SizedBox(height: 2),
            Text(
              'Tap to open',
              style: TextStyle(
                color: Colors.black.withValues(alpha: 0.40),
                fontSize: 11,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

// ── Empty slot interior ─────────────────────────────────────────────────────

class _EmptySlotContent extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Dashed rectangle to suggest a PDF page
          CustomPaint(
            size: const Size(48, 62),
            painter: _DashedPagePainter(),
          ),
        ],
      ),
    );
  }
}

class _DashedPagePainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = Colors.black.withValues(alpha: 0.22)
      ..strokeWidth = 1.2
      ..style = PaintingStyle.stroke;

    const r = 4.0;
    const dash = 4.0;
    const gap = 3.0;

    final rect = RRect.fromRectAndRadius(
      Rect.fromLTWH(0, 0, size.width, size.height),
      const Radius.circular(r),
    );

    final path = Path()..addRRect(rect);
    final metrics = path.computeMetrics();

    for (final metric in metrics) {
      double dist = 0;
      bool draw = true;
      while (dist < metric.length) {
        final len = draw ? dash : gap;
        if (draw) {
          canvas.drawPath(metric.extractPath(dist, dist + len), paint);
        }
        dist += len;
        draw = !draw;
      }
    }

    // Tiny dog-ear fold top-right corner
    final foldPaint = Paint()
      ..color = Colors.black.withValues(alpha: 0.16)
      ..style = PaintingStyle.fill;

    final foldPath = Path()
      ..moveTo(size.width - 12, 0)
      ..lineTo(size.width, 12)
      ..lineTo(size.width - 12, 12)
      ..close();
    canvas.drawPath(foldPath, foldPaint);

    // Three faint lines to suggest text on the page
    final linePaint = Paint()
      ..color = Colors.black.withValues(alpha: 0.16)
      ..strokeWidth = 1.0
      ..style = PaintingStyle.stroke;

    for (int i = 0; i < 3; i++) {
      final y = size.height * 0.45 + i * 7.0;
      canvas.drawLine(
        Offset(size.width * 0.18, y),
        Offset(size.width * (i == 2 ? 0.65 : 0.82), y),
        linePaint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter old) => false;
}

// ── Filled slot interior ────────────────────────────────────────────────────

class _FilledSlotContent extends StatelessWidget {
  final MusicSheet sheet;
  const _FilledSlotContent({required this.sheet});

  @override
  Widget build(BuildContext context) {
    if (sheet.thumbnailUrl != null) {
      return ClipRRect(
        borderRadius: BorderRadius.circular(14),
        child: Image.network(
          sheet.thumbnailUrl!,
          fit: BoxFit.cover,
          errorBuilder: (_, _, _) => _EmptySlotContent(),
        ),
      );
    }
    return _EmptySlotContent();
  }
}

// ── Placeholder text line ───────────────────────────────────────────────────

class _PlaceholderLine extends StatelessWidget {
  final double width;
  final double height;
  const _PlaceholderLine({required this.width, required this.height});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: width,
      height: height,
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.07),
        borderRadius: BorderRadius.circular(6),
      ),
    );
  }
}