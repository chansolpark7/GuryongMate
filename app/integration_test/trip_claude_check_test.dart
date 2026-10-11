// 실제 LLM(AGENT_PROVIDER=claude) 으로 동행 모드를 짧게 확인한다 (비용이 드니 손으로만 돌린다).
//   장면 ③ 선제 턴(delay) + 조건 대화 1번 ("과메기문화관은 유지해줘"), 장면 ② 장소 질문 1번.
//   flutter test integration_test/trip_claude_check_test.dart -d windows --dart-define=LIVE_API=http://127.0.0.1:8020 \
//     --dart-define=DEMO=true --dart-define=REPO=C:/path/to/GuryongMate
// 재생기는 --speed 0 (기다리지 않음, 선제 턴이 끝날 때까지는 기다린다). 재생 뒤 바로 여행을 끝내 백그라운드 틱이 선제 턴을 더 보내지 않게 한다.
// 화면은 build/screens_l2/claude_*.png, 단계·카드·답변은 로그로.
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
import 'package:guryong_mate/main.dart';
import 'package:integration_test/integration_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

const live = String.fromEnvironment('LIVE_API', defaultValue: 'http://127.0.0.1:8020');
const repo = String.fromEnvironment('REPO', defaultValue: '..');

/// 한 장면만 다시 확인할 때 (비용): --dart-define=ONLY=scene2 / scene3
const only = String.fromEnvironment('ONLY');
final _root = GlobalKey();

Future<void> shot(WidgetTester tester, String name) async {
  await tester.pump(const Duration(milliseconds: 300));
  final image = await (_root.currentContext!.findRenderObject()! as RenderRepaintBoundary).toImage(pixelRatio: 1);
  final png = await image.toByteData(format: ui.ImageByteFormat.png);
  final file = File('build/screens_l2/claude_$name.png');
  await file.parent.create(recursive: true);
  await file.writeAsBytes(png!.buffer.asUint8List());
}

Future<void> settle(WidgetTester tester, [int ms = 800]) async {
  for (var t = 0; t < ms; t += 100) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

Future<void> waitUntil(WidgetTester tester, bool Function() cond, String what, {Duration timeout = const Duration(minutes: 3)}) async {
  final end = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(end)) {
    await tester.pump(const Duration(milliseconds: 200));
    try {
      if (cond()) return;
    } catch (_) {}
  }
  await shot(tester, 'FAIL');
  throw TestFailure('시간 안에 조건을 만족하지 않음: $what');
}

Element _shell(WidgetTester tester) => tester.element(find.byType(IndexedStack, skipOffstage: false).first);
ChatController chatOf(WidgetTester tester) => Provider.of<ChatController>(_shell(tester), listen: false);

Future<void> say(WidgetTester tester, String text) async {
  final field = find.byType(TextField).last;
  await tester.tap(field);
  await settle(tester, 300);
  await tester.enterText(field, text);
  await settle(tester, 300);
  await tester.tap(find.byTooltip('보내기').last);
  await tester.pump();
}

void logTurn(ChatTurn t) {
  debugPrint('[claude] 사용자: ${t.user}');
  debugPrint('[claude] 단계: ${t.steps.map((s) => '${s.title}(${s.kind}) ${s.detail}').join(' → ')}');
  debugPrint('[claude] 답변: ${t.text}');
  for (final p in t.proposals) {
    debugPrint('[claude] 카드 #${p.proposal.id} ${p.state.name}: ${p.proposal.changes.map((c) => c.message).join(' / ')}');
  }
  if (t.error != null) debugPrint('[claude] 오류: ${t.error}');
}

Map<String, String> env(String home) => {'USERPROFILE': home, 'HOME': home, 'PYTHONIOENCODING': 'utf-8', 'GURYONG_API_URL': live};

Future<int> replay(String home, String scene) async {
  final r = await Process.run(
    'python',
    ['$repo/scripts/planner/cli.py', '--url', live, 'trip', 'replay', 'new', scene, '--speed', '0'],
    environment: env(home),
    workingDirectory: repo,
    stdoutEncoding: utf8,
    stderrEncoding: utf8,
  );
  final out = '${r.stdout}'.replaceAll(RegExp(r'\x1B\[[0-9;]*m'), '');
  debugPrint(out);
  return int.parse(RegExp(r'계획 #(\d+)').firstMatch(out)!.group(1)!);
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('실제 Claude: 장면 ③ 선제 턴 + 조건 대화, 장면 ② 장소 질문', (tester) async {
    await tester.binding.setSurfaceSize(const Size(390, 844));
    AppConfig.demo = true;
    final info = await ApiClient(baseUrl: live).get('/agent/info');
    expect('${(info as Map)['provider']}', startsWith('claude'), reason: 'AGENT_PROVIDER=claude 스택에서만');
    final prefs = await SharedPreferences.getInstance();
    await prefs.clear();
    await prefs.setString('base_url', live);
    await prefs.setBool('demo_location_mode', true);
    final store = SecureTokenStore();
    await store.clear();
    final session = await AppSession.load(tokenStore: store);
    final loginId = 'l2cl${DateTime.now().millisecondsSinceEpoch % 100000000}';
    await session.signInWith(await session.api.signup(loginId, 'guryong2026', displayName: '서윤'));
    final home = (await Directory.systemTemp.createTemp('guryong_l2_claude')).path;
    final login = await Process.run(
      'python',
      ['$repo/scripts/planner/cli.py', '--url', live, 'login', loginId, '--password', 'guryong2026'],
      environment: env(home),
      workingDirectory: repo,
    );
    expect(login.exitCode, 0);
    await tester.pumpWidget(
      RepaintBoundary(
        key: _root,
        child: ChangeNotifierProvider.value(value: session, child: const GuryongApp()),
      ),
    );
    await settle(tester, 2000);
    final nav = tester.state<NavigatorState>(find.byType(Navigator).first);

    // ---------- 장면 ③: 재생(선제 턴은 Claude) → 미리보기 → 19 → "과메기문화관은 유지해줘" → 20 ----------
    if (only != 'scene2') {
      final p3 = await replay(home, 'scene3_delay');
      nav.push(MaterialPageRoute<void>(builder: (_) => PlannerShell(planId: p3)));
      await waitUntil(
        tester,
        () => find.byKey(const ValueKey('proactive-peek')).evaluate().isNotEmpty || find.byKey(const ValueKey('fab-suggestion-dot')).evaluate().isNotEmpty,
        '선제 미리보기',
      );
      await settle(tester, 1500);
      await shot(tester, '14s3_delay_peek');
      if (find.text('보기 ›').evaluate().isNotEmpty) {
        await tester.tap(find.text('보기 ›'));
      } else {
        await tester.tap(find.byTooltip('구룡메이트 열기').first);
      }
      await waitUntil(tester, () => find.byKey(const ValueKey('proactive-header')).evaluate().isNotEmpty, '선제 표시 줄');
      await settle(tester, 1500);
      await shot(tester, '19_proactive_delay');
      logTurn(chatOf(tester).turns.lastWhere((t) => t.user.isEmpty));
      await say(tester, '과메기문화관은 유지해줘');
      await waitUntil(tester, () => !chatOf(tester).busy && chatOf(tester).turns.last.done, '조건 대화', timeout: const Duration(minutes: 4));
      await settle(tester, 1500);
      await shot(tester, '20_revised');
      logTurn(chatOf(tester).turns.last);
      await session.api.tripEnd(p3);
      nav.popUntil((r) => r.isFirst);
      await settle(tester, 1000);
    }
    if (only == 'scene3') {
      await session.deleteAccount();
      return;
    }

    // ---------- 장면 ②: 재생 → 가옥거리 도착 → 장소 질문 ----------
    final p2 = await replay(home, 'scene2_arrival');
    nav.push(MaterialPageRoute<void>(builder: (_) => PlannerShell(planId: p2)));
    await waitUntil(tester, () => find.text('동행 중').evaluate().isNotEmpty, '동행 모드');
    await settle(tester, 6000);
    if (find.byKey(const ValueKey('arrival-card')).evaluate().isNotEmpty) {
      await shot(tester, '16_arrival');
      await tester.tap(find.descendant(of: find.byKey(const ValueKey('arrival-card')), matching: find.byTooltip('닫기'))); // 카드가 FAB 위에 있다
      await settle(tester, 600);
    }
    await tester.tap(find.byTooltip('구룡메이트 열기').first);
    await settle(tester, 1500);
    await say(tester, '언제 지어진 집들이에요?');
    await waitUntil(tester, () => !chatOf(tester).busy && chatOf(tester).turns.last.done, '장소 질문', timeout: const Duration(minutes: 4));
    await settle(tester, 1500);
    await shot(tester, '18_place_question');
    logTurn(chatOf(tester).turns.last);
    await session.api.tripEnd(p2);
    nav.popUntil((r) => r.isFirst);
    await settle(tester, 500);
    await session.deleteAccount();
  }, timeout: const Timeout(Duration(minutes: 20)));
}
