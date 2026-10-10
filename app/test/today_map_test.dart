// Today · Map 역할 나누기 (docs/app/today-map-redesign.md): 남은 일정은 처음부터 · 길 안내 자동+토글 · 지도 핀 작은 창 ·
// Map 끄는 시트(3단)·장소 상세 · 동행 레이어. 여행 재생 fixture(trip/scene2·4)와 f3 로 그린다 (LLM 호출 없음).
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/widgets/common.dart';
import 'package:guryong_mate/features/planner/plan_controller.dart';
import 'package:guryong_mate/features/trip/trip_controller.dart';
import 'package:provider/provider.dart';

import 'support/fixture_gateway.dart';
import 'support/harness.dart';
import 'support/trip_replay.dart';
import 'trip_screens_test.dart' show openShell, poll;

Element _shell(WidgetTester tester) => tester.element(find.byType(IndexedStack, skipOffstage: false).first);
TripController tripOf(WidgetTester tester) => Provider.of<TripController>(_shell(tester), listen: false);
PlanController planOf(WidgetTester tester) => Provider.of<PlanController>(_shell(tester), listen: false);

/// 길 찾기 대역: 정류장 사이 직선 + 가운데 점, steps 요청이면 회전 안내.
Res fakeRoute(Req q) {
  final b = q.body as Map;
  final st = (b['stops'] as List).cast<Map>();
  final (lat0, lng0, lat1, lng1) = (st[0]['lat'] as num, st[0]['lng'] as num, st.last['lat'] as num, st.last['lng'] as num);
  return Res.json(200, {
    'mode': b['mode'] ?? 'walk', 'source': 'road', 'minutes': 8, 'distance_m': 600,
    'geometry': {'type': 'LineString', 'coordinates': [[lng0, lat0], [(lng0 + lng1) / 2, (lat0 + lat1) / 2], [lng1, lat1]]},
    if (b['steps'] == true)
      'legs': [{'steps': [
        {'type': 'start', 'instruction': '출발', 'distance_m': 0, 'begin_index': 0},
        {'type': 'left', 'instruction': '왼쪽으로', 'distance_m': 300, 'begin_index': 1},
        {'type': 'arrive', 'instruction': '도착', 'distance_m': 0, 'begin_index': 2},
      ]}],
  });
}

const placeJson = {
  'id': 'kto-2610501', 'title': '구룡포항', 'content_type': '관광지', 'category_names': ['자연', '항구'], 'tags': [],
  'lat': 35.9894, 'lng': 129.5574, 'addr': '경상북도 포항시 남구 구룡포읍 구룡포리', 'area': '구룡포읍',
  'open_hours': '상시', 'rest_date': '연중무휴', 'fee': '무료', 'parking': '가능', 'tel': '054-270-5000',
  'overview': '동해안 최대 어항 중 하나.', 'images': [], 'image_url': '', 'story_count': 0,
};

TripReplay scene(String name, {List<String> flows = const []}) {
  final r = TripReplay(name, flows: flows);
  r.gw.on('POST /routing/route', fakeRoute);
  r.gw.on('GET /places/{id}', (_) => Res.json(200, placeJson));
  return r;
}

final guideCard = find.byKey(const ValueKey('guide-card'));

void main() {
  setUpHarness();

  testWidgets('f3 · 여행 시작 직후(첫 도착 전)에도 남은 일정 행이 보인다 · 한 줄 요약은 머리에 · 큰 [길 안내] 없음', (tester) async {
    final gw = FixtureGateway(['f3_trip_start.json']);
    final r = TripReplay('scene2_arrival'); // 계획 모양만 빌림
    final plan = {...r.plan, 'id': int.parse(gw.param('start', 'plan_id'))};
    gw.on('GET /planner/plans/{id}', (_) => Res.json(200, plan));
    gw.on('GET /planner/plans/{id}/safety', (_) => Res.error(503, 'safety_unavailable'));
    gw.on('GET /planner/plans/{id}/safety/alerts', (_) => Res.json(200, []));
    gw.on('GET /safety/avoid', (_) => Res.json(200, {'zones': []}));
    gw.on('POST /routing/route', (_) => Res.error(503, 'routing_unavailable'));
    gw.on('GET /docent/plans/{id}/notes', (_) => Res.json(200, []));
    gw.on('GET /agent/sessions', (_) => Res.json(200, [
          {'id': (gw.ex('start')['body'] as Map)['session_id'], 'plan_id': plan['id'], 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z'}
        ]));
    gw.on('GET /agent/sessions/{sid}', (_) => Res.json(200, {'id': 's', 'plan_id': plan['id'], 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z', 'messages': []}));
    gw.on('GET /planner/plans/{id}/proposals', (_) => Res.json(200, []));
    gw.on('GET /docent/memory', (_) => Res.json(200, {'items': [], 'memory_version': 1, 'style_length': null}));
    var state = gw.response('state_before') as Map<String, dynamic>;
    gw.on('GET /trip/plans/{id}/state', (_) => Res.json(200, state));
    gw.on('POST /trip/plans/{id}/start', (_) {
      state = gw.response('start') as Map<String, dynamic>;
      return Res.json(200, state);
    });
    await openShell(tester, gw, plan['id'] as int, startTrip: true);
    await tapText(tester, '여행 시작', frames: 30);
    expect(find.text('Today'), findsOneWidget);
    expect(find.textContaining('첫 일정까지'), findsOneWidget);
    expect(find.textContaining('오늘 일정 5곳'), findsOneWidget, reason: '한 줄 요약은 목록 머리에 그대로');
    for (final id in [502, 503, 504, 505, 506]) {
      expect(find.byKey(ValueKey('remaining-$id'), skipOffstage: false), findsOneWidget, reason: '14-S2 에서도 일정 행 (redesign §2.3)');
    }
    expect(find.text('길 안내'), findsNothing, reason: '큰 [길 안내] 버튼 제거');
    expect(find.text('가이드 미리 보기'), findsOneWidget);
    expect(gw.unmatched, isEmpty);
  });

  testWidgets('15 길 안내: 이동 중 자동 → [×] 이번 구간만 숨김 → 도착하면 꺼짐 → 🧭 수동 → 다음 이동에서 다시 자동', (tester) async {
    final r = scene('scene4_safety', flows: ['f6_safety.json']);
    r.advanceTo('13:35:24'); // 512 → 513 이동 중
    await openShell(tester, r.gw, r.planId);
    await poll(tester);
    tripOf(tester).dismissArrival();
    await settle(tester);
    expect(guideCard, findsOneWidget, reason: '이동 중 자동 켜짐');
    expect(find.text('왼쪽으로'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('guide-close')));
    await settle(tester);
    expect(guideCard, findsNothing, reason: '[×] 숨김');
    await poll(tester);
    expect(guideCard, findsNothing, reason: '같은 구간 동안은 계속 숨김');

    r.advanceTo('13:40:00'); // 513 도착 (visiting)
    await poll(tester);
    expect(tripOf(tester).state!.visiting, isTrue);
    expect(guideCard, findsNothing, reason: '도착하면 꺼짐');

    await tester.tap(find.byKey(const ValueKey('next-guide')));
    await settle(tester, 20);
    expect(guideCard, findsOneWidget, reason: '머무는 중 🧭 로 미리 안내');
    expect(find.textContaining('구룡포과메기문화관까지'), findsOneWidget);
    expect(tripOf(tester).guideTo, 514);

    r.advanceTo('14:33:00'); // 513 출발 → 514 로 이동 중
    await poll(tester);
    expect(tripOf(tester).guideTo, isNull, reason: '상태가 바뀌면 손으로 고른 안내는 지운다');
    expect(guideCard, findsOneWidget, reason: '다음 구간에서 다시 자동');
    expect(planOf(tester).tab, 2);
  });

  testWidgets('14 핀 → 작은 창 (탭 그대로) · [설명 듣기] → 17 가이드 노트 · 남은 일정 행도 작은 창 · [자세히] → Map 시트 펼침', (tester) async {
    final r = scene('scene2_arrival', flows: ['f4_arrival_docent.json']);
    r.advanceTo('12:30:30');
    await openShell(tester, r.gw, r.planId);
    r.advanceTo('12:33:00');
    await poll(tester);
    tripOf(tester).dismissArrival();
    await settle(tester);
    expect(find.text('이야기 듣기'), findsOneWidget);
    expect(find.text('길 안내'), findsNothing, reason: '지금 패널의 큰 [길 안내] 제거');

    await tester.tap(find.byKey(const ValueKey('today-pin-502')));
    await settle(tester);
    final popup = find.byKey(const ValueKey('today-place-popup'));
    expect(popup, findsOneWidget);
    expect(find.descendant(of: popup, matching: find.text('구룡포 일본인 가옥거리')), findsOneWidget);
    expect(find.descendant(of: popup, matching: find.textContaining('머무는 중', findRichText: true)), findsOneWidget);
    expect(planOf(tester).tab, 2, reason: '탭 이동 없음');
    expect(planOf(tester).selectedItemId, isNull, reason: 'Planner 강조를 건드리지 않는다');

    await tester.tap(find.descendant(of: popup, matching: find.text('설명 듣기')));
    await settle(tester, 20);
    expect(find.text('현장 가이드'), findsOneWidget, reason: '17 가이드 노트');
    expect(find.textContaining('일제강점기 구룡포에 정착한'), findsOneWidget);
    tester.state<NavigatorState>(find.byType(Navigator).first).pop();
    await settle(tester, 10);

    // 남은 일정 행 → 지도 그 핀 + 작은 창 (Map 으로 가지 않음)
    final row = find.byKey(const ValueKey('remaining-504'));
    await tester.drag(find.byType(DraggableScrollableSheet), const Offset(0, -300));
    await settle(tester);
    await tester.tap(row);
    await settle(tester);
    expect(find.descendant(of: popup, matching: find.text('구룡포과메기문화관')), findsOneWidget);
    expect(planOf(tester).tab, 2);

    // [자세히] → 유일하게 탭이 바뀌는 곳: Map 시트를 펼쳐 장소 상세
    await tester.tap(find.descendant(of: popup, matching: find.text('자세히 ›')));
    await settle(tester, 20);
    expect(planOf(tester).tab, 1);
    expect(planOf(tester).selectedItemId, 504);
    expect(find.text('경상북도 포항시 남구 구룡포읍 구룡포리'), findsOneWidget, reason: '펼친 시트에 장소 상세');
    expect(find.byKey(const ValueKey('map-listen')), findsOneWidget, reason: '동행 중 오늘 장소 → 상세 위 [설명 듣기]');
  });

  testWidgets('07 Map 시트: 끌어서 접힘 · 반 · 펼침 3단 · 장소를 고르면 펼침에 장소 상세 · 동행 중 내 위치 · 위험 구역은 Today 와 같은 데이터', (tester) async {
    final r = scene('scene2_arrival', flows: ['f4_arrival_docent.json']);
    r.advanceTo('12:30:30');
    await openShell(tester, r.gw, r.planId);
    r.advanceTo('12:33:00');
    await poll(tester);
    tripOf(tester).dismissArrival();
    final avoidBefore = r.gw.sent('GET', '/safety/avoid').length;
    planOf(tester).setTab(1);
    await settle(tester, 20);
    expect(find.byKey(const ValueKey('map-location')), findsOneWidget, reason: '동행 중이면 Map 에도 내 위치');
    expect(r.gw.sent('GET', '/safety/avoid').length, avoidBefore, reason: 'Map 이 따로 avoid 를 부르지 않는다 (60초 안)');

    final sheet = find.byKey(const ValueKey('map-sheet'));
    double top() => tester.getTopLeft(sheet).dy;
    final collapsed = top();
    expect(find.text('Day 1 경로 · 5곳'), findsOneWidget, reason: '접힘 = 요약');
    await tester.drag(find.byType(Grabber).last, const Offset(0, -220));
    await settle(tester, 20);
    final half = top();
    expect(half, lessThan(collapsed - 120), reason: '반');
    expect(find.byKey(const ValueKey('map-day-503')).hitTestable(), findsOneWidget, reason: '반 = 그날 일정 목록');
    await tester.drag(find.byType(Grabber).last, const Offset(0, -250));
    await settle(tester, 20);
    expect(top(), lessThan(half - 150), reason: '펼침');
    await tester.drag(find.byType(Grabber).last, const Offset(0, 700));
    await settle(tester, 20);
    expect(top(), closeTo(collapsed, 2), reason: '다시 접힘');

    // 목록 행 → 그 장소 선택 → 접힘 카드, 끌어 올리면 장소 상세
    await tester.drag(find.byType(Grabber).last, const Offset(0, -220));
    await settle(tester, 20);
    await tester.tap(find.byKey(const ValueKey('map-day-503')));
    await settle(tester, 20);
    expect(planOf(tester).selectedItemId, 503);
    expect(find.text('2 / 5'), findsOneWidget);
    expect(find.text('일정에서 보기'), findsOneWidget);
    expect(find.text('경상북도 포항시 남구 구룡포읍 구룡포리'), findsNothing, reason: '접힘에는 상세 없음 (요청도 안 함)');
    expect(r.gw.sent('GET', '/places/{id}'), isEmpty);
    await tester.drag(find.byType(Grabber).last, const Offset(0, -600));
    await settle(tester, 20);
    expect(find.text('경상북도 포항시 남구 구룡포읍 구룡포리'), findsOneWidget, reason: '펼침 = 장소 상세');
    expect(find.text('운영'), findsOneWidget);
    expect(r.gw.sent('GET', '/places/{id}').single.path, endsWith('/kto-2610501'));

    // [일정에서 보기] 는 그대로 Planner 강조
    await tester.drag(find.byType(Grabber).last, const Offset(0, 800));
    await settle(tester, 20);
    await tester.tap(find.text('일정에서 보기'));
    await settle(tester, 20);
    expect(planOf(tester).tab, 0);
    expect(planOf(tester).selectedItemId, 503);
    expect(r.gw.unmatched, isEmpty);
  });

  testWidgets('28 우회 카드 [이 길로 안내] (동행 중) → Today 로 돌아가 그 장소로 길 안내', (tester) async {
    final r = scene('scene4_safety', flows: ['f6_safety.json']);
    r.gw.on('GET /planner/plans/{id}/safety', (_) => Res.json(200, r.flowResponse('compare_before_safety')));
    r.advanceTo('12:40:00');
    await openShell(tester, r.gw, r.planId);
    tripOf(tester).dismissArrival();
    planOf(tester).setTab(1);
    await settle(tester, 20);
    expect(find.text('위험 구역을 피해 가는 길'), findsOneWidget);
    await tester.tap(find.text('이 길로 안내'));
    await settle(tester, 20);
    expect(planOf(tester).tab, 2, reason: 'Today 로');
    expect(tripOf(tester).guideTo, 516);
    expect(guideCard, findsOneWidget);
    expect(find.descendant(of: guideCard, matching: find.textContaining('카페파도까지')), findsOneWidget);
    final guide = r.gw.sent('POST', '/routing/route').where((q) => (q.body as Map)['steps'] == true).last.body as Map;
    expect(guide['hazards'], 'avoid', reason: '위험 구역을 피하는 길');
  });
}
