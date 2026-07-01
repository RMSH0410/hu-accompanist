import 'package:flutter/material.dart';
import 'package:liquid_glass_easy/liquid_glass_easy.dart';

/// Reusable iOS-style "liquid glass" wrapper, now backed by the real
/// liquid_glass_easy package (LiquidGlassLens) instead of a hand-rolled
/// blur. Same call signature as before, so every call site that uses
/// `LiquidGlass(...)` keeps working unchanged.
///
/// On Impeller this refracts the live backdrop with no extra setup. On
/// Skia it needs an ancestor `LiquidGlassView` to refract — without one
/// it gracefully degrades to a frosted look.
class LiquidGlass extends StatelessWidget {
  final Widget child;
  final BorderRadius borderRadius;
  final double blur;
  final double tintOpacity;
  final Color tintColor;
  final double shadowOpacity;

  const LiquidGlass({
    super.key,
    required this.child,
    this.borderRadius = const BorderRadius.all(Radius.circular(20)),
    this.blur = 16,
    this.tintOpacity = 0.18,
    this.tintColor = const Color.fromARGB(255, 190, 190, 190),
    this.shadowOpacity = 0.20,
  });

  @override
  Widget build(BuildContext context) {
    // borderRadius is assumed uniform across corners for these buttons.
    final cornerRadius = borderRadius.topLeft.x;

    return DecoratedBox(
      // A plain Flutter drop shadow — independent of the glass shaders, so
      // it renders the same on Impeller and Skia and grounds the lens
      // against a light/white background where the glass alone is faint.
      decoration: BoxDecoration(
        borderRadius: borderRadius,
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: shadowOpacity),
            blurRadius: 18,
            offset: const Offset(0, 8),
          ),
          BoxShadow(
            color: Colors.black.withValues(alpha: shadowOpacity * 0.6),
            blurRadius: 4,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: LiquidGlassLens(
        style: LiquidGlassStyle(
          shape: LiquidGlassShape.continuousRoundedRectangle(
            cornerRadius: cornerRadius,
          ),
          appearance: LiquidGlassAppearance(
            color: tintColor.withValues(alpha: tintOpacity),
          ),
          refraction: const LiquidGlassRefraction(
            distortion: 0.1,
            distortionWidth: 22,
          ),
        ),
        child: child,
      ),
    );
  }
}