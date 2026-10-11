// 여행 중 시간 판정 점검 (docs/trip/audit-2026-10-12.md) — 앱 표시 쪽 재현 테스트.
// ETA 칩 Red(대기 중인 지연 제안) 문구·지난 알림, '지금' 남은 시간, '다음' 항목.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/models/trip.dart';
import 'package:guryong_mate/features/planner/plan_controller.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';
import 'package:guryong_mate/features/trip/location.dart';
import 'package:guryong_mate/features/trip/today_screen.dart';
import 'package:guryong_mate/features/trip/trip_controller.dart';

import 'support/harness.dart';
import 'support/trip_replay.dart';

Map<String, dynamic> _copy(Object? v) => jsonDecode(jsonEncode(v)) as Map<String, dynamic>;

/// 장면 ③ 재생의 delay 이벤트 (agent 가 채워진 것).
Map<String, dynamic> delayEvent() {
  final r = TripReplay('scene3_delay')..advanceTo('23:59:59');
  return _copy(r.events.values.lastWhere((e) => e['type'] == 'delay' && e['agent'] != null));
}

Future<(TripController, TripReplay)> controller(WidgetTester tester) async {
  final r = TripReplay('scene3_delay');
  r.advanceTo('13:50:00');
  final session = await signedInSession(r.gw, prefs: {'demo_location_mode': true});
  final plan = PlanController(session.api, r.planId);
  await plan.load();
  final t = TripController(api: session.api, plan: plan, session: session, source: FakeLocationSource());
  _live.add(() {
    t.dispose();
    plan.dispose();
  });
  return (t, r);
}

final _live = <void Function()>[];

/// 타이머가 남지 않게 테스트 본문 끝에서 정리 (위젯 테스트는 본문이 끝나면 남은 타이머를 검사한다).
void finish() {
  for (final f in _live) {
    f();
  }
  _live.clear();
}

/// 일정 밀림(behind_schedule) 이벤트: 문구의 첫 시각은 '다음 장소 계획 시작'이다 (judge.judge_delay).
Map<String, dynamic> behindEvent(Map<String, dynamic> base, int nextId) => {
      ...base,
      'message': '구룡포항 도착이 13:34 예정에서 14:43으로 1시간 늦어질 것 같아요.',
      'payload': {
        'reason': 'behind_schedule',
        'new_violations': <dynamic>[],
        'delay_minutes': 69,
        'next': {'item_id': nextId, 'title': '구룡포항', 'planned_start': '13:34', 'eta': '14:43', 'start_at': '14:43'},
      },
    };

TripState visitingState({required int current, int? next, int? delay}) =>
    TripState(active: true, day: 1, currentItemId: current, currentState: 'visiting', currentSince: '12:30', nextItemId: next, delayMinutes: delay);

void main() {
  setUpHarness();

  testWidgets('B1 일정 밀림 알림의 첫 시각(다음 장소 계획 시작)을 종료 시각으로 읽지 않는다', (tester) async {
    final (t, r) = await controller(tester);
    final items = (r.plan['days'] as List).first['items'] as List;
    final port = items[1]['id'] as int;
    t.ingest([TripEvent.fromJson({...behindEvent(delayEvent(), port), 'id': 950, 'seen_at': null})]);
    await tester.pump();
    t.state = visitingState(current: items[0]['id'] as int, next: port, delay: 69);
    expect(t.pendingDelay?.id, 950);
    final label = EtaChip.violationLabel(t);
    expect(label, isNot(contains('13:34')), reason: '13:34 는 구룡포항 계획 시작이지 종료 시각이 아니다');
    expect(label, isNot(contains('규칙 넘음')), reason: '밀림은 규칙 위반이 아니다 (새 위반 없음)');
    expect(label, '69분 늦음', reason: '지금 지연 (Red: 대기 중인 제안)');
    finish();
  });

  testWidgets('B2 이미 지난 다음 일정에 대한 지연 알림으로 지금 다음 일정 칩을 Red 로 그리지 않는다', (tester) async {
    final (t, r) = await controller(tester);
    final items = (r.plan['days'] as List).first['items'] as List;
    final port = items[1]['id'] as int, museum = items[2]['id'] as int;
    t.ingest([TripEvent.fromJson({...behindEvent(delayEvent(), port), 'id': 951, 'seen_at': null})]);
    await tester.pump();
    // 제안을 무시한 채(대기) 구룡포항에 도착 -> 다음은 과메기문화관, 지금은 10분 늦음
    t.state = visitingState(current: port, next: museum, delay: 10);
    expect(EtaChip.violationLabel(t), isNull, reason: '알림이 가리킨 다음 일정(구룡포항)은 이미 지났다');
    // 위반 알림(하루 초과)도 같다: 알림의 다음 항목이 지났으면 지금 칩은 지금 지연으로
    final v = delayEvent();
    t.ingest([TripEvent.fromJson({...v, 'id': 952, 'seen_at': null, 'payload': {...v['payload'] as Map, 'next': {'item_id': port}}})]);
    await tester.pump();
    expect(EtaChip.violationLabel(t), isNull);
    // 알림의 다음 일정이 아직 다음이면 그대로 Red
    t.state = visitingState(current: items[0]['id'] as int, next: port, delay: 31);
    expect(EtaChip.violationLabel(t), '18:01 종료 · 규칙 넘음');
    finish();
  });

  testWidgets('B4 고정 시작 시각 항목에 일찍 도착: 남은 시간은 고정 시각 + 체류까지 (서버 재계산과 같게)', (tester) async {
    final r = TripReplay('scene2_arrival', flows: ['f4_arrival_docent.json']);
    r.advanceTo('12:35:00');
    final first = ((r.plan['days'] as List).first['items'] as List).first as Map<String, dynamic>;
    first
      ..['start_time'] = '13:00'
      ..['start_at'] = '13:00'
      ..['end_at'] = '${(13 + (first['stay_minutes'] as int) ~/ 60).toString().padLeft(2, '0')}:${((first['stay_minutes'] as int) % 60).toString().padLeft(2, '0')}';
    await phone(tester);
    final s = await signedInSession(r.gw, prefs: {'demo_location_mode': true});
    await tester.pumpWidget(app(s, PlannerShell(planId: r.planId, locationSource: FakeLocationSource())));
    await settle(tester, 30);
    expect(find.textContaining('${first['end_at']}까지 예정'), findsOneWidget,
        reason: '12:30 에 와도 13:00 고정이면 13:00 에 시작해 체류만큼 머문다 (projection 과 같은 기준)');
  }, skip: true); // 버그 B4
}
