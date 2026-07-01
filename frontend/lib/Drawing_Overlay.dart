import 'package:flutter/material.dart';

class Drawing_Overlay extends StatefulWidget {
  final bool isDrawingMode;
  const Drawing_Overlay({super.key, required this.isDrawingMode});

  @override
  State<Drawing_Overlay> createState() => _Drawing_OverlayState();
}

class _Drawing_OverlayState extends State<Drawing_Overlay> {
  final List<List<Offset>> _strokes = [];
  List<Offset> _currentStroke = [];

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      // Pass touches through when not drawing
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
        painter: _strokes.isNotEmpty || _currentStroke.isNotEmpty
            ? _StrokePainter(strokes: _strokes, currentStroke: _currentStroke)
            : null, // no painter = no repaint cost when canvas is empty
      ),
    );
  }
}

// _StrokePainter stays exactly the same as before
class _StrokePainter extends CustomPainter {
  final List<List<Offset>> strokes;
  final List<Offset> currentStroke;

  _StrokePainter({required this.strokes, required this.currentStroke});

  final _paint = Paint()
    ..color = const Color(0xFFE94560)
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