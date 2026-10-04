import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// Figma "Foundations" 색상 토큰. AI 요소에만 Purple 을 쓴다.
abstract final class AppColors {
  static const primary = Color(0xFF2F6BFF);
  static const primarySoft = Color(0xFFEAF0FF);
  static const ai = Color(0xFF7B61FF);
  static const aiSoft = Color(0xFFF2EFFF);
  static const aiLine = Color(0xFFE2DCFF);
  static const success = Color(0xFF12A150);
  static const successSoft = Color(0xFFE7F6EE);
  static const warning = Color(0xFFD97706);
  static const warningSoft = Color(0xFFFFF4E0);
  static const warningLine = Color(0xFFFBE1B5);
  static const danger = Color(0xFFE5484D);
  static const dangerSoft = Color(0xFFFDECEC);
  static const text = Color(0xFF111827);
  static const text2 = Color(0xFF4B5563);
  static const text3 = Color(0xFF8B95A1);
  static const line = Color(0xFFE8EBEF);
  static const rail = Color(0xFFE3E7ED);
  static const field = Color(0xFFF3F4F6);
  static const background = Color(0xFFF5F6F8);
  static const surface = Color(0xFFFFFFFF);
  static const dark = Color(0xFF1F2937);
  static const grabber = Color(0xFFD9DDE3);

  /// FAB · AI 버튼 · 로고에만 쓴다.
  static const aiGradient = LinearGradient(
    colors: [Color(0xFF6E59FF), Color(0xFFA66BFF)],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );
}

/// Noto Sans KR (google_fonts). 처음 접근할 때 글꼴을 내려받기 시작한다.
/// google_fonts 9 는 material_ui 패키지 기반이라 TextTheme 타입이 달라 글꼴 이름만 쓴다.
final String? appFontFamily = GoogleFonts.notoSansKr().fontFamily;

/// Figma 타이포 스케일 (크기/행간 Weight).
abstract final class AppText {
  static TextStyle _s(double size, double height, FontWeight w, [Color c = AppColors.text]) => TextStyle(
      fontFamily: appFontFamily, fontSize: size, height: height / size, fontWeight: w, color: c, letterSpacing: -0.2);

  static final display = _s(28, 36, FontWeight.w700);
  static final title = _s(24, 32, FontWeight.w700);
  static final headline = _s(17, 24, FontWeight.w700);
  static final section = _s(16, 22, FontWeight.w700);
  static final body = _s(15, 22, FontWeight.w400);
  static final label = _s(14, 20, FontWeight.w500);
  static final meta = _s(13, 18, FontWeight.w400, AppColors.text2);
  static final caption = _s(12, 16, FontWeight.w400, AppColors.text3);
}

abstract final class AppShadow {
  static const card = [BoxShadow(color: Color(0x0F111827), blurRadius: 12, offset: Offset(0, 2))];
  static const floating = [BoxShadow(color: Color(0x24111827), blurRadius: 24, offset: Offset(0, 8))];
}

ThemeData buildTheme() {
  final base = ThemeData(
    useMaterial3: true,
    fontFamily: appFontFamily,
    colorScheme: ColorScheme.fromSeed(seedColor: AppColors.primary, primary: AppColors.primary, surface: AppColors.surface),
    scaffoldBackgroundColor: AppColors.background,
  );
  return base.copyWith(
    textTheme: base.textTheme.apply(bodyColor: AppColors.text, displayColor: AppColors.text),
    appBarTheme: const AppBarTheme(backgroundColor: AppColors.background, surfaceTintColor: Colors.transparent, elevation: 0),
    bottomSheetTheme: const BottomSheetThemeData(backgroundColor: AppColors.surface, surfaceTintColor: Colors.transparent),
    dialogTheme: const DialogThemeData(backgroundColor: AppColors.surface, surfaceTintColor: Colors.transparent),
    popupMenuTheme: const PopupMenuThemeData(color: AppColors.surface, surfaceTintColor: Colors.transparent),
    snackBarTheme: const SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: AppColors.dark,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.all(Radius.circular(14))),
    ),
    inputDecorationTheme: const InputDecorationTheme(
      filled: true,
      fillColor: AppColors.field,
      contentPadding: EdgeInsets.symmetric(horizontal: 14, vertical: 15),
      border: OutlineInputBorder(borderRadius: BorderRadius.all(Radius.circular(12)), borderSide: BorderSide.none),
      hintStyle: TextStyle(color: AppColors.text3),
    ),
  );
}
