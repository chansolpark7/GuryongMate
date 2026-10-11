// 여러 날을 바꾸는 제안 (2026-10-11): 카드 변경 목록은 날짜별 머리, 요약 상자는 날마다 한 줄(위반 있는 날 Red),
// 비교 화면은 바뀐 날만 날짜 탭 — 처음엔 위반 있는 날. 하루짜리는 지금과 같다 (ai_sheet_test·safety_screens_test).
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/models/plan.dart';
import 'package:guryong_mate/core/theme/app_theme.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';

import 'support/fixture_gateway.dart';
import 'support/harness.dart';

Map<String, dynamic> _copy(Object? m) => jsonDecode(jsonEncode(m)) as Map<String, dynamic>;

Map<String, dynamic> _safety(int id) => {
      'plan_id': id, 'plan_version': 1, 'checked_at': '2026-10-09T10:00:00+09:00', 'overall_risk': 'safe',
      'counts': {'safe': 0, 'caution': 0, 'danger': 0, 'unassessed': 0, 'info': 0}, 'items': [], 'data_ok': {},
    };

/// f1 의 하루 일정(240)을 이틀로 늘리고: Day1 2번째(구룡포항) → '새 카페' 교체, Day2 끝에 '구룡포 야시장' 추가 (Day2 에 위반).
({Map<String, dynamic> plan, Map<String, dynamic> proposal}) _twoDays(FixtureGateway gw) {
  final plan = _copy((gw.response('accept') as Map)['plan']);
  final days = plan['days'] as List;
  final d2 = _copy(days[0])
    ..['day'] = 2
    ..['date'] = '2026-10-11'
    ..['weekday'] = '일';
  for (final i in d2['items'] as List) {
    (i as Map)
      ..['id'] = (i['id'] as int) + 1000
      ..['day'] = 2;
  }
  days.add(d2);
  plan['end_date'] = '2026-10-11';

  final after = _copy(plan);
  final a1 = (after['days'] as List)[0]['items'] as List, a2 = (after['days'] as List)[1]['items'] as List;
  final removedId = a1[1]['id'] as int;
  a1[1] = _copy(a1[1])
    ..['id'] = -1
    ..['place_id'] = 'x-cafe'
    ..['memo'] = '바다 보이는 창가'
    ..['place'] = {..._copy(a1[1]['place']), 'id': 'x-cafe', 'title': '새 카페', 'lat': 35.99, 'lng': 129.561};
  a2.add(_copy(a2.last)
    ..['id'] = -2
    ..['order'] = a2.length + 1
    ..['place_id'] = 'x-night'
    ..['start_at'] = '18:30'
    ..['end_at'] = '19:20'
    ..['memo'] = '저녁 먹거리'
    ..['place'] = {..._copy(a2.last['place']), 'id': 'x-night', 'title': '구룡포 야시장', 'lat': 35.987, 'lng': 129.556});
  (after['days'] as List)[1]['end_at'] = '19:20';
  after['violations'] = [
    {'code': 'end_before', 'severity': 'error', 'message': '18:00 전에 끝나지 않아요', 'day': 2, 'item_id': -2},
  ];

  final proposal = _copy(gw.response('proposal_detail'))
    ..['status'] = 'pending'
    ..['decision'] = null
    ..['decided_at'] = null
    ..['base_changed'] = false
    ..['progress_changed'] = []
    ..['ops'] = []
    ..['before'] = plan
    ..['after'] = after
    ..['changes'] = [
      {'kind': 'removed', 'message': "삭제: 1일차 '구룡포항'", 'item_id': removedId, 'day': 1},
      {'kind': 'added', 'message': "추가: 1일차 2번째 '새 카페' (50분)", 'item_id': -1, 'day': 1},
      {'kind': 'added', 'message': "추가: 2일차 6번째 '구룡포 야시장' (50분)", 'item_id': -2, 'day': 2},
      {'kind': 'day_end', 'message': '2일차 종료: 17:38 → 19:20', 'item_id': null, 'day': 2},
    ];
  return (plan: plan, proposal: proposal);
}

Future<void> _open(WidgetTester tester, FixtureGateway gw, Map<String, dynamic> plan, Map<String, dynamic> proposal) async {
  final detail = _copy(gw.response('session_detail'));
  final msgs = detail['messages'] as List;
  (msgs[1] as Map)['cards'] = [
    {'type': 'proposal', 'proposal': proposal},
  ];
  gw.on('GET /planner/plans/{id}', (_) => Res.json(200, plan));
  gw.on('GET /planner/plans/{id}/safety', (_) => Res.json(200, _safety(240)));
  gw.on('POST /planner/plans/{id}/safety/preview', (_) => Res.json(200, _safety(240)));
  gw.on('GET /planner/plans/{id}/safety/alerts', (_) => Res.json(200, []));
  gw.on('POST /routing/route', (_) => Res.error(503, 'routing_unavailable'));
  gw.on('GET /safety/avoid', (_) => Res.json(200, {'zones': []}));
  gw.on('GET /agent/sessions', (_) => Res.json(200, [
        {'id': detail['id'], 'plan_id': 240, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z'},
      ]));
  gw.on('GET /agent/sessions/{sid}', (_) => Res.json(200, detail));
  await phone(tester);
  final session = await signedInSession(gw);
  await tester.pumpWidget(app(session, const PlannerShell(planId: 240)));
  await settle(tester);
  await tester.tap(find.byTooltip('구룡메이트 열기').first);
  await settle(tester, 20);
}

void main() {
  setUpHarness();

  test('changedDays: changes 의 day + 다른 날로 옮긴 항목의 전·후 날', () {
    final gw = FixtureGateway(['f1_plan_with_ai.json']);
    final two = _twoDays(gw);
    expect(ProposalDetail.fromJson(two.proposal).changedDays, [1, 2]);

    // Day1 → Day2 로 옮김: 변경 요약엔 day 1 만 있어도 옮긴 뒤 날(2)까지
    final moved = _copy(two.proposal);
    final after = moved['after'] as Map<String, dynamic>;
    final item = ((after['days'] as List)[0]['items'] as List).removeAt(0) as Map;
    item['day'] = 2;
    ((after['days'] as List)[1]['items'] as List).add(item);
    moved['changes'] = [
      {'kind': 'moved', 'message': '이동: 1일차 → 2일차', 'item_id': item['id'], 'day': 1},
    ];
    final p = ProposalDetail.fromJson(moved);
    expect(p.changedDays, [1, 2]);
    expect(p.multiDay, isTrue);

    final one = ProposalDetail.fromJson(_copy(gw.response('proposal_detail')));
    expect(one.changedDays, [1]);
    expect(one.multiDay, isFalse);
  });

  testWidgets('2일 제안: 카드 Day 머리 2개 · 요약 날마다 한 줄(Day2 위반 Red) → 비교 화면 탭 2개 · 처음 Day2', (tester) async {
    final gw = FixtureGateway(['f1_plan_with_ai.json']);
    final two = _twoDays(gw);
    await _open(tester, gw, two.plan, two.proposal);

    expect(find.text('일정 변경 제안'), findsOneWidget);
    expect(find.byKey(const ValueKey('change-day-header')), findsNWidgets(2));
    expect(find.text('Day 1 · 10/10(토)'), findsOneWidget);
    expect(find.text('Day 2 · 10/11(일)'), findsOneWidget);
    // 날짜 → 시각 순서: Day1 교체가 Day2 추가보다 위
    expect(tester.getTopLeft(find.text('구룡포항').first).dy, lessThan(tester.getTopLeft(find.text('구룡포 야시장').first).dy));

    final r1 = find.byKey(const ValueKey('route-impact-1')), r2 = find.byKey(const ValueKey('route-impact-2'));
    expect(r1, findsOneWidget);
    expect(r2, findsOneWidget);
    expect(find.descendant(of: r1, matching: find.textContaining('위반')), findsNothing);
    expect(find.descendant(of: r1, matching: find.text('17:38 종료')), findsOneWidget);
    expect(find.descendant(of: r2, matching: find.text('위반 1건')), findsOneWidget, reason: '둘째 날 위반이 "✓ 종료"로 가려지지 않는다');
    expect(tester.widget<Text>(find.descendant(of: r2, matching: find.text('Day 2  '))).style?.color, AppColors.danger);

    await tapIn(tester, find.text('비교 보기 ›'), frames: 30);
    expect(find.text('기존안 · 수정안 비교'), findsOneWidget);
    expect(find.byKey(const ValueKey('compare-day-1')), findsOneWidget);
    expect(find.byKey(const ValueKey('compare-day-2')), findsOneWidget);
    expect(find.text('Day 2 변경 1건 · 전체 2건'), findsOneWidget, reason: '처음엔 위반 있는 날');
    expect(find.text('구룡포 야시장'), findsWidgets);
    expect(find.text('0건'), findsOneWidget, reason: '규칙 위반 0 → 1 (Day2 기준)');
    expect(find.text('1건'), findsOneWidget);
    expect(find.text('19:20'), findsOneWidget, reason: 'Day2 종료 17:38 → 19:20');

    await tester.tap(find.byKey(const ValueKey('compare-day-1')));
    await settle(tester, 20);
    expect(find.text('Day 1 변경 1건 · 전체 2건'), findsOneWidget);
    expect(find.text('새 카페'), findsWidgets);
    expect(find.text('구룡포 야시장'), findsNothing);
  });
}
