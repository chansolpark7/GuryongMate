// 실제 LLM(AGENT_PROVIDER=claude) 으로 계획 모드 대화 몇 턴만 확인한다 (비용이 드니 손으로만 돌린다).
//   flutter test integration_test/claude_check_test.dart -d windows --dart-define=LIVE_API=http://127.0.0.1:8018
// 화면은 build/screens/claude_*.png. 단계·카드·오류를 로그로 남기고, 만든 계정은 끝에 탈퇴로 지운다.
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
import 'package:guryong_mate/core/theme/app_theme.dart';
import 'package:integration_test/integration_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

const live = String.fromEnvironment('LIVE_API', defaultValue: 'http://127.0.0.1:8018');
final _root = GlobalKey();

Future<void> shot(WidgetTester tester, String name) async {
  await tester.pump(const Duration(milliseconds: 300));
  final image = await (_root.currentContext!.findRenderObject()! as RenderRepaintBoundary).toImage(pixelRatio: 1);
  final png = await image.toByteData(format: ui.ImageByteFormat.png);
  final file = File('build/screens/claude_$name.png');
  await file.parent.create(recursive: true);
  await file.writeAsBytes(png!.buffer.asUint8List());
}

Future<void> settle(WidgetTester tester, [int ms = 800]) async {
  for (var t = 0; t < ms; t += 100) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Claude: 계획 모드 대화 3턴 (일정 짜기 → 승인 → 조건 바꾸기 → 카페 추천)', (tester) async {
    await tester.binding.setSurfaceSize(const Size(390, 844));
    final prefs = await SharedPreferences.getInstance();
    await prefs.clear();
    await prefs.setString('base_url', live);
    await ApiClient(baseUrl: live).put('/safety/sim', {'scenario': 'calm', 'now': '2026-10-09T10:00:00+09:00'});
    final store = MemoryTokenStore();
    final session = await AppSession.load(tokenStore: store);
    await session.signInWith(await session.api.signup('l1claude${DateTime.now().millisecondsSinceEpoch % 100000000}', 'guryong2026', displayName: '서윤'));
    final plan = await session.api.createPlan({
      'title': '엄마랑 구룡포', 'start_date': '2026-10-10', 'end_date': '2026-10-10', 'day_start': '12:30', 'day_end': '18:00', 'travel_mode': 'walk',
    });
    debugPrint('provider: ${(await session.api.agentInfo())['provider']}');

    await tester.pumpWidget(RepaintBoundary(
      key: _root,
      child: ChangeNotifierProvider.value(value: session, child: MaterialApp(debugShowCheckedModeBanner: false, theme: buildTheme(), home: PlannerShell(planId: plan.id))),
    ));
    await settle(tester, 2000);
    ChatController chat() => Provider.of<ChatController>(tester.element(find.text('Planner')), listen: false);

    Future<void> turn(String text, String name) async {
      await tester.tap(find.byTooltip('구룡메이트 열기').first);
      await settle(tester, 1200);
      final field = find.byType(TextField).last;
      await tester.tap(field);
      await tester.enterText(field, text);
      await settle(tester, 300);
      await tester.tap(find.byTooltip('보내기'));
      final end = DateTime.now().add(const Duration(minutes: 4));
      var shotMid = false;
      while (DateTime.now().isBefore(end)) {
        await tester.pump(const Duration(milliseconds: 200));
        if (!shotMid && chat().turns.isNotEmpty && chat().turns.last.steps.length >= 2) {
          shotMid = true;
          await shot(tester, '${name}_streaming');
        }
        if (chat().turns.isNotEmpty && chat().turns.last.done) break;
      }
      final t = chat().turns.last;
      debugPrint('[$name] steps=${t.steps.map((s) => '${s.kind}:${s.title}=${s.status}(${s.detail})').join(' | ')}');
      debugPrint('[$name] summary=${t.stepsSummary} cards=${t.cards.map((c) => c.runtimeType).toList()} error=${t.errorCode}/${t.error}');
      debugPrint('[$name] text=${t.text}');
      expect(t.done, isTrue);
      await settle(tester, 800);
      await shot(tester, '${name}_done');
    }

    await turn('엄마랑 구룡포 일정 짜줘. 어머니가 오래 걷기 힘드셔.', '1_make_plan');
    final card = chat().turns.last.proposals.lastOrNull;
    if (card != null) {
      await chat().accept(card);
      await settle(tester, 2500);
      debugPrint('승인: ${card.state} 일정 ${chat().plan.plan!.allItems.map((i) => '${i.startAt} ${i.title}').join(', ')}');
      await shot(tester, '1_after_apply');
    }
    await turn('카페는 바다가 보이는 곳으로 바꿔줘', '2_change');
    await turn('Day 1 날씨와 안전 확인해줘', '3_safety');

    await session.api.deleteMe();
    await ApiClient(baseUrl: live).put('/safety/sim', {'reset_clock': true});
  });
}
