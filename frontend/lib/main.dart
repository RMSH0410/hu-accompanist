import 'package:flutter/material.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'DraggableRecorderButton.dart';
import 'DrawingOverlay.dart';
import 'dart:ffi' as ffi;

typedef StartRecordingFunc = ffi.Void Function();
typedef StartRecordingFuncDart = void Function();
typedef StopRecordingFunc = ffi.Void Function();
typedef StopRecordingFuncDart = void Function();

// ─── Safe no-op stubs used when native symbols are unavailable ───────────────
void _stubStart() => debugPrint('NativeBridge: start_recording stub (symbols not linked yet)');
void _stubStop()  => debugPrint('NativeBridge: stop_recording stub (symbols not linked yet)');

class NativeBridge {
  // Nullable so we know whether real lookup succeeded
  ffi.DynamicLibrary? _nativeLib;

  // Always callable — fall back to stubs if lookup failed
  StartRecordingFuncDart _startRecording = _stubStart;
  StopRecordingFuncDart  _stopRecording  = _stubStop;

  bool get isNativeAvailable => _nativeLib != null;

  NativeBridge() {
    // All lookup work is inside try/catch so a missing symbol
    // can NEVER reach main() and block the UI from rendering.
    try {
      final lib = ffi.DynamicLibrary.executable();

      _startRecording = lib
          .lookup<ffi.NativeFunction<StartRecordingFunc>>('start_recording')
          .asFunction();

      _stopRecording = lib
          .lookup<ffi.NativeFunction<StopRecordingFunc>>('stop_recording')
          .asFunction();

      _nativeLib = lib; // only set AFTER both lookups succeed
      debugPrint('NativeBridge: native symbols linked successfully.');
    } on ArgumentError catch (e) {
      // Symbol not found — app keeps running with stubs
      debugPrint('NativeBridge: symbol lookup failed — $e');
      debugPrint('NativeBridge: running with no-op stubs. '
          'Make sure start_recording / stop_recording are compiled '
          'into the iOS Runner target with external "C" linkage.');
    } catch (e) {
      debugPrint('NativeBridge: unexpected init error — $e');
    }
  }

  // Public API — callers never touch private fields directly
  void startRecording() => _startRecording();
  void stopRecording()  => _stopRecording();
}

// Single shared instance — safe because constructor never throws now
final NativeBridge _nativeBridge = NativeBridge();

void main() {
  runApp(const HuAccumponistApp());
}

class HuAccumponistApp extends StatelessWidget {
  const HuAccumponistApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF1A1A2E),
        colorScheme: const ColorScheme.dark(primary: Color(0xFFE94560)),
      ),
      home: const ScoreViewerPage(),
    );
  }
}

class ScoreViewerPage extends StatefulWidget {
  const ScoreViewerPage({super.key});

  @override
  State<ScoreViewerPage> createState() => _ScoreViewerPageState();
}

class _ScoreViewerPageState extends State<ScoreViewerPage> {
  bool _isDrawingMode = false;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Stack(
          children: [
            // LAYER 1: PDF Viewer
            Positioned.fill(
              child: SfPdfViewer.asset(
                'assets/placeholder_score.pdf',
                canShowScrollHead: false,
                pageLayoutMode: PdfPageLayoutMode.single,
                scrollDirection: PdfScrollDirection.horizontal,
              ),
            ),

            // LAYER 2: Drawing overlay
            DrawingOverlay(isDrawingMode: _isDrawingMode),

            // LAYER 3: Floating Toolbar
            Positioned(
              top: 16,
              right: 16,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: const Color(0xFF16213E).withValues(alpha: 0.85),
                  borderRadius: BorderRadius.circular(30),
                  border: Border.all(color: Colors.white.withValues(alpha: 0.1)),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    IconButton(
                      icon: Icon(
                        Icons.edit_outlined,
                        size: 22,
                        color: _isDrawingMode
                            ? const Color(0xFFE94560)
                            : Colors.white,
                      ),
                      onPressed: () =>
                          setState(() => _isDrawingMode = !_isDrawingMode),
                    ),
                    IconButton(
                      icon: const Icon(Icons.more_horiz, size: 22),
                      color: Colors.white,
                      onPressed: () {},
                    ),
                  ],
                ),
              ),
            ),

            // LAYER 4: Draggable Recorder Button
            // Uses public methods — no private field access across class boundary
            DraggableRecorderButton(
              onToggle: (isRecording) {
                if (isRecording) {
                  _nativeBridge.startRecording();
                } else {
                  _nativeBridge.stopRecording();
                }
              },
            ),
          ],
        ),
      ),
    );
  }
}