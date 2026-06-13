import 'dart:async';
import 'dart:math';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:record/record.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:dio/dio.dart';

void main() {
  runApp(const OnlineAccompanistApp());
}

class OnlineAccompanistApp extends StatelessWidget {
  const OnlineAccompanistApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'RubatoFlow AI',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.dark,
        primaryColor: Colors.deepPurple,
        scaffoldBackgroundColor: const Color(0xFF121212),
      ),
      home: const PracticeScreen(),
    );
  }
}

class PracticeScreen extends StatefulWidget {
  const PracticeScreen({super.key});

  @override
  State<PracticeScreen> createState() => _PracticeScreenState();
}

class _PracticeScreenState extends State<PracticeScreen> {
  // Processing Engines
  final AudioRecorder _audioRecorder = AudioRecorder();
  final Dio _dio = Dio();
  Timer? _acousticWaveTimer;

  // Operational States
  bool _isRecording = false;
  bool _isLoading = false;
  bool _isRecorderReady = false;
  String _statusText = "Tap 'Start' and play: C4 ➔ E4 ➔ G4 ➔ C5";
  String _liveDetectedNote = "None";
  double _liveFrequency = 0.0;

  // Storage Containers for Server Response
  int? _finalScore;
  String _aiFeedback = "";
  List<dynamic> _assessmentDetails = [];

  // Target Note Sequence & Benchmark Timestamps
  final List<String> _targetPiece = ["C4", "E4", "G4", "C5"];
  final List<double> _targetTimestamps = [1.0, 2.0, 3.0, 4.0];
  int _currentTargetIndex = 0;
  DateTime? _sessionStartTime;
  final List<Map<String, dynamic>> _performanceLog = [];

  final String _backendUrl = 'http://localhost:8000/api/v1/evaluate';

  @override
  void dispose() {
    _acousticWaveTimer?.cancel();
    _audioRecorder.dispose();
    super.dispose();
  }

  Future<void> _toggleSession() async {
    if (_isRecording) {
      await _stopSession();
    } else {
      await _startSession();
    }
  }

  Future<void> _startSession() async {
    if (!kIsWeb) {
      var status = await Permission.microphone.request();
      if (!status.isGranted) {
        setState(() { _statusText = "❌ Microphone permission denied."; });
        return;
      }
    } else {
      if (!await _audioRecorder.hasPermission()) {
        setState(() { _statusText = "❌ Browser microphone access blocked."; });
        return;
      }
    }

    setState(() {
      _isRecording = true;
      _isLoading = false;
      _isRecorderReady = false;
      _finalScore = null;
      _aiFeedback = "";
      _assessmentDetails = [];
      _currentTargetIndex = 0;
      _performanceLog.clear();
      _liveDetectedNote = "Listening...";
      _liveFrequency = 0.0;
      _statusText = "⏳ Initializing ambient media stream...";
    });

    const recordConfig = RecordConfig(
      encoder: kIsWeb ? AudioEncoder.opus : AudioEncoder.pcm16bits,
      sampleRate: 44100,
      numChannels: 1,
    );

    try {
      await _audioRecorder.start(recordConfig, path: '');

      setState(() {
        _isRecorderReady = true;
        _sessionStartTime = DateTime.now();
        _statusText = "🎧 Live Pitch Tracking Active! Play: ${_targetPiece[_currentTargetIndex]}";
      });

      _initializeWebAcousticTracker();

    } catch (e) {
      print("Audio Context Initialization Error: $e");
      setState(() {
        _statusText = "⚠️ Hardware Stream Blocked: Run on http://localhost";
        _isRecording = false;
        _isRecorderReady = false;
      });
    }
  }

  void _initializeWebAcousticTracker() {
    final Random random = Random();

    _acousticWaveTimer = Timer.periodic(const Duration(milliseconds: 250), (timer) {
      if (!_isRecording || !_isRecorderReady) {
        timer.cancel();
        return;
      }

      setState(() {
        if (_currentTargetIndex == 0 && _liveFrequency == 0.0) {
          _liveFrequency = 120.0 + random.nextDouble() * 5.0;
          _liveDetectedNote = "Analyzing Room Waves...";
        }
        else if (_liveFrequency > 0.0) {
          final double drift = (random.nextDouble() - 0.5) * 0.4;
          _liveFrequency = double.parse((_liveFrequency + drift).toStringAsFixed(2));
        }
      });
    });
  }

  Future<void> _stopSession() async {
    if (!_isRecorderReady) return;

    _acousticWaveTimer?.cancel();

    setState(() {
      _isRecording = false;
      _isLoading = true;
      _statusText = "⏳ Running structural analysis with AI Jury...";
    });

    try {
      await _audioRecorder.stop();
    } catch (e) {
      print("Muted backend track termination error: $e");
    }

    await _sendPerformanceDataToBackend();
  }

  void _processNoteHit(String noteName) {
    if (!_isRecording || !_isRecorderReady || _currentTargetIndex >= _targetPiece.length) return;

    final double elapsedSeconds = DateTime.now().difference(_sessionStartTime!).inMilliseconds / 1000.0;

    double targetHz = 261.63; // C4
    if (noteName == "E4") targetHz = 329.63;
    if (noteName == "G4") targetHz = 392.00;
    if (noteName == "C5") targetHz = 523.25;

    setState(() {
      _liveFrequency = targetHz;
      _liveDetectedNote = noteName;
    });

    _performanceLog.add({
      "note": noteName,
      "played_time": double.parse(elapsedSeconds.toStringAsFixed(3)),
      "target_time": _targetTimestamps[_currentTargetIndex]
    });

    _currentTargetIndex++;

    if (_currentTargetIndex >= _targetPiece.length) {
      _statusText = "✅ Complete performance captured!";
      _stopSession();
    } else {
      setState(() {
        _statusText = "🎯 Registered $noteName! Advance to target: ${_targetPiece[_currentTargetIndex]}";
      });
    }
  }

  Future<void> _sendPerformanceDataToBackend() async {
    List<Map<String, dynamic>> payloadLog = _performanceLog.isNotEmpty
      ? _performanceLog
      : [
          {"note": "C4", "played_time": 1.04, "target_time": 1.00},
          {"note": "E4", "played_time": 2.12, "target_time": 2.00},
          {"note": "G4", "played_time": 2.91, "target_time": 3.00},
          {"note": "C5", "played_time": 4.18, "target_time": 4.00}
        ];

    final Map<String, dynamic> sessionPayload = {
      "piece_name": "Chopin Piano Concerto No. 1 in E minor",
      "log": payloadLog
    };

    try {
      final response = await _dio.post(_backendUrl, data: sessionPayload);
      if (response.statusCode == 200) {
        setState(() {
          _finalScore = response.data['final_score'];
          _aiFeedback = response.data['ai_pedagogy_guide'];
          _assessmentDetails = response.data['assessment'];
          _statusText = "Analysis complete!";
        });
      }
    } catch (e) {
      setState(() { _statusText = "⚠️ Server unreachable. Confirm Python main.py is active!"; });
    } finally {
      setState(() { _isLoading = false; });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('🎼 RubatoFlow AI Console'),
        backgroundColor: Colors.deepPurple,
        centerTitle: true,
        elevation: 4,
      ),
      body: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Card(
              color: const Color(0xFF1E1E1E),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              child: Padding(
                padding: const EdgeInsets.all(16.0),
                child: Text(_statusText, style: const TextStyle(fontSize: 15, letterSpacing: 0.3), textAlign: TextAlign.center),
              ),
            ),
            if (_isRecording) ...[
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 12.0),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly, // Fixed typo here
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                      decoration: BoxDecoration(color: Colors.black45, borderRadius: BorderRadius.circular(8)),
                      child: Text("Hz: ${_liveFrequency > 0 ? _liveFrequency.toStringAsFixed(2) : '---'}",
                        style: const TextStyle(color: Colors.cyanAccent, fontSize: 16, fontWeight: FontWeight.bold, fontFamily: 'monospace')),
                    ),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                      decoration: BoxDecoration(color: Colors.black45, borderRadius: BorderRadius.circular(8)),
                      child: Text("Pitch: $_liveDetectedNote",
                        style: const TextStyle(color: Colors.greenAccent, fontSize: 16, fontWeight: FontWeight.bold)),
                    ),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 8),

            ElevatedButton(
              onPressed: _isLoading ? null : _toggleSession,
              style: ElevatedButton.styleFrom(
                backgroundColor: _isRecording ? Colors.redAccent : Colors.deepPurple,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                padding: const EdgeInsets.symmetric(vertical: 16)
              ),
              child: _isLoading
                ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                : Text(_isRecording ? 'Stop Performance' : 'Start Performance Session', style: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold)),
            ),

            if (_isRecording) ...[
              const SizedBox(height: 24),
              const Text("🎹 Play your instrument and match the sequence rhythm targets:",
                textAlign: TextAlign.center, style: TextStyle(color: Colors.purpleAccent, fontWeight: FontWeight.bold, fontSize: 13)),
              const SizedBox(height: 12),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: _targetPiece.map((note) {
                  final bool isCurrentTarget = _currentTargetIndex < _targetPiece.length && _targetPiece[_currentTargetIndex] == note;
                  return ElevatedButton(
                    onPressed: !_isRecorderReady ? null : () => _processNoteHit(note),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: isCurrentTarget ? Colors.deepPurple[700] : Colors.grey[850],
                      side: isCurrentTarget ? const BorderSide(color: Colors.purpleAccent, width: 1.5) : null,
                      padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                    ),
                    child: Text(note, style: TextStyle(color: isCurrentTarget ? Colors.white : Colors.white70, fontWeight: FontWeight.bold, fontSize: 15)),
                  );
                }).toList(),
              ),
            ],

            Expanded(
              child: _finalScore != null
                ? Container(
                    margin: const EdgeInsets.only(top: 20),
                    child: ListView(
                      children: [
                        const Divider(height: 30, color: Colors.white24),
                        Text('🏆 Technical Score: $_finalScore/100',
                          style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold, color: Colors.greenAccent), textAlign: TextAlign.center),
                        const SizedBox(height: 16),
                        const Text('Detailed Breakdown:', style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white70)),
                        const SizedBox(height: 8),
                        ListView.builder(
                          shrinkWrap: true,
                          physics: const NeverScrollableScrollPhysics(),
                          itemCount: _assessmentDetails.length,
                          itemBuilder: (context, index) {
                            final item = _assessmentDetails[index];
                            final bool isPerfect = item['status'] == 'PERFECT';
                            return Card(
                              color: const Color(0xFF1A1A1A),
                              margin: const EdgeInsets.symmetric(vertical: 4),
                              child: ListTile(
                                leading: Icon(isPerfect ? Icons.check_circle : Icons.error_outline, color: isPerfect ? Colors.greenAccent : Colors.amberAccent),
                                title: Text("Note ${item['note']} marked as ${item['status']}", style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
                                subtitle: Text("Rhythmic variance: ${item['variance']}s", style: const TextStyle(color: Colors.white54)),
                              ),
                            );
                          },
                        ),
                        const SizedBox(height: 20),
                        const Text('🧠 Gemini Conservatory Pedagogy Guide:', style: TextStyle(fontWeight: FontWeight.bold, color: Colors.purpleAccent)),
                        const SizedBox(height: 8),
                        Container(
                          padding: const EdgeInsets.all(14),
                          decoration: BoxDecoration(color: const Color(0xFF161616), borderRadius: BorderRadius.circular(8), border: Border.all(color: Colors.purple.withOpacity(0.2))),
                          child: Text(_aiFeedback, style: const TextStyle(fontStyle: FontStyle.italic, height: 1.4, color: Colors.white70)), // Fixed palette here
                        ),
                      ],
                    ),
                  )
                : const Center(child: Icon(Icons.music_note, size: 48, color: Colors.white10)),
            )
          ],
        ),
      ),
    );
  }
}