// 동행 모드 화면 — 여행 재생 fixture(trip/scene2·3·4)와 흐름 fixture(f3~f6)로 그린다 (LLM 호출 없음).
// 앱은 데모 위치 모드: 위치를 보내지 않고 서버 상태·이벤트(폴링)만 그린다 (trip §2.2, 33).
import 'package:flutter/gestures.dart' show PointerDeviceKind;
import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:latlong2/latlong.dart';
import 'package:guryong_mate/core/theme/app_theme.dart';
import 'package:guryong_mate/core/widgets/common.dart';
import 'package:guryong_mate/features/chat/chat_controller.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';
import 'package:guryong_mate/features/trip/location.dart';
import 'package:guryong_mate/features/trip/trip_controller.dart';
import 'package:provider/provider.dart';

import 'support/fixture_gateway.dart';
import 'support/harness.dart';
import 'support/trip_replay.dart';

Future<void> openShell(WidgetTester tester, FixtureGateway gw, int planId, {bool demo = true, FakeLocationSource? source, bool startTrip = false}) async {
  await phone(tester);
  final s = await signedInSession(gw, prefs: {'demo_location_mode': demo});
  await tester.pumpWidget(app(s, PlannerShell(planId: planId, locationSource: source ?? FakeLocationSource(), startTrip: startTrip)));
  await settle(tester, 30);
}

/// 폴링 한 번 (5초) 을 흘리고 화면을 그린다.
Future<void> poll(WidgetTester tester, [int seconds = 5]) async {
  await tester.pump(Duration(seconds: seconds));
  await settle(tester, 20);
}

TripController tripOf(WidgetTester tester) => Provider.of<TripController>(tester.element(find.byType(IndexedStack, skipOffstage: false).first), listen: false);
ChatController chatOf(WidgetTester tester) => Provider.of<ChatController>(tester.element(find.byType(IndexedStack, skipOffstage: false).first), listen: false);

/// 시트 목록을 위로 올려(이전 대화 쪽) [f] 를 찾는다.
Future<void> revealUp(WidgetTester tester, Finder f) async {
  final sheet = find.descendant(of: find.byType(DraggableScrollableSheet).last, matching: find.byType(Scrollable));
  for (var i = 0; i < 30 && f.evaluate().isEmpty; i++) {
    await tester.drag(sheet.first, const Offset(0, 200));
    await tester.pump(const Duration(milliseconds: 50));
  }
  await reveal(tester, f);
}

Future<void> say(WidgetTester tester, String text) async {
  await tester.enterText(find.byType(TextField).last, text);
  await tester.pump();
  await tester.tap(find.byTooltip('보내기').last);
  await tester.pump();
}

void main() {
  setUpHarness();

  testWidgets('F3 · 11 → 409 not_trip_day → 12 → 다시 시작 → 14 Today 탭 → 여행 종료 → 30 (기록 없음)', (tester) async {
    final gw = FixtureGateway(['f3_trip_start.json']);
    final r = TripReplay('scene2_arrival'); // 계획 모양만 빌림
    final planId = gw.param('start', 'plan_id');
    final plan = {...r.plan, 'id': int.parse(planId)};
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
    // 상태: 처음은 녹화(비활성), 시작 뒤에는 시작 응답, 종료 뒤에는 종료 응답
    var state = gw.response('state_before') as Map<String, dynamic>;
    gw.on('GET /trip/plans/{id}/state', (_) => Res.json(200, state));
    await openShell(tester, gw, plan['id'] as int);

    // 여행일(10/10)이고 첫 일정 1시간 전 이후 → 그날 한 번 11 이 뜬다 (자동 시작은 안 함)
    expect(find.text('오늘 여행 시작할까요?'), findsOneWidget);
    expect(find.textContaining('엄마랑 구룡포 · 10/10 (토) 12:30 – 18:00 · 5곳'), findsOneWidget);
    expect(find.text('늦어져서 일정에 문제가 생기면 먼저 알려드려요'), findsOneWidget);
    expect(find.text('Today'), findsNothing, reason: '계획 모드: Planner · Map');

    await tapText(tester, '여행 시작', frames: 20); // 녹화 순서: 첫 시작은 다른 날 계획 → 409
    expect(find.text('오늘은 여행일이 아니에요'), findsOneWidget);
    expect(find.textContaining('10/17'), findsOneWidget);
    expect(find.text('오늘로 옮기기'), findsOneWidget);
    await tapText(tester, '일정 둘러보기만 할게요');
    expect(find.text('오늘은 여행일이 아니에요'), findsNothing);

    // Planner ⋯ → 여행 시작 → 200 → 동행 모드 (Today 기본)
    state = gw.response('start') as Map<String, dynamic>;
    await tester.tap(find.byTooltip('더보기'));
    await settle(tester);
    await tapText(tester, '여행 시작');
    await tapText(tester, '여행 시작', frames: 30);
    expect(find.text('Today'), findsOneWidget);
    expect(find.text('동행 중'), findsOneWidget);
    expect(find.byKey(const ValueKey('demo-location-chip')), findsOneWidget, reason: '데모 위치 모드 칩 (14-S2)');
    expect(find.textContaining('첫 일정까지'), findsOneWidget, reason: '14-S2 첫 장소 전');
    expect(gw.sent('POST', '/trip/plans/{id}/start').length, 2);
    expect((gw.sent('POST', '/trip/plans/{id}/start').last.body as Map)['session_id'], isNotNull, reason: '선제 턴을 보낼 세션');
    expect(gw.sent('POST', '/trip/plans/{id}/location'), isEmpty, reason: '데모 위치 모드는 GPS 를 보내지 않는다');

    // 실수로 시작 → 여행 종료 → 확인 → 30 "기록된 방문이 없어요"
    await tester.drag(find.byType(DraggableScrollableSheet), const Offset(0, -400));
    await settle(tester);
    await tapText(tester, '여행 종료');
    expect(find.text('여행을 마칠까요?'), findsOneWidget);
    // 서버 상태는 종료 요청부터 바뀐다 (데모 위치 모드는 state 를 1초마다 읽어서, 미리 바꾸면 그 사이 끝나 버린다)
    state = gw.response('end_by_mistake') as Map<String, dynamic>;
    await tapText(tester, '마치기', frames: 30);
    expect(find.text('오늘 여행을 마쳤어요'), findsOneWidget);
    expect(find.text('기록된 방문이 없어요. 일정은 그대로예요.'), findsOneWidget);
    await tester.tap(find.byTooltip('닫기'));
    await settle(tester);
    expect(find.text('Today'), findsNothing, reason: '계획 모드로 돌아감');
    expect(gw.unmatched, isEmpty);
  });

  testWidgets('13 위치 권한 → 위치 없이 계속 → 시작은 된다 → 14-S1 위치 꺼짐 배너 → [켜기] 다시 요청', (tester) async {
    final r = TripReplay('scene2_arrival');
    final started = r.timeline.firstWhere((e) => (e['route'] ?? '').toString().endsWith('/start'))['response'] as Map<String, dynamic>;
    r.gw.on('POST /trip/plans/{id}/start', (_) {
      r.state = Map.of(started);
      return Res.json(200, started);
    });
    final loc = FakeLocationSource(access: LocationAccess.denied, afterRequest: LocationAccess.denied);
    await openShell(tester, r.gw, r.planId, demo: false, source: loc, startTrip: true);
    await tapText(tester, '여행 시작', frames: 20);
    expect(find.text('위치를 켜면 현장에서 함께해요'), findsOneWidget);
    expect(find.text('위치 허용'), findsOneWidget);
    await tapText(tester, '위치 없이 계속', frames: 30);
    expect(find.text('동행 중'), findsOneWidget);
    expect(find.text('위치가 꺼져 있어요'), findsOneWidget);
    expect(find.text('도착 해설·위험 구역 알림이 동작하지 않아요'), findsOneWidget);
    await tapText(tester, '켜기');
    expect(loc.requests, 1);
    loc.afterRequest = LocationAccess.granted;
    await tapText(tester, '켜기');
    expect(find.text('위치가 꺼져 있어요'), findsNothing);
    expect(tripOf(tester).sender?.running, isTrue, reason: '권한을 받으면 위치 전송 시작');
  });

  testWidgets('장면 ② 14 → 16 도착 카드 → 17 가이드 노트 → 18 장소 질문 · "더 짧게" → 기억 칩', (tester) async {
    final r = TripReplay('scene2_arrival', flows: ['f4_arrival_docent.json']);
    r.advanceTo('12:30:30');
    await openShell(tester, r.gw, r.planId);
    expect(find.text('동행 중'), findsOneWidget);
    expect(find.textContaining('첫 일정까지'), findsOneWidget);
    expect(find.text('구룡포 일본인 가옥거리'), findsWidgets);

    r.advanceTo('12:33:00'); // 12:31:30 위치 응답에 arrived (단계 2, 해설 카드)
    await poll(tester);
    expect(find.byKey(const ValueKey('arrival-card')), findsOneWidget);
    expect(find.text('도착했어요 · 가이드 준비됨'), findsOneWidget);
    expect(find.text('이야기 들어볼까요?'), findsOneWidget);
    expect(find.text('평소 선호'), findsNothing, reason: '아직 길이 선호 없음 (style_length null)');
    expect(find.textContaining('지금 · 12:30 도착'), findsOneWidget, reason: '패널 지금 = visiting');
    expect(r.events.values.firstWhere((e) => e['type'] == 'arrived')['seen_at'], isNotNull, reason: '카드가 보이면 seen');

    await tapText(tester, '짧게', frames: 20);
    expect(find.text('현장 가이드'), findsOneWidget);
    expect(find.textContaining('일제강점기 구룡포에 정착한'), findsOneWidget, reason: '노트 short 를 기다림 없이');
    expect(find.text('당신에게'), findsOneWidget);
    expect(find.text('사진 포인트'), findsOneWidget);
    expect(find.text('알아두세요'), findsOneWidget);
    await tester.tap(find.text('자세히 · 2~3분'));
    await settle(tester);
    expect(find.textContaining('동해안 어업의 전진기지'), findsOneWidget, reason: 'Segment → long');
    expect(tripOf(tester).heard, containsPair('kto-1909659', 'long'));

    await tester.scrollUntilVisible(find.text('출처'), 200, scrollable: find.byType(Scrollable).first);
    await settle(tester);
    await tester.tap(find.text('집들은 언제 지어졌어요?'));
    await settle(tester, 20);
    await until(tester, () => !chatOf(tester).busy);
    await settle(tester);
    expect(find.text('구룡포 일본인 가옥거리 · 현장 해설'), findsOneWidget, reason: '18 헤더 컨텍스트');
    expect(find.textContaining('2단계 완료'), findsOneWidget);

    await say(tester, '다음부터는 더 짧게 해줘요');
    await until(tester, () => !chatOf(tester).busy);
    await settle(tester);
    expect(find.text('기억했어요 · 해설은 핵심만 짧게'), findsOneWidget);
    expect(find.text('나에 대해 ›'), findsOneWidget);
    expect(r.gw.unmatched, isEmpty);
  });

  testWidgets('16 · 짧게 선호면 [짧게]가 "평소 선호" · 노트가 없으면 16-S1 [지금 설명 듣기]', (tester) async {
    final r = TripReplay('scene3_delay', flows: ['f4_arrival_docent.json']);
    r.gw.on('GET /docent/memory', (_) => Res.json(200, r.flowResponse('memory_after')));
    r.noteOverride = (req) => req.path.endsWith('kto-2610501') ? Res.error(404, 'note_not_found') : Res.json(200, r.flowResponse('note'));
    r.advanceTo('12:31:00');
    await openShell(tester, r.gw, r.planId);
    r.advanceTo('12:33:00');
    await poll(tester);
    expect(find.text('30초 · 평소 선호'), findsOneWidget, reason: '"더 짧게" 다음 장소부터 반영 (18 확인 포인트)');
    await tester.tap(find.byTooltip('닫기').first);
    await settle(tester);
    expect(find.byKey(const ValueKey('arrival-card')), findsNothing);

    r.advanceTo('14:10:00'); // 구룡포항 도착 (노트 없음)
    await poll(tester);
    // 3단계(delay) 미리보기가 같이 떠 있으면 카드는 칩으로 접힌다 (events.md §2)
    if (find.byKey(const ValueKey('arrival-chip')).evaluate().isNotEmpty) {
      await tester.tap(find.byKey(const ValueKey('arrival-chip')));
      await settle(tester);
    }
    expect(find.text('도착했어요 · 가이드 없음'), findsOneWidget);
    expect(find.text('지금 설명 듣기'), findsOneWidget);
    expect(find.text('준비된 해설이 없어 10초쯤 걸려요'), findsOneWidget);
  });

  testWidgets('장면 ③ 가벼운 지연(Amber) → delay 선제 → 14-S3 미리보기·FAB 점 → 19 → 20 다시 제안 → 21-S1 → 승인', (tester) async {
    final r = TripReplay('scene3_delay', flows: ['f5_delay.json']);
    r.advanceTo('13:43:00');
    await openShell(tester, r.gw, r.planId);
    expect(find.text('13분 늦음'), findsOneWidget, reason: '가벼운 지연: ETA 칩 Amber, 알림 없음');
    expect(find.byKey(const ValueKey('proactive-peek')), findsNothing);

    r.advanceTo('13:53:00'); // delay queued (agent 없음) → 미리보기 없음, 2초 폴링
    await poll(tester);
    expect(find.byKey(const ValueKey('proactive-peek')), findsNothing, reason: 'queued 는 알림 목록에만');
    expect(tripOf(tester).pollInterval, const Duration(seconds: 2));

    r.advanceTo('14:00:00'); // 폴링 응답에 agent 가 채워짐 + 선제 세션
    await poll(tester, 2);
    expect(find.byKey(const ValueKey('proactive-peek')), findsOneWidget);
    expect(find.text('일정 지연'), findsOneWidget);
    expect(find.textContaining('18:01 에 끝나'), findsOneWidget, reason: '미리보기 = 서버 message');
    expect(find.byKey(const ValueKey('fab-suggestion-dot')), findsOneWidget);
    expect(find.text('18:01 종료 · 규칙 넘음'), findsOneWidget, reason: '새 위반 + 대기 제안 → ETA 칩 Red');

    await tester.tap(find.text('보기 ›'));
    await settle(tester, 30);
    expect(find.byKey(const ValueKey('proactive-header')), findsOneWidget);
    expect(find.textContaining('구룡메이트가 먼저 알려드려요', findRichText: true), findsOneWidget);
    expect(find.text('엄마랑 구룡포 · 동행 중'), findsOneWidget);
    expect(find.text('일정 변경 제안'), findsOneWidget);
    expect(find.byKey(const ValueKey('end-row')), findsOneWidget, reason: '종료 시각 행');
    expect(find.text('18:00 전 종료'), findsOneWidget);
    expect(find.byKey(const ValueKey('fab-suggestion-dot')), findsNothing, reason: '시트에서 메시지를 보면 점이 사라짐');
    final keep = find.text('구룡포과메기문화관은 유지해줘');
    await reveal(tester, keep);
    expect(find.text('그대로 둘게요'), findsOneWidget);

    await tester.tap(keep);
    await settle(tester);
    await until(tester, () => !chatOf(tester).busy);
    await settle(tester);
    expect(chatOf(tester).proposalEntry(120)?.state, ProposalState.superseded, reason: '20 새 제안이 이전 제안을 대체');
    await revealUp(tester, find.text('새 제안으로 대체됨'));
    expect(find.text('새 제안으로 대체됨'), findsOneWidget, reason: '20 이전 카드 접힘');
    expect(find.textContaining('조건 반영'), findsWidgets);
    final cmp = find.text('비교 보기 ›').last;
    await reveal(tester, cmp);
    await tester.tap(cmp);
    await settle(tester, 30);
    expect(find.text('규칙 위반'), findsOneWidget, reason: '21-S1: 안전 변화 없음 → 첫 지표 규칙 위반');
    await tester.tap(find.text('전체 적용').last);
    await settle(tester, 40);
    expect(r.gw.sent('POST', '/agent/sessions/{sid}/proposals/{pid}/accept').length, 1);
  });

  testWidgets('장면 ④ safety_raised 미리보기(닫을 때까지) → 22 근거 박스·위험한 곳 1 → 0 → near_hazard 23 최상단 → 확인 → 띠 → 24 알림', (tester) async {
    final r = TripReplay('scene4_safety', flows: ['f6_safety.json']);
    r.advanceTo('15:16:30');
    await openShell(tester, r.gw, r.planId);
    r.advanceTo('15:22:00'); // safety_raised (agent 채워짐) + 선제 세션
    await poll(tester);
    expect(find.byKey(const ValueKey('proactive-peek')), findsOneWidget);
    expect(find.text('안전 · 위험으로 바뀜'), findsOneWidget);
    await tester.pump(const Duration(seconds: 10));
    await settle(tester);
    expect(find.byKey(const ValueKey('proactive-peek')), findsOneWidget, reason: '안전은 8초 뒤에도 접히지 않는다');

    await tester.tap(find.text('보기 ›'));
    await settle(tester, 30);
    expect(find.byKey(const ValueKey('hazard-evidence')), findsOneWidget);
    expect(find.textContaining('풍속 16m/s 예보'), findsWidgets);
    expect(find.text('기상청 단기예보 · 기상특보'), findsOneWidget, reason: '출처 줄 (중복 없이)');
    await reveal(tester, find.byKey(const ValueKey('safety-impact')));
    expect(find.text('1 → 0'), findsOneWidget, reason: '위험한 곳 1 → 0 (planner safety preview)');
    expect(find.text('실내 말고 다른 곳은?'), findsOneWidget);
    await tester.tap(find.byTooltip('닫기').first);
    await settle(tester);

    r.advanceTo('16:34:07'); // 16:33:54 near_hazard (단계 1, 즉시 경고)
    await poll(tester);
    expect(find.byKey(const ValueKey('hazard-panel')), findsOneWidget);
    expect(find.text('침수 감시 구역 근처예요'), findsOneWidget);
    expect(find.text('구룡포수협 주변 · 물이 고인 길은 피하세요'), findsOneWidget);
    expect(find.text('수위 센서 실측 · 위험 (등급 3) · 16:33'), findsOneWidget);
    // 최상단: 열린 시트 위에서도 (역사관 도착 카드가 FAB 위에 떠 있어 먼저 닫는다 — Figma 16 배치)
    tripOf(tester).dismissArrival();
    await settle(tester);
    await tester.tap(find.byTooltip('구룡메이트 열기').first);
    await settle(tester, 20);
    expect(find.byKey(const ValueKey('hazard-panel')).hitTestable(), findsOneWidget);
    await tester.tap(find.text('확인'));
    await settle(tester);
    expect(find.byKey(const ValueKey('hazard-strip')), findsOneWidget);
    await tester.tap(find.descendant(of: find.byType(DraggableScrollableSheet).last, matching: find.byTooltip('닫기')));
    await settle(tester);
    expect(find.byKey(const ValueKey('hazard-strip')), findsOneWidget, reason: '구역 안에 있는 동안 띠로 남는다');

    await tester.tap(find.byTooltip('알림'));
    await settle(tester, 30);
    expect(find.text('알림'), findsOneWidget);
    expect(find.text('지도'), findsWidgets);
    expect(find.text('제안 보기'), findsOneWidget, reason: '대기 중인 safety 제안');
    expect(find.textContaining('남은 일정에 위험 신호'), findsOneWidget);
  });
  testWidgets('15 길 안내선: 지나온 부분은 회색, 남은 부분만 Primary · 다음 장소로 가는 구간선은 겹쳐 그리지 않는다', (tester) async {
    final r = TripReplay('scene4_safety', flows: ['f6_safety.json']);
    r.advanceTo('13:35:24'); // 13:33:31 departed → 이동 중
    // 안내 경로: 출발점(지금 위치보다 뒤) → 지금 위치 근처 → 중간 → 다음 장소. 지금 위치는 둘째 점.
    r.gw.on('POST /routing/route', (q) {
      final b = q.body as Map;
      if (b['steps'] != true) return Res.error(503, 'routing_unavailable');
      final st = (b['stops'] as List).cast<Map>();
      final (lat0, lng0, lat1, lng1) = (st[0]['lat'] as num, st[0]['lng'] as num, st[1]['lat'] as num, st[1]['lng'] as num);
      return Res.json(200, {
        'mode': 'walk', 'source': 'road', 'minutes': 8, 'distance_m': 600,
        'geometry': {'type': 'LineString', 'coordinates': [
          [lng0 - 0.002, lat0 - 0.002], [lng0, lat0], [(lng0 + lng1) / 2, (lat0 + lat1) / 2], [lng1, lat1],
        ]},
        'legs': [{'steps': [
          {'type': 'start', 'instruction': '출발', 'distance_m': 0, 'begin_index': 0},
          {'type': 'left', 'instruction': '왼쪽으로', 'distance_m': 300, 'begin_index': 1},
          {'type': 'arrive', 'instruction': '도착', 'distance_m': 0, 'begin_index': 3},
        ]}],
      });
    });
    await openShell(tester, r.gw, r.planId);
    await poll(tester);
    expect(find.byKey(const ValueKey('guide-card')), findsOneWidget, reason: '이동 중이면 안내 카드');

    final t = tripOf(tester);
    final next = t.item(t.state!.nextItemId)!;
    final loc = (t.state!.location!.lat, t.state!.location!.lng);
    final lines = tester.widget<PolylineLayer>(find.byType(PolylineLayer).first).polylines;
    bool at(LatLng p, (num, num) q) => (p.latitude - q.$1).abs() < 1e-9 && (p.longitude - q.$2).abs() < 1e-9;
    final passed = lines.where((l) => l.color == const Color(0xFFAEB6C1) && l.points.length == 2 && at(l.points.last, loc));
    expect(passed, hasLength(1), reason: '출발점 → 지금 위치는 회색');
    final toNext = lines.where((l) => at(l.points.last, (next.place!.lat, next.place!.lng)) && l.color == AppColors.primary).toList();
    expect(toNext, hasLength(1), reason: '다음 장소로 가는 Primary 선은 안내선 하나만 (구간선 숨김)');
    expect(toNext.single.points, hasLength(3), reason: '지금 위치부터 남은 부분');
    expect(at(toNext.single.points.first, loc), isTrue);
  });
  testWidgets('14 패널: 마우스로도 끌어서 펼치고 접는다 (데스크톱 데모)', (tester) async {
    final r = TripReplay('scene4_safety', flows: ['f6_safety.json']);
    r.advanceTo('12:40:00');
    await openShell(tester, r.gw, r.planId);
    tripOf(tester).dismissArrival();
    await settle(tester, 10);
    double top() => tester.getTopLeft(find.byType(Grabber).last).dy;
    final base = top();
    await tester.drag(find.byType(Grabber).last, const Offset(0, -250), kind: PointerDeviceKind.mouse);
    await settle(tester, 20);
    expect(top(), lessThan(base - 200), reason: '펼침');
    await tester.drag(find.byType(Grabber).last, const Offset(0, 600), kind: PointerDeviceKind.mouse);
    await settle(tester, 20);
    expect(top(), greaterThan(base + 100), reason: '접힘 (지도를 넓게)');
  });
}
