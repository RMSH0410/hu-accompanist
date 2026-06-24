import 'package:flutter/material.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'DraggableRecorderButton.dart';

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
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFFE94560),
        ),
      ),
      home: const ScoreViewerPage(),
    );
  }
}

class ScoreViewerPage extends StatelessWidget {
  const ScoreViewerPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        // Using Stack at the root allows your components to float perfectly over each other
        child: Stack(
          children: [
            // LAYER 1: The Interactive PDF Viewer (Kept entirely on its own background layer)
            Positioned.fill(
              child: SfPdfViewer.asset(
                'assets/placeholder_score.pdf',
                canShowScrollHead: false,
                pageLayoutMode: PdfPageLayoutMode.single,
                scrollDirection: PdfScrollDirection.horizontal,
              ),
            ),

            // LAYER 2: Floating iPad-style Toolbar
            Positioned(
              top: 16,
              right: 16,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: const Color(0xFF16213E).withValues(alpha: 0.85),
                  borderRadius: BorderRadius.circular(30),
                  border: Border.all(
                    color: Colors.white.withValues(alpha: 0.1),
                  ),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    IconButton(
                      icon: const Icon(Icons.edit_outlined, size: 22),
                      color: Colors.white,
                      onPressed: () {},
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

            // LAYER 3: The Draggable Recorder Button
            // Placing it at the very bottom of the Stack ensures its rendering order stays on top.
            DraggableRecorderButton(
              onToggle: (isRecording) {
                if (isRecording) {
                  print('▶ started — call your Rust backend here');
                } else {
                  print('■ stopped — call your Rust backend here');
                }
              },
            ),
          ],
        ),
      ),
    );
  }
}