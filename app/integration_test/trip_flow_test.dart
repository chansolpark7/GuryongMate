// 동행 모드 장면 ②③④ — 실제 앱(데모 위치 모드)을 각본 LLM 스택에 붙이고, 서버 쪽은 CLI 재생기로 위치·시각·안전 상황을 넣는다.
//   docker compose up -d --build                      (저장소 루트, .env 의 API_PORT. backend/.env: AGENT_PROVIDER=scripted, SAFETY_SOURCE=mock)
//   flutter test integration_test/trip_flow_test.dart -d windows --dart-define=LIVE_API=http://127.0.0.1:8020 \
//     --dart-define=DEMO=true --dart-define=REPO=C:/path/to/GuryongMate
// 앱은 GPS 를 보내지 않고 서버의 TripState.location·이벤트를 그린다 (trip §2.2). 재생기는
//   python scripts/planner/cli.py trip replay new scene2_arrival|scene3_delay|scene4_safety --speed N
// 을 테스트가 직접 띄운다 (같은 계정으로 로그인, 세션 파일은 임시 HOME 에). 단계마다 build/screens_l2/NN_이름.png (390×844).
import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/api/api_client.dart';
import 'package:guryong_mate/core/app_session.dart';
import 'package:guryong_mate/core/token_store.dart';
import 'package:guryong_mate/features/chat/chat_controller.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';
import 'package:guryong_mate/features/trip/trip_controller.dart';
import 'package:guryong_mate/main.dart';
import 'package:integration_test/integration_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

const live = String.fromEnvironment('LIVE_API', defaultValue: 'http://127.0.0.1:8020');
const repo = String.fromEnvironment('REPO', defaultValue: '..');

final _root = GlobalKey();
var _shotNo = 0;

Future<void> shot(WidgetTester tester, String name) async {
  await tester.pump(const Duration(milliseconds: 300));
  final boundary = _root.currentContext!.findRenderObject()! as RenderRepaintBoundary;
  final image = await boundary.toImage(pixelRatio: 1);
  final png = await image.toByteData(format: ui.ImageByteFormat.png);
  final file = File('build/screens_l2/${(++_shotNo).toString().padLeft(2, '0')}_$name.png');
  await file.parent.create(recursive: true);
  await file.writeAsBytes(png!.buffer.asUint8List());
}

Future<void> settle(WidgetTester tester, [int ms = 800]) async {
  for (var t = 0; t < ms; t += 100) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

bool _exists(Finder f) {
  try {
    return f.evaluate().isNotEmpty;
  } catch (_) {
    return false;
  }
}

Future<void> waitFor(WidgetTester tester, Finder finder, {Duration timeout = const Duration(seconds: 60), String? name}) async {
  final end = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(end)) {
    await tester.pump(const Duration(milliseconds: 200));
    if (_exists(finder)) return;
  }
  await shot(tester, 'FAIL_${name ?? 'wait'}');
  throw TestFailure('시간 안에 나타나지 않음: $finder');
}

Future<void> waitUntil(WidgetTester tester, bool Function() cond, String what, {Duration timeout = const Duration(seconds: 90)}) async {
  final end = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(end)) {
    await tester.pump(const Duration(milliseconds: 200));
    if (cond()) return;
  }
  await shot(tester, 'FAIL');
  throw TestFailure('시간 안에 조건을 만족하지 않음: $what');
}

Future<void> reveal(WidgetTester tester, Finder f) async {
  final sheet = find.descendant(of: find.byType(DraggableScrollableSheet).last, matching: find.byType(Scrollable));
  if (sheet.evaluate().isEmpty) {
    await tester.ensureVisible(f.first);
    await tester.pump();
    return;
  }
  final view = tester.getRect(sheet.first);
  for (var i = 0; i < 40; i++) {
    if (_exists(f)) {
      final r = tester.getRect(f.first);
      if (r.top >= view.top + 8 && r.bottom <= view.bottom - 8) return;
      await tester.drag(sheet.first, Offset(0, r.center.dy > view.center.dy ? -200 : 200));
    } else {
      await tester.drag(sheet.first, const Offset(0, -200));
    }
    await settle(tester, 200);
  }
}

Future<void> tap(WidgetTester tester, Finder f, {int ms = 800}) async {
  await reveal(tester, f);
  await tester.tap(f.first);
  await settle(tester, ms);
}

/// 전역 오버레이(미리보기·경고·카드)와 화면 고정 버튼은 스크롤 없이 바로 누른다.
Future<void> tapNow(WidgetTester tester, Finder f, {int ms = 800}) async {
  await tester.tap(f.first);
  await settle(tester, ms);
}

Future<void> say(WidgetTester tester, String text) async {
  final field = find.byType(TextField).last;
  for (var i = 0; i < 3; i++) {
    await tester.tap(field);
    await settle(tester, 300);
    await tester.enterText(field, text);
    await settle(tester, 300);
    if (tester.widget<TextField>(field).controller?.text == text) break;
  }
  await tester.tap(find.byTooltip('보내기').last);
  await tester.pump();
}

Element _shell(WidgetTester tester) => tester.element(find.byType(IndexedStack, skipOffstage: false).first);
TripController tripOf(WidgetTester tester) => Provider.of<TripController>(_shell(tester), listen: false);
ChatController chatOf(WidgetTester tester) => Provider.of<ChatController>(_shell(tester), listen: false);

Future<void> waitTurn(WidgetTester tester) => waitUntil(tester, () => !chatOf(tester).busy, '응답 끝');

/// 서버 쪽 재생기 (같은 계정, 임시 HOME 의 세션 파일).
class Replay {
  Replay(this.scene, this.speed, this.home);
  final String scene;
  final double speed;
  final String home;
  late final Process proc;
  final lines = <String>[];
  int? planId;
  final _done = Completer<int>();

  static Map<String, String> env(String home) => {'USERPROFILE': home, 'HOME': home, 'PYTHONIOENCODING': 'utf-8', 'GURYONG_API_URL': live, 'NO_COLOR': '1'};

  Future<void> start() async {
    proc = await Process.start('python', ['-u', '$repo/scripts/planner/cli.py', '--url', live, 'trip', 'replay', 'new', scene, '--speed', '$speed'],
        environment: env(home), workingDirectory: repo);
    void listen(Stream<List<int>> s) => s.transform(utf8.decoder).transform(const LineSplitter()).map((l) => l.replaceAll(RegExp(r'\x1B\[[0-9;]*m'), '')).listen((l) {
          lines.add(l);
          debugPrint('[$scene] $l');
          final m = RegExp(r'계획 #(\d+)').firstMatch(l);
          if (m != null) planId ??= int.parse(m.group(1)!);
        });
    listen(proc.stdout);
    listen(proc.stderr);
    proc.exitCode.then(_done.complete);
  }

  bool get finished => _done.isCompleted;

  /// 재생기가 [pattern] 을 출력했는지.
  bool printed(Pattern pattern) => lines.any((l) => l.contains(pattern));
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('장면 ②③④ — 각본 LLM 스택 + 재생기, 데모 위치 모드', (tester) async {
    await tester.binding.setSurfaceSize(const Size(390, 844));
    AppConfig.demo = true;
    final prefs = await SharedPreferences.getInstance();
    await prefs.clear();
    await prefs.setString('base_url', live);
    await prefs.setBool('demo_location_mode', true);
    final store = SecureTokenStore();
    await store.clear();
    await ApiClient(baseUrl: live).put('/safety/sim', {'scenario': 'calm', 'reset_clock': true});

    // 계정: 앱에서 가입 → 같은 계정으로 CLI 로그인 (재생기가 만든 계획을 앱에서 본다)
    final session = await AppSession.load(tokenStore: store);
    final loginId = 'l2it${DateTime.now().millisecondsSinceEpoch % 100000000}';
    const password = 'guryong2026';
    await session.signInWith(await session.api.signup(loginId, password, displayName: '서윤'));
    final home = (await Directory.systemTemp.createTemp('guryong_l2_home')).path;
    final login = await Process.run('python', ['$repo/scripts/planner/cli.py', '--url', live, 'login', loginId, '--password', password],
        environment: Replay.env(home), workingDirectory: repo);
    expect(login.exitCode, 0, reason: '${login.stdout}\n${login.stderr}');

    await tester.pumpWidget(RepaintBoundary(key: _root, child: ChangeNotifierProvider.value(value: session, child: const GuryongApp())));
    await waitFor(tester, find.text('My Plans'));
    final nav = tester.state<NavigatorState>(find.byType(Navigator).first);

    /// 재생기를 띄우고, 여행일이 시작된 뒤(첫 위치) 계획 화면을 연다.
    Future<Replay> openScene(String scene, double speed) async {
      final r = Replay(scene, speed, home);
      await r.start();
      // 재생기가 여행일을 시작하고 상태를 출력하면 ("[계획 #N] 1일차 … 여행 중") 계획 화면을 연다 (11 자동 표시 전에)
      await waitUntil(tester, () => r.planId != null && r.printed('여행 중'), '$scene 시작', timeout: const Duration(minutes: 3));
      unawaited(nav.push(MaterialPageRoute<void>(builder: (_) => PlannerShell(planId: r.planId!))));
      await waitFor(tester, find.text('동행 중'), timeout: const Duration(seconds: 30), name: scene);
      await settle(tester, 1500);
      return r;
    }

    Future<void> closeScene(Replay r) async {
      await waitUntil(tester, () => r.finished, '${r.scene} 재생 끝', timeout: const Duration(minutes: 20));
      await session.api.tripEnd(r.planId!); // 끝내지 않으면 백그라운드 틱이 계속 판정한다 (trip §8)
      nav.popUntil((x) => x.isFirst);
      await settle(tester, 1000);
    }

    // ---------- 장면 ②: 12:30 가옥거리 도착 → 해설 카드 → 노트 → 질문 → "더 짧게" ----------
    final s2 = await openScene('scene2_arrival', 10);
    await shot(tester, '14s2_before_first');
    await waitFor(tester, find.byKey(const ValueKey('arrival-card')), timeout: const Duration(minutes: 3), name: 'arrival');
    await waitUntil(tester, () => _exists(find.textContaining('도착했어요 · 가이드')), '노트 상태');
    await settle(tester, 1500);
    await shot(tester, '16_arrival');
    if (_exists(find.text('짧게'))) {
      await tapNow(tester, find.text('짧게'), ms: 2500);
      await shot(tester, '17_guide_note');
      await tester.tap(find.byType(BackButton).last);
      await settle(tester, 1000);
    }
    tripOf(tester).dismissArrival(); // 카드가 Today FAB 위에 떠 있다 (Figma 16 과 같은 배치)
    await settle(tester, 500);
    await shot(tester, '14_today_visiting');
    await tapNow(tester, find.byTooltip('구룡메이트 열기').first, ms: 1500);
    await say(tester, '언제 지어진 집들이에요?');
    await waitTurn(tester);
    await settle(tester, 800);
    await say(tester, '다음부터는 더 짧게 해줘요');
    await waitTurn(tester);
    await settle(tester, 1000);
    expect(find.text('기억했어요 · 해설은 핵심만 짧게'), findsOneWidget);
    await shot(tester, '18_docent_chat');
    await tapNow(tester, find.byTooltip('닫기').last);
    await closeScene(s2);

    // ---------- 장면 ③: 30분 더 머묾 → 13:53 delay 선제 → 14-S3 → 19 → 20 → 21-S1 ----------
    final s3 = await openScene('scene3_delay', 20);
    await waitFor(tester, find.byKey(const ValueKey('arrival-card')), timeout: const Duration(minutes: 2), name: 'arrival3');
    tripOf(tester).dismissArrival();
    await waitUntil(tester, () => (tripOf(tester).state?.delayMinutes ?? 0) >= 1, '가벼운 지연', timeout: const Duration(minutes: 6));
    await settle(tester, 1500);
    await shot(tester, '14_light_delay');
    await waitFor(tester, find.byKey(const ValueKey('proactive-peek')), timeout: const Duration(minutes: 6), name: 'delay_peek');
    await settle(tester, 1000);
    await shot(tester, '14s3_delay_peek');
    await tapNow(tester, find.text('보기 ›'), ms: 2500);
    await waitFor(tester, find.byKey(const ValueKey('proactive-header')));
    await shot(tester, '19_proactive_delay');
    await tap(tester, find.text('구룡포과메기문화관은 유지해줘'), ms: 500);
    await waitTurn(tester);
    await settle(tester, 1000);
    await shot(tester, '20_revised');
    await tap(tester, find.text('비교 보기 ›').last, ms: 3000);
    await waitFor(tester, find.text('기존안 · 수정안 비교'));
    await settle(tester, 2000);
    await shot(tester, '21s1_compare_delay');
    await tapNow(tester, find.text('전체 적용').last, ms: 3000);
    await settle(tester, 1500);
    if (_exists(find.byTooltip('닫기'))) await tapNow(tester, find.byTooltip('닫기').last);
    await settle(tester, 1500);
    await shot(tester, '08_after_apply_trip');
    await waitUntil(tester, () => tripOf(tester).state?.moving == true || tripOf(tester).toast != null || s3.finished, '출발', timeout: const Duration(minutes: 3));
    await settle(tester, 1500);
    await shot(tester, '15_moving');
    await closeScene(s3);

    // ---------- 장면 ④: 15:20 storm → safety_raised 선제 → 22 → (재생기 승인) → near_hazard 23 → 24 → 30 ----------
    final s4 = await openScene('scene4_safety', 20);
    await waitFor(tester, find.byKey(const ValueKey('proactive-peek')), timeout: const Duration(minutes: 10), name: 'safety_peek');
    await settle(tester, 800);
    await shot(tester, '14s3_safety_peek');
    await tapNow(tester, find.text('보기 ›'), ms: 2500);
    await waitFor(tester, find.byKey(const ValueKey('hazard-evidence')), timeout: const Duration(seconds: 30));
    await settle(tester, 1500);
    await shot(tester, '22_proactive_safety');
    await tapNow(tester, find.byTooltip('닫기').last);
    await waitFor(tester, find.byKey(const ValueKey('hazard-panel')), timeout: const Duration(minutes: 10), name: 'hazard');
    await settle(tester, 800);
    await shot(tester, '23_hazard');
    await tapNow(tester, find.text('확인'));
    await shot(tester, '23_hazard_collapsed');
    await tapNow(tester, find.byTooltip('알림'), ms: 2500);
    await shot(tester, '24_inbox');
    nav.pop(); // 재생 중 다음 위험 구역 경고가 앱 바를 덮을 수 있다 (최상단)
    await settle(tester, 1000);
    await waitUntil(tester, () => s4.finished, '장면 ④ 재생 끝', timeout: const Duration(minutes: 15));
    await settle(tester, 6000);
    await shot(tester, '14_after_scene4');
    // 여행 종료 → 30 하루 요약
    tripOf(tester).dismissArrival();
    if (tripOf(tester).hazard != null) tripOf(tester).collapseHazard();
    ScaffoldMessenger.of(_shell(tester)).hideCurrentSnackBar(); // 테스트 바인딩에서는 스낵바가 스스로 내려가지 않는다
    await settle(tester, 800);
    await tester.drag(find.byType(DraggableScrollableSheet).first, const Offset(0, -400));
    await settle(tester, 800);
    await tapNow(tester, find.text('여행 종료'));
    await tapNow(tester, find.text('마치기'), ms: 3000);
    await waitFor(tester, find.text('오늘 여행을 마쳤어요'));
    await settle(tester, 1500);
    await shot(tester, '30_trip_end');

    // 정리: 계정 삭제 (planner·agent·trip 데이터도 지운다)
    await session.deleteAccount();
  }, timeout: const Timeout(Duration(minutes: 60)));
}
