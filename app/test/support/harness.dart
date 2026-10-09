// 위젯 테스트 공통: 로그인된 세션, 화면 크기(Figma 390×844), 펌프 도우미.
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:guryong_mate/core/app_session.dart';
import 'package:guryong_mate/core/theme/app_theme.dart';
import 'package:guryong_mate/core/token_store.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'fixture_gateway.dart';

void setUpHarness() {
  setUpAll(() {
    GoogleFonts.config.allowRuntimeFetching = false;
    // 지도 타일 캐시가 path_provider 로 캐시 폴더를 묻는다. 테스트에선 플러그인이 없어 임시 폴더로 답한다.
    final tmp = Directory.systemTemp.createTempSync('guryong_test').path;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(const MethodChannel('plugins.flutter.io/path_provider'), (_) async => tmp);
  });
}

Future<void> phone(WidgetTester tester) async {
  await tester.binding.setSurfaceSize(const Size(390, 844));
  addTearDown(() => tester.binding.setSurfaceSize(null));
}

/// f11 의 refresh 응답으로 앱 시작 복원(POST /auth/refresh)을 거친 로그인 세션.
Future<AppSession> signedInSession(FixtureGateway gw, {Map<String, Object> prefs = const {}}) async {
  SharedPreferences.setMockInitialValues({'base_url': 'http://gateway.test', ...prefs});
  final f11 = loadFixture('f11_login.json');
  final refresh = (f11['exchanges'] as List).firstWhere((e) => e['name'] == 'refresh')['response'] as Map<String, dynamic>;
  gw.overrides.putIfAbsent('POST /auth/refresh', () => (_) => Res.json(200, refresh));
  final store = MemoryTokenStore(access: '<old_access>', refresh: '<refresh_token>', user: jsonEncode(refresh['user']));
  final s = await AppSession.load(clientFactory: gw.client, tokenStore: store);
  await s.restore();
  return s;
}

Widget app(AppSession session, Widget home) => ChangeNotifierProvider.value(
      value: session,
      child: MaterialApp(
        theme: buildTheme(),
        locale: const Locale('ko', 'KR'),
        supportedLocales: const [Locale('ko', 'KR')],
        localizationsDelegates: const [GlobalMaterialLocalizations.delegate, GlobalWidgetsLocalizations.delegate, GlobalCupertinoLocalizations.delegate],
        home: home,
      ),
    );

Future<void> settle(WidgetTester tester, [int frames = 20]) async {
  for (var i = 0; i < frames; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

/// 실제 이벤트 루프가 필요한 비동기(스트림 종료 등)를 흘려보내며 [cond] 를 기다린다.
Future<void> until(WidgetTester tester, bool Function() cond, {int tries = 100}) async {
  for (var i = 0; i < tries && !cond(); i++) {
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 10)));
    await tester.pump(const Duration(milliseconds: 20));
  }
  expect(cond(), isTrue, reason: '시간 안에 조건을 만족하지 않음');
}

Future<void> tapText(WidgetTester tester, String text, {int frames = 10}) async {
  final f = find.text(text).last;
  await tester.ensureVisible(f);
  await tester.pump();
  await tester.tap(f);
  await settle(tester, frames);
}

/// AI 시트 안의 위젯을 화면(헤더 아래 ~ 입력창 위) 안으로 끌어온다. 시트 목록은 DraggableScrollableSheet 의
/// 컨트롤러라 ensureVisible 로는 스크롤되지 않는다.
Future<void> reveal(WidgetTester tester, Finder f) async {
  final sheet = find.descendant(of: find.byType(DraggableScrollableSheet), matching: find.byType(Scrollable));
  if (sheet.evaluate().isEmpty) {
    await tester.ensureVisible(f);
    await tester.pump();
    return;
  }
  final view = tester.getRect(sheet.first);
  for (var i = 0; i < 40; i++) {
    if (f.evaluate().isNotEmpty) {
      final r = tester.getRect(f.first);
      if (r.top >= view.top + 8 && r.bottom <= view.bottom - 8) return;
      final dy = r.center.dy > view.center.dy ? -200.0 : 200.0;
      await tester.drag(sheet.first, Offset(0, dy));
    } else {
      await tester.drag(sheet.first, const Offset(0, -200));
    }
    await tester.pump(const Duration(milliseconds: 50));
  }
}

Future<void> tapIn(WidgetTester tester, Finder f, {int frames = 10}) async {
  await reveal(tester, f);
  await tester.tap(f.first);
  await settle(tester, frames);
}

/// 디버그: 화면의 글자 전부.
String texts(WidgetTester tester) => tester.widgetList<Text>(find.byType(Text)).map((t) => t.data ?? t.textSpan?.toPlainText()).join(' | ');
