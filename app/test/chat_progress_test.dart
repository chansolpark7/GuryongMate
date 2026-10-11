// 대화 진행 표시 (lib/features/chat/ai_sheet.dart ActivityCard, 2026-10-11): 단계 사이 LLM 이 생각하는 동안에도
// 무언가 움직이고(✨ 펄스 · "다음 단계를 생각하고 있어요…"), 머리 오른쪽에 경과 시간, 끝나면 접힌 요약에 걸린 시간.
import 'package:clock/clock.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/models/agent.dart';
import 'package:guryong_mate/core/theme/app_theme.dart';
import 'package:guryong_mate/features/chat/ai_sheet.dart';
import 'package:guryong_mate/features/chat/chat_controller.dart';

import 'support/harness.dart';

Future<void> _pump(WidgetTester tester, Widget child, {bool disableAnimations = false}) => tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: MediaQuery(
        data: MediaQueryData(disableAnimations: disableAnimations),
        child: Scaffold(body: Padding(padding: const EdgeInsets.all(16), child: child)),
      ),
    ));

AgentStep _step(String id, String status) => AgentStep(id: id, kind: 'review', status: status, title: '일정 검토', detail: '');

ChatTurn _turn() => ChatTurn('일정 바꿔줘')
  ..startedAt = clock.now()
  ..status = '일정을 검토하고 있어요';

final _thinking = find.byKey(const ValueKey('activity-thinking'));

String _elapsed(WidgetTester tester) => tester.widget<Text>(find.byKey(const ValueKey('activity-elapsed'))).data!;

void main() {
  setUpHarness();

  test('경과 시간 글자: 초 → 분 초', () {
    expect(formatElapsed(const Duration(seconds: 0)), '0초');
    expect(formatElapsed(const Duration(seconds: 59)), '59초');
    expect(formatElapsed(const Duration(seconds: 65)), '1분 5초');
    expect(formatElapsed(const Duration(seconds: 120)), '2분');
  });

  testWidgets('단계가 없을 때(첫 응답 전) · running 이 없을 때 "생각 중" 줄, running 이 있으면 없음', (tester) async {
    final turn = _turn();
    await _pump(tester, ActivityCard(turn: turn));
    expect(_thinking, findsOneWidget);
    expect(find.text('생각하고 있어요…'), findsOneWidget);

    turn.upsertStep(_step('s1', 'running'));
    await _pump(tester, ActivityCard(turn: turn));
    expect(_thinking, findsNothing, reason: '단계가 시작되면 사라진다');

    turn.upsertStep(_step('s1', 'done'));
    await _pump(tester, ActivityCard(turn: turn));
    expect(find.text('다음 단계를 생각하고 있어요…'), findsOneWidget, reason: '모든 단계가 ✓ 이고 턴이 안 끝났으면 다음 행동을 생각 중');
    expect(tester.hasRunningAnimations, isTrue, reason: '✨ 펄스·스피너가 움직인다');
  });

  testWidgets('답 글자가 스트리밍되기 시작하면 "생각 중" 줄은 숨긴다', (tester) async {
    final turn = _turn()..upsertStep(_step('s1', 'done'));
    await _pump(tester, ActivityCard(turn: turn));
    expect(_thinking, findsOneWidget);
    turn.text.write('검토해 보니');
    await _pump(tester, ActivityCard(turn: turn));
    expect(_thinking, findsNothing);
  });

  testWidgets('경과 시간은 1초마다 늘고, 턴이 끝나면 멈춘다 · 접힌 요약엔 걸린 시간 한 번', (tester) async {
    final turn = _turn()..upsertStep(_step('s1', 'done'));
    await _pump(tester, ActivityCard(turn: turn));
    expect(_elapsed(tester), '0초');
    await tester.pump(const Duration(seconds: 3));
    expect(_elapsed(tester), '3초');
    await tester.pump(const Duration(seconds: 62));
    expect(_elapsed(tester), '1분 5초');

    turn
      ..done = true
      ..endedAt = clock.now();
    await _pump(tester, ActivityCard(turn: turn));
    expect(_thinking, findsNothing);
    await tester.pump(const Duration(seconds: 5));
    expect(_elapsed(tester), '1분 5초', reason: '끝난 턴은 시간이 멈춘다');
    expect(tester.hasRunningAnimations, isFalse);

    turn.stepsSummary = '1단계 완료';
    await _pump(tester, ActivitySummary(turn: turn));
    expect(find.text('1단계 완료 · 1분 5초'), findsOneWidget);
  });

  testWidgets('이전 기록에서 불러온 턴은 시간을 모른다 → 요약에 시간 없음', (tester) async {
    final turn = ChatTurn('예전 말', fromHistory: true)
      ..done = true
      ..upsertStep(_step('s1', 'done'))
      ..stepsSummary = '1단계 완료';
    await _pump(tester, ActivitySummary(turn: turn));
    expect(find.text('1단계 완료'), findsOneWidget);
  });

  testWidgets('애니메이션 줄이기 설정이면 ✨ 는 가만히 (스피너만)', (tester) async {
    final turn = _turn()..upsertStep(_step('s1', 'running'));
    await _pump(tester, ActivityCard(turn: turn), disableAnimations: true);
    final opacity = tester.widget<Opacity>(find.descendant(of: find.byKey(const ValueKey('activity-sparkle')), matching: find.byType(Opacity)));
    expect(opacity.opacity, 1);
    await tester.pump(const Duration(milliseconds: 700));
    expect(tester.widget<Opacity>(find.descendant(of: find.byKey(const ValueKey('activity-sparkle')), matching: find.byType(Opacity))).opacity, 1);
  });
}
