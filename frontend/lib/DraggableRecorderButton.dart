import 'dart:async';
import 'package:flutter/material.dart';

void main() => runApp(const MyApp());

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Audio Recorder',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(useMaterial3: true),
      home: const Scaffold(
        backgroundColor: Color(0xFF0F0F1A),
        body: Center(child: RecordButton()),
      ),
    );
  }
}

class RecordButton extends StatefulWidget {
  /// Called when recording starts.
  final VoidCallback? onStart;

  /// Called when recording stops.
  final VoidCallback? onStop;

  const RecordButton({super.key, this.onStart, this.onStop});

  @override
  State<RecordButton> createState() => _RecordButtonState();
}

class _RecordButtonState extends State<RecordButton>
    with SingleTickerProviderStateMixin {
  bool _isRecording = false;
  Duration _elapsed = Duration.zero;
  Timer? _timer;

  late final AnimationController _pulseCtrl;
  late final Animation<double> _pulseAnim;

  static const _idleColor  = Color(0xFF6C5CE7);
  static const _activeColor = Color(0xFFFF4757);

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    );
    _pulseAnim = Tween<double>(begin: 1.0, end: 1.5).animate(
      CurvedAnimation(parent: _pulseCtrl, curve: Curves.easeInOut),
    );
  }

  @override
  void dispose() {
    _timer?.cancel();
    _pulseCtrl.dispose();
    super.dispose();
  }

  void _toggle() {
    if (_isRecording) {
      _timer?.cancel();
      _pulseCtrl.stop();
      _pulseCtrl.reset();
      setState(() { _isRecording = false; });
      widget.onStop?.call();
    } else {
      setState(() { _isRecording = true; _elapsed = Duration.zero; });
      _pulseCtrl.repeat(reverse: true);
      _timer = Timer.periodic(const Duration(seconds: 1), (_) {
        setState(() => _elapsed += const Duration(seconds: 1));
      });
      widget.onStart?.call();
    }
  }

  String get _elapsedLabel {
    final m = _elapsed.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = _elapsed.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$m:$s';
  }

  @override
  Widget build(BuildContext context) {
    final color = _isRecording ? _activeColor : _idleColor;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        // Timer / hint label
        AnimatedSwitcher(
          duration: const Duration(milliseconds: 250),
          child: Text(
            _isRecording ? _elapsedLabel : 'Tap to record',
            key: ValueKey(_isRecording),
            style: TextStyle(
              color: _isRecording ? _activeColor : Colors.white54,
              fontSize: 20,
              fontWeight: FontWeight.w600,
              letterSpacing: 1.5,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
        ),

        const SizedBox(height: 40),

        // Button + pulse rings
        AnimatedBuilder(
          animation: _pulseAnim,
          builder: (_, __) => GestureDetector(
            onTap: _toggle,
            behavior: HitTestBehavior.opaque,
            child: SizedBox(
              width: 140,
              height: 140,
              child: Stack(
                alignment: Alignment.center,
                children: [
                  if (_isRecording) ...[
                    Transform.scale(
                      scale: _pulseAnim.value,
                      child: _ring(_activeColor, 0.10),
                    ),
                    Transform.scale(
                      scale: (_pulseAnim.value + 1) / 2,
                      child: _ring(_activeColor, 0.18),
                    ),
                  ],
                  AnimatedContainer(
                    duration: const Duration(milliseconds: 300),
                    curve: Curves.easeInOut,
                    width: 88,
                    height: 88,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: color,
                      boxShadow: [
                        BoxShadow(
                          color: color.withOpacity(0.45),
                          blurRadius: 28,
                          spreadRadius: 2,
                        ),
                      ],
                    ),
                    child: AnimatedSwitcher(
                      duration: const Duration(milliseconds: 200),
                      child: Icon(
                        _isRecording ? Icons.stop_rounded : Icons.mic_rounded,
                        key: ValueKey(_isRecording),
                        color: Colors.white,
                        size: 40,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),

        const SizedBox(height: 28),

        // Recording indicator dot + label
        AnimatedOpacity(
          opacity: _isRecording ? 1.0 : 0.0,
          duration: const Duration(milliseconds: 300),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              _BlinkingDot(color: _activeColor),
              const SizedBox(width: 8),
              Text(
                'Recording',
                style: TextStyle(
                  color: Colors.white.withOpacity(0.7),
                  fontSize: 13,
                  letterSpacing: 0.5,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _ring(Color color, double opacity) => Container(
        width: 110,
        height: 110,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: color.withValues(alpha: opacity,)
          ,
        ),
      );
}

// Blinking red dot shown while recording
class _BlinkingDot extends StatefulWidget {
  final Color color;
  const _BlinkingDot({required this.color});

  @override
  State<_BlinkingDot> createState() => _BlinkingDotState();
}

class _BlinkingDotState extends State<_BlinkingDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 600),
  )..repeat(reverse: true);

  @override
  void dispose() { _ctrl.dispose(); super.dispose(); }

  @override
  Widget build(BuildContext context) => FadeTransition(
        opacity: _ctrl,
        child: Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(shape: BoxShape.circle, color: widget.color),
        ),
      );
}