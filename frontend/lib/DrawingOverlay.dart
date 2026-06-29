import 'package:flutter/material.dart';

class DrawingOverlay extends StatefulWidget {
  final bool isDrawingMode;
  const DrawingOverlay({super.key, required this.isDrawingMode});

  @override
  State<DrawingOverlay> createState() => _DrawingOverlayState();
}

class _DrawingOverlayState extends State<DrawingOverlay> {
  final List<List<Offset>> _strokes = [];
  List<Offset> _currentStroke = [];

  @override
  Widget build(BuildContext context) {
    // When not drawing and nothing drawn, let touches pass through to PDF
    if (!widget.isDrawingMode && _strokes.isEmpty) return const SizedBox.shrink();

    return Positioned.fill(
      child: GestureDetector(
        // Absorb touches only when drawing mode is on
        behavior: widget.isDrawingMode
            ? HitTestBehavior.opaque
            : HitTestBehavior.translucent,
        onPanStart: widget.isDrawingMode
            ? (d) => setState(() => _currentStroke = [d.localPosition])
            : null,
        onPanUpdate: widget.isDrawingMode
            ? (d) => setState(() => _currentStroke.add(d.localPosition))
            : null,
        onPanEnd: widget.isDrawingMode
            ? (d) => setState(() {
                  if (_currentStroke.isNotEmpty) {
                    _strokes.add(List.from(_currentStroke));
                    _currentStroke = [];
                  }
                })
            : null,
        child: CustomPaint(
          painter: _StrokePainter(
            strokes: _strokes,
            currentStroke: _currentStroke,
          ),
        ),
      ),
    );
  }
}

class _StrokePainter extends CustomPainter {
  final List<List<Offset>> strokes;
  final List<Offset> currentStroke;

  _StrokePainter({required this.strokes, required this.currentStroke});

  final _paint = Paint()
    ..color = Color(0xFFE94560)
    ..strokeWidth = 3.0
    ..strokeCap = StrokeCap.round
    ..strokeJoin = StrokeJoin.round
    ..style = PaintingStyle.stroke;

  void _drawStroke(Canvas canvas, List<Offset> stroke) {
    if (stroke.length < 2) return;
    final path = Path()..moveTo(stroke[0].dx, stroke[0].dy);
    for (int i = 1; i < stroke.length; i++) {
      path.lineTo(stroke[i].dx, stroke[i].dy);
    }
    canvas.drawPath(path, _paint);
  }

  @override
  void paint(Canvas canvas, Size size) {
    for (final s in strokes) {
      _drawStroke(canvas, s);
    }
    _drawStroke(canvas, currentStroke);
  }

  @override
  bool shouldRepaint(_StrokePainter old) => true;
}