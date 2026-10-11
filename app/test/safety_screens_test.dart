// 25 Planner 안전 · 26 위험 근거 · 27 날짜 전망 · 28 지도 위험 구역/우회 · 21 비교 — 상태별 위젯 테스트 (f6 장면 ④ 녹화).
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/features/chat/chat_controller.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';
import 'package:provider/provider.dart';

import 'support/extra.dart';
import 'support/fixture_gateway.dart';
import 'support/harness.dart';

/// 장면 ④: 북방파제 위험 직전의 일정(제안 before)과 그 일정의 안전 점검.
({FixtureGateway gw, Map<String, dynamic> plan, Map<String, dynamic> safety}) scene4({Map<String, dynamic> Function(Map<String, dynamic>)? editSafety}) {
  final gw = FixtureGateway(['f6_safety.json']);
  final proposal = gw.response('proposal') as Map<String, dynamic>;
  final plan = proposal['before'] as Map<String, dynamic>;
  var safety = jsonDecode(jsonEncode(gw.response('compare_before_safety'))) as Map<String, dynamic>;
  if (editSafety != null) safety = editSafety(safety);
  gw.on('GET /planner/plans/{id}', (_) => Res.json(200, plan));
  gw.on('GET /planner/plans/{id}/safety', (_) => Res.json(200, safety));
  gw.on('GET /planner/plans/{id}/safety/alerts', (_) => Res.json(200, []));
  gw.on('GET /safety/outlook', (r) => Res.json(200, outlookJson(date: r.query['date']!)));
  gw.on('GET /safety/situation', (_) => Res.json(200, situationJson()));
  gw.on('POST /routing/route', (r) => (r.body as Map)['hazards'] == 'avoid' ? Res.json(200, gw.response('detour_route')) : Res.error(503, 'routing_unavailable'));
  final sid = gw.param('session_with_proactive', 'session_id');
  gw.on('GET /agent/sessions', (_) => Res.json(200, [
        {'id': sid, 'plan_id': plan['id'], 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z'}
      ]));
  return (gw: gw, plan: plan, safety: safety);
}

Future<void> openPlanner(WidgetTester tester, FixtureGateway gw, int planId) async {
  await phone(tester);
  final s = await signedInSession(gw);
  await tester.pumpWidget(app(s, PlannerShell(planId: planId)));
  await settle(tester);
}

Map<String, dynamic> _item(Map<String, dynamic> safety, String kind, int id) =>
    (safety['items'] as List).cast<Map<String, dynamic>>().firstWhere((i) => i['kind'] == kind && i['item_id'] == id);

void main() {
  setUpHarness();

  testWidgets('25: 위험 단계 배지 · 참고 칩 · 다녀옴 · 구간 수단 · 위험 배너', (tester) async {
    final s = scene4();
    await openPlanner(tester, s.gw, s.plan['id'] as int);

    expect(find.textContaining('안전 점검 · 예보 기준'), findsOneWidget);
    expect(find.text('구룡포 북방파제 16:00 위험 · 풍속 16m/s 예보'), findsOneWidget, reason: '배너: 이날 위험 방문 중 가장 이른 것');
    expect(find.text('AI로 조정'), findsOneWidget);
    expect(find.textContaining('다녀옴'), findsNWidgets(2), reason: '가옥거리·구룡포항 done');
    expect(find.text('지금 여기'), findsOneWidget, reason: '과메기문화관 visiting');
    expect(find.text('안전 · 실내'), findsWidgets);
    expect(find.text('참고 · 약한 비'), findsWidgets);
    await tester.ensureVisible(find.text('위험 · 풍속 16m/s').first);
    expect(find.text('위험 · 풍속 16m/s'), findsWidgets, reason: '북방파제 방문 배지 + 가는 길 배지');
    expect(find.text('도보 13분'), findsOneWidget);
    expect(find.text(' · 차량 10분'), findsOneWidget, reason: '다른 수단은 회색으로 같이');
    await tester.ensureVisible(find.text('차량 13분'));
    expect(find.text('위험 · 수위 등급 3'), findsOneWidget, reason: '카페 가는 차량 구간: 침수 감시 구역');
  });

  testWidgets('25: risk=null 은 회색 "판단 불가"/"예보 전" (절대 Green 아님)', (tester) async {
    final s = scene4(editSafety: (j) {
      _item(j, 'visit', 516)
        ..['risk'] = null
        ..['unassessed_reason'] = 'data_missing'
        ..['hazards'] = [];
      _item(j, 'visit', 515)
        ..['risk'] = null
        ..['unassessed_reason'] = 'beyond_forecast'
        ..['recheck_from'] = '2026-10-07'
        ..['hazards'] = [];
      return j;
    });
    await openPlanner(tester, s.gw, s.plan['id'] as int);
    await tester.ensureVisible(find.text('판단 불가 · 데이터 없음'));
    expect(find.text('판단 불가 · 데이터 없음'), findsOneWidget);
    expect(find.text('예보 전 · 10/07부터'), findsOneWidget);
    final chip = tester.widget<Material>(find.ancestor(of: find.text('판단 불가 · 데이터 없음'), matching: find.byType(Material)).first);
    expect(chip.color, const Color(0xFFF3F4F6), reason: '회색');
  });

  testWidgets('25: 503 safety_unavailable → 배지 숨김 + 헤더 한 줄, 일정은 그대로', (tester) async {
    final s = scene4();
    final err = FixtureGateway(['errors.json']);
    s.gw.on('GET /planner/plans/{id}/safety', (_) => Res.json(503, err.response('planner_safety_unavailable')));
    await openPlanner(tester, s.gw, s.plan['id'] as int);
    expect(find.text('안전 정보를 불러오지 못했어요'), findsOneWidget);
    expect(find.textContaining('위험 ·'), findsNothing);
    expect(find.text('구룡포 북방파제'), findsOneWidget);
  });

  testWidgets('25: 새 안전 경고 배너 (needs_action) → 확인 표시', (tester) async {
    final s = scene4();
    final alert = {
      'id': 97, 'plan_id': s.plan['id'], 'created_at': '2026-10-10T06:20:00Z', 'level': 'danger', 'needs_action': true, 'status': 'new',
      'message': '일정 1곳의 위험 단계가 올라갔습니다.', 'plan_version': 5, 'situation': 'x', 'checked_at': '2026-10-10T15:20:00+09:00',
      'changes': [
        {'kind': 'raised', 'from_risk': 'safe', 'to_risk': 'danger', 'item': _item(s.safety, 'visit', 515)},
      ],
    };
    s.gw.on('GET /planner/plans/{id}/safety/alerts', (_) => Res.json(200, [alert]));
    s.gw.on('POST /planner/plans/{id}/safety/alerts/{a}/seen', (_) => Res.json(200, {...alert, 'status': 'seen'}));
    await openPlanner(tester, s.gw, s.plan['id'] as int);
    expect(find.text('구룡포 북방파제 16:00 위험 · 풍속 16m/s 예보'), findsOneWidget);
    expect(s.gw.sent('POST', '/planner/plans/{id}/safety/alerts/97/seen'), hasLength(1));
  });

  testWidgets('26: 배지 탭 → 근거·괜찮은 시간대·가는 길·신뢰도', (tester) async {
    final s = scene4();
    await openPlanner(tester, s.gw, s.plan['id'] as int);
    final badge = find.text('위험 · 풍속 16m/s').last;
    await tester.ensureVisible(badge);
    await tester.tap(badge);
    await settle(tester);
    expect(find.text('근거'), findsOneWidget);
    expect(find.text('10/10 16시 파고 3.5m 예보'), findsOneWidget);
    expect(find.text('방파제'), findsWidgets);
    expect(find.text('실측·24시간 안 예보'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('AI에게 대안 묻기'), 200, scrollable: find.byType(Scrollable).last);
    expect(find.text('괜찮은 시간대'), findsOneWidget);
    expect(find.text('10/12 08:00–18:40'), findsOneWidget);
    expect(find.textContaining('가는 길 · 구룡포과메기문화관 → 구룡포 북방파제'), findsOneWidget);
    expect(find.text('AI에게 대안 묻기'), findsOneWidget);
    expect(find.textContaining('제시하라'), findsNothing, reason: 'advice(LLM 지시문)는 보이지 않는다');
  });

  testWidgets('27: 시간 띠 (장소 종류 × 09~18시) · 괜찮은 시간 · 만조', (tester) async {
    final s = scene4();
    await openPlanner(tester, s.gw, s.plan['id'] as int);
    await tester.tap(find.textContaining('안전 점검 · 예보 기준'));
    await settle(tester);
    expect(find.text('10/10 (토) 구룡포 전망'), findsOneWidget);
    Color cell(String k, int h) => (tester.widget<Container>(find.byKey(ValueKey('cell-$k-$h'))).decoration! as BoxDecoration).color!;
    expect(cell('breakwater', 10), const Color(0xFFCDEFD9));
    expect(cell('breakwater', 14), const Color(0xFFFCD9A3));
    expect(cell('breakwater', 17), const Color(0xFFF6A6A9));
    expect(cell('outdoor', 15), const Color(0xFFE5E7EB), reason: 'safe + 참고 → 회색');
    expect(find.textContaining('방파제 14시부터 주의, 16시부터 위험'), findsOneWidget);
    expect(find.text('방파제 09:00–14:00'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('발효 중인 특보 없음'), 200, scrollable: find.byType(Scrollable).last);
    expect(find.text('만조 05:41 · 17:52'), findsOneWidget);
    expect(find.text('발효 중인 특보 없음'), findsOneWidget);
  });

  testWidgets('27: 먼 날짜 → 시간 띠 대신 "아직 예보 전" (괜찮다고 말하지 않음)', (tester) async {
    final s = scene4();
    s.gw.on('GET /safety/outlook', (r) => Res.json(200, outlookJson(date: r.query['date']!, far: true)));
    s.gw.on('GET /safety/situation', (_) => Res.json(200, situationJson(date: '2026-10-01')));
    await openPlanner(tester, s.gw, s.plan['id'] as int);
    await tester.tap(find.textContaining('안전 점검 · 예보 기준'));
    await settle(tester);
    expect(find.text('아직 예보 전이에요 · 10/27부터 확인할 수 있어요'), findsOneWidget);
    expect(find.byKey(const ValueKey('cell-breakwater-10')), findsNothing);
    expect(find.textContaining('주의·위험 신호가 없어요'), findsNothing);
  });

  testWidgets('28 · 28-S1: 위험 구역 레이어 · 범례 · 우회 카드 · 혼잡도 비활성', (tester) async {
    final s = scene4();
    await openPlanner(tester, s.gw, s.plan['id'] as int);
    await tester.tap(find.text('Map'));
    await settle(tester, 30);
    expect(s.gw.sent('GET', '/safety/avoid'), hasLength(1));
    expect(find.textContaining('지금 기준'), findsOneWidget);
    expect(find.text('구룡포 북방파제 · 위험'), findsOneWidget);
    expect(find.text('위험 구역을 피해 가는 길'), findsOneWidget);
    expect(find.text('구룡포 북방파제 → 카페파도 · 차량'), findsOneWidget);
    expect(find.textContaining('(+50분)'), findsOneWidget);
    expect(find.text('구룡포수협_스마트맨홀 주변을(를) 지나지 않아요'), findsOneWidget);
    final avoid = s.gw.sent('POST', '/routing/route').where((r) => (r.body as Map)['hazards'] == 'avoid').first.body as Map;
    expect(avoid['mode'], 'car');

    await tester.tap(find.byTooltip('지도 레이어'));
    await settle(tester);
    expect(find.text('위험 구역'), findsOneWidget);
    expect(find.text('데이터 없음'), findsOneWidget);
    expect(find.text('날씨'), findsNothing);
  });

  testWidgets('21: 비교 보기 — 지표(위험한 곳 1→0) · 두 열 타임라인 · 전체 적용', (tester) async {
    final s = scene4();
    final sid = s.gw.param('session_with_proactive', 'session_id');
    s.gw.on('GET /agent/sessions/{sid}', (_) => Res.json(200, s.gw.response('session_with_proactive')));
    final accepted = s.gw.response('accept_proactive') as Map<String, dynamic>;
    await openPlanner(tester, s.gw, s.plan['id'] as int);
    await tester.tap(find.byTooltip('구룡메이트 열기').first);
    await settle(tester, 20);
    expect(find.text('일정 변경 제안'), findsOneWidget);
    await tapIn(tester, find.text('비교 보기 ›'), frames: 30);

    expect(find.text('기존안 · 수정안 비교'), findsOneWidget);
    expect(find.text('구룡메이트 제안 · 변경 1건'), findsOneWidget);
    expect(find.text('위험한 곳'), findsOneWidget);
    expect(find.text('1곳'), findsOneWidget);
    expect(find.text('0곳'), findsOneWidget);
    expect(find.text('17:22'), findsOneWidget, reason: '종료 17:30 → 17:22');
    expect(find.text('기존안 · 지금 일정'), findsOneWidget);
    expect(find.text('구룡포 근대문화역사관'), findsWidgets);
    expect(find.text('다녀온 곳 2곳'), findsOneWidget);
    expect(s.gw.sent('POST', '/planner/plans/{id}/safety/preview').single.body, {'ops': (s.gw.response('proposal') as Map)['ops']});

    s.gw.on('GET /planner/plans/{id}', (_) => Res.json(200, accepted['plan']));
    await tester.tap(find.text('전체 적용'));
    await settle(tester, 30);
    expect(s.gw.sent('POST', '/agent/sessions/$sid/proposals/{p}/accept'), hasLength(1));
    expect(find.text('기존안 · 수정안 비교'), findsNothing, reason: '승인하면 비교 화면을 닫는다');
    final chat = Provider.of<ChatController>(tester.element(find.text('Planner')), listen: false);
    expect((chat.turns.last.cards.single as ProposalEntry).state, ProposalState.applied);
  });
}
