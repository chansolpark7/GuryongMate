// 선제 메시지 카드 (19·22): Agent 가 먼저 말을 건 답(선제 턴)은 앞 답변에 이어붙지 않고 별도 카드로.
// 서버 MessageView.trigger 로 알아보므로 새로고침(이번 실행 중 받은 이벤트 없음) 뒤에도 카드다.
// trigger 가 없는 옛 기록은 '사용자 말 없이 시작한 assistant 턴'(첫 턴 제외)을 선제로 본다. LLM 호출 없음.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/models/agent.dart';
import 'package:guryong_mate/features/chat/chat_controller.dart';
import 'package:guryong_mate/features/chat/proposal_card.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';

import 'ai_sheet_test.dart' show PlanState, chatOf, openSheet;
import 'support/fixture_gateway.dart';
import 'support/harness.dart';

const _sid = 'sess-pro';

Map<String, dynamic> _detail(List<Map<String, dynamic>> messages) =>
    {'id': _sid, 'plan_id': 240, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z', 'messages': messages};

Map<String, dynamic> _trigger(String type, {String? reason, int id = 41, String at = '2026-10-10T13:53:00+09:00'}) =>
    {'kind': 'event', 'type': type, 'event_id': id, 'reason': reason, 'at': at};

/// 기록만 있는 세션으로 AI 시트를 연다 (= 앱을 새로 켜거나 새로고침한 상태: 이번 실행 중 받은 이벤트 없음).
Future<FixtureGateway> openWith(WidgetTester tester, List<Map<String, dynamic>> messages) async {
  await phone(tester);
  final gw = FixtureGateway(['f1_plan_with_ai.json']);
  final ps = PlanState(gw, (gw.response('accept') as Map)['plan'] as Map<String, dynamic>,
      session: {'id': _sid, 'plan_id': 240, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z'});
  ps.sessionDetail = _detail(messages);
  final session = await signedInSession(gw);
  await tester.pumpWidget(app(session, const PlannerShell(planId: 240)));
  await settle(tester);
  await openSheet(tester);
  return gw;
}

Finder _inCard(Finder f) => find.descendant(of: find.byKey(const ValueKey('proactive-card')), matching: f);

void main() {
  setUpHarness();

  testWidgets('새로고침 뒤에도 trigger 로 선제 카드: 머리줄(시각·종류 칩) + 답변·제안 카드를 카드 안에 · 일반 답은 그대로', (tester) async {
    final gw = FixtureGateway(['f1_plan_with_ai.json']);
    final withProposal = Map<String, dynamic>.of(((gw.response('session_detail') as Map)['messages'] as List).last as Map<String, dynamic>);
    await openWith(tester, [
      {'id': 1, 'role': 'user', 'content': '일정 괜찮아?'},
      {'id': 2, 'role': 'assistant', 'content': '네, 지금 일정은 무리 없어요.'},
      {...withProposal, 'id': 3, 'content': '과메기문화관 도착이 늦어질 것 같아요. 체류를 줄이는 안을 만들었어요.', 'trigger': _trigger('delay', reason: 'violations')},
    ]);
    expect(chatOf(tester).proactive, isEmpty, reason: '이번 실행 중 받은 이벤트 없음 (새로고침 상태)');

    final card = find.byKey(const ValueKey('proactive-card'));
    await revealUp(tester, card);
    expect(card, findsOneWidget);
    expect(_inCard(find.byKey(const ValueKey('proactive-header'))), findsOneWidget);
    expect(_inCard(find.text('구룡메이트가 먼저 알려드려요 · 13:53', findRichText: true)), findsOneWidget, reason: '이벤트 시각 (trigger.at)');
    expect(_inCard(find.text('일정 지연')), findsOneWidget);
    expect(_inCard(find.textContaining('과메기문화관 도착이 늦어질')), findsOneWidget, reason: '답변이 카드 안');
    expect(_inCard(find.byType(ProposalCard)), findsOneWidget, reason: '제안 카드도 카드 안');
    final prevAnswer = find.textContaining('지금 일정은 무리 없어요');
    await revealUp(tester, prevAnswer);
    expect(prevAnswer, findsOneWidget);
    expect(_inCard(prevAnswer), findsNothing, reason: '앞 턴 답은 카드 밖');

    // 앞 턴과 넉넉히 떨어져 있다 (24px 이상)
    final prev = tester.getBottomLeft(prevAnswer).dy;
    expect(tester.getTopLeft(card).dy - prev, greaterThanOrEqualTo(24));
  });

  testWidgets('종류 칩: 일정 밀림(behind_schedule) · 안전(danger 톤) · 옛 기록(trigger 없음)은 사용자 말 없이 시작한 턴을 선제로', (tester) async {
    await openWith(tester, [
      {'id': 1, 'role': 'assistant', 'content': '첫 안내예요.'}, // 첫 턴: 환영 → 카드 아님
      {'id': 2, 'role': 'user', 'content': '고마워'},
      {'id': 3, 'role': 'assistant', 'content': '천만에요.'},
      {'id': 4, 'role': 'assistant', 'content': '40분 늦어지고 있어요.', 'trigger': _trigger('delay', reason: 'behind_schedule', id: 50)},
      {'id': 5, 'role': 'assistant', 'content': '방파제에 풍랑 주의보가 났어요.', 'trigger': _trigger('safety_raised', id: 51, at: '2026-10-10T15:10:00+09:00')},
      {'id': 6, 'role': 'assistant', 'content': '예전 선제 메시지예요.', 'created_at': '2026-10-10T06:20:00Z'},
      {'id': 7, 'role': 'user', 'content': '알겠어'},
      {'id': 8, 'role': 'assistant', 'content': '네.'},
    ]);
    final chat = chatOf(tester);
    expect([for (final t in chat.turns) chat.proactiveOf(t) != null], [false, false, true, true, true, false]);
    expect(chat.proactiveOf(chat.turns[3])!.type, 'safety_raised');
    expect(chat.proactiveOf(chat.turns[3])!.timeText, '15:10');
    expect(chat.proactiveOf(chat.turns[3])!.event, isNull);
    final legacy = chat.proactiveOf(chat.turns[4])!;
    expect(legacy.type, isNull, reason: 'trigger 없는 옛 기록 → 추정');
    expect(legacy.at, DateTime.utc(2026, 10, 10, 6, 20).toLocal(), reason: '저장 시각(기기 시간대)');

    await revealUp(tester, find.text('일정 밀림'));
    expect(find.text('일정 밀림'), findsOneWidget);
    await revealUp(tester, find.text('안전 경고'));
    expect(find.text('안전 경고'), findsOneWidget, reason: '이벤트 심각도를 모르면 안전 경고 (danger 톤)');
    expect(find.byKey(const ValueKey('proactive-card')), findsWidgets);
    expect(find.text('첫 안내예요.'), findsOneWidget);
    expect(find.ancestor(of: find.text('첫 안내예요.'), matching: find.byKey(const ValueKey('proactive-card'))), findsNothing);
  });

  test('대화 기록 → 턴: trigger 가 있으면 앞 턴이 빈 답으로 끝났어도 새 턴', () {
    final d = AgentSessionDetail.fromJson(_detail([
      {'id': 1, 'role': 'user', 'content': '물어볼게'},
      {'id': 2, 'role': 'assistant', 'content': '', 'steps': [
        {'id': 's1', 'kind': 'search', 'status': 'error', 'title': '검색'}
      ]},
      {'id': 3, 'role': 'assistant', 'content': '늦어지고 있어요.', 'trigger': _trigger('delay')},
    ]));
    final turns = ChatController.turnsFromHistory(d.messages);
    expect(turns, hasLength(2));
    expect(turns.last.trigger?.type, 'delay');
    expect(turns.last.trigger?.eventId, 41);
    expect(turns.first.trigger, isNull);
  });
}

Future<void> revealUp(WidgetTester tester, Finder f) async {
  final sheet = find.descendant(of: find.byType(DraggableScrollableSheet).last, matching: find.byType(Scrollable));
  for (var i = 0; i < 30 && f.evaluate().isEmpty; i++) {
    await tester.drag(sheet.first, const Offset(0, 200));
    await tester.pump(const Duration(milliseconds: 50));
  }
  await reveal(tester, f);
}
