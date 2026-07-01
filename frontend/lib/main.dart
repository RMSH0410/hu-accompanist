import 'package:flutter/material.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'package:liquid_glass_easy/liquid_glass_easy.dart';
import 'Draggable_Recorder_Button.dart';
import 'Drawing_Overlay.dart';
import 'Music_Library_Page.dart';
import 'LiquidGlass.dart';
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
        scaffoldBackgroundColor: const Color.fromARGB(255, 255, 255, 255),
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
  final bool _hasMusicSheet = true; // Track if a music sheet is loaded

  void _goToNavPage() {
    Navigator.of(context).push(
      PageRouteBuilder(
        transitionDuration: const Duration(milliseconds: 400),
        pageBuilder: (context, animation, secondaryAnimation) =>
            const Music_Library_Page(), // 👈 replace with your class name
        transitionsBuilder: (context, animation, secondaryAnimation, child) {
          final tween = Tween(
            begin: const Offset(0.0, 1.0),
            end: Offset.zero,
          ).chain(CurveTween(curve: Curves.easeOutCubic));
          return SlideTransition(
            position: animation.drive(tween),
            child: child,
          );
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: LiquidGlassView(
          // Everything the glass buttons should refract goes here. This is
          // still rendered normally and stays fully interactive (e.g. the
          // Drawing_Overlay's gestures keep working) — it's just *also*
          // captured for the lenses on Skia backends like macOS desktop.
          backgroundWidget: Stack(
            children: [
              // LAYER 1: PDF Viewer (or placeholder background)
              // 1. If true, look at how the Widget sits right under the if statement (no braces!)
              if (_hasMusicSheet)
                Positioned.fill(
                  child: SfPdfViewer.asset(
                    'assets/test.pdf',
                    canShowScrollHead: false,
                    pageLayoutMode: PdfPageLayoutMode.single,
                    scrollDirection: PdfScrollDirection.horizontal,
                  ),
                ) // 👈 Note: NO trailing comma or semicolon directly after the widget if an 'else' follows
              // 2. If false, the else statement also has no braces
              else
                Positioned.fill(
                  child: Container(color: const Color.fromARGB(255, 236, 236, 236)),
                ), // 👈 Comma goes here at the very end of the whole if/else block

              // LAYER 2: Drawing overlay
              Positioned.fill(
                child: Drawing_Overlay(isDrawingMode: _isDrawingMode),
              ),
            ],
          ), // closes Stack (backgroundWidget)

          child: Stack(
            children: [
              // LAYER 3: Floating Toolbar
              Positioned(
                top: 16,
                right: 16,
                child: LiquidGlass(
                  borderRadius: BorderRadius.circular(30),
                  blur: 18,
                  tintOpacity: 0.16,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        IconButton(
                          icon: Icon(
                            Icons.edit_outlined,
                            size: 22,
                            color: _isDrawingMode
                                ? const Color(0xFFE94560)
                                : const Color.fromARGB(255, 170, 170, 170),
                          ),
                          onPressed: () =>
                              setState(() => _isDrawingMode = !_isDrawingMode),
                        ),
                        IconButton(
                          icon: const Icon(Icons.more_horiz, size: 22),
                          color: const Color.fromARGB(255, 170, 170, 170),
                          onPressed: () {},
                        ),
                      ],
                    ),
                  ),
                ),
              ),

              // LAYER 4: Draggable Recorder Button
              Draggable_Recorder_Button(
                onToggle: (isRecording) {
                  if (isRecording) {
                    _nativeBridge.startRecording();
                  } else {
                    _nativeBridge.stopRecording();
                  }
                },
              ),

              // LAYER 5: Nav Button
              Positioned(
                bottom: 32,
                left: 16,
                child: GestureDetector(
                  onTap: _goToNavPage,
                  child: LiquidGlass(
                    borderRadius: BorderRadius.circular(16),
                    blur: 14,
                    tintOpacity: 0.20,
                    child: Padding(
                      padding: const EdgeInsets.all(14),
                      child: Icon(
                        Icons.search,
                        color: const Color.fromARGB(255, 170, 170, 170),
                      ),
                    ),
                  ),
                ),
              ),
            ], // closes children of foreground Stack
          ), // closes foreground Stack (child:)
        ), // closes LiquidGlassView
      ), // closes SafeArea
    ); // closes Scaffold
  }
}