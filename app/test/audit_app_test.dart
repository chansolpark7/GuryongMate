// 앱 상태·표시 로직 버그 (docs/audit/logic-bugs-2026-10-12.md §6, F1~F12) 회귀 테스트.
// 여행 재생 fixture(trip/scene2)와 planner JSON 도우미(fixtures.dart)로 그린다 (LLM·서버 없음).
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/app_session.dart';
import 'package:guryong_mate/core/models/plan.dart';
import 'package:guryong_mate/core/models/trip.dart';
import 'package:guryong_mate/core/widgets/safety_widgets.dart';
import 'package:guryong_mate/features/chat/compare_screen.dart' show appliedItemId;
import 'package:guryong_mate/features/chat/proposal_card.dart';
import 'package:guryong_mate/features/planner/plan_controller.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';
import 'package:guryong_mate/features/planner/restore_ops.dart';
import 'package:guryong_mate/features/trip/event_inbox_screen.dart';
import 'package:guryong_mate/features/trip/trip_controller.dart';
import 'package:guryong_mate/features/trip/trip_end_screen.dart';

import 'fixtures.dart';
import 'support/fixture_gateway.dart';
import 'support/harness.dart';
import 'support/trip_replay.dart';

final _live = <void Function()>[];

/// 타이머가 남지 않게 테스트 본문 끝에서 정리.
void done() {
  for (final f in _live) {
    f();
  }
  _live.clear();
}

Future<(TripController, PlanController, AppSession)> make(FixtureGateway gw, int planId, {bool demo = false}) async {
  final session = await signedInSession(gw, prefs: {'demo_location_mode': demo});
  final plan = PlanController(session.api, planId);
  await plan.load();
  final t = TripController(api: session.api, plan: plan, session: session, source: FakeLocationSource());
  _live.add(() {
    t.dispose();
    plan.dispose();
  });
  return (t, plan, session);
}

Map<String, dynamic> eventJson(int id, String type, {int day = 1, int level = 1, String? message, Map<String, dynamic> payload = const {}, String? createdAt}) => {
      'id': id,
      'plan_id': 243,
      'day': day,
      'type': type,
      'level': level,
      'severity': 'info',
      'created_at': createdAt ?? '2026-10-09T14:40:00Z',
      'now': '2026-10-10T12:26:00+09:00',
      'item_ids': [],
      'message': message ?? '$type $id',
      'payload': payload,
      'status': 'delivered',
      'agent': null,
      'card': null,
      'seen_at': null,
    };

Map<String, dynamic> visited(Map<String, dynamic> item, String arrived, String left) => {...item, 'status': 'done', 'arrived_at': arrived, 'done_at': left};

void main() {
  setUpHarness();

  group('F1 · 다른 클라이언트가 시작한 여행', () {
    testWidgets('여행 기간 중이면 여행 전에도 state 를 15초마다 읽어 동행 모드로 바뀐다', (tester) async {
      final r = TripReplay('scene2_arrival'); // 10/10 12:25 여행 전 (계획 10/10)
      final (t, _, _) = await make(r.gw, r.planId);
      await t.load();
      expect(t.active, isFalse);
      int states() => r.gw.sent('GET', '/trip/plans/{id}/state').length;
      final s0 = states();
      await tester.pump(const Duration(seconds: 14));
      expect(states(), s0);
      await tester.pump(const Duration(seconds: 1));
      expect(states(), s0 + 1, reason: '여행 전 15초');
      expect(t.active, isFalse);

      r.advanceTo('12:25:00'); // 진행 스크립트·대시보드가 여행을 시작
      await tester.pump(const Duration(seconds: 15));
      await tester.pump();
      expect(t.active, isTrue, reason: '동행 모드로');
      expect(r.gw.sent('GET', '/trip/plans/{id}/events'), isNotEmpty, reason: '이벤트 폴링 시작');
      done();
    });

    testWidgets('앱이 다시 보이면 바로 다시 읽는다 · 데모 위치 모드면 5초', (tester) async {
      final r = TripReplay('scene2_arrival');
      final (t, _, _) = await make(r.gw, r.planId, demo: true);
      await t.load();
      int states() => r.gw.sent('GET', '/trip/plans/{id}/state').length;
      final s0 = states();
      await tester.pump(const Duration(seconds: 5));
      expect(states(), s0 + 1, reason: '데모 위치 모드: 여행 전 5초');

      r.advanceTo('12:25:00');
      t.didChangeAppLifecycleState(AppLifecycleState.resumed);
      await tester.pump();
      await tester.pump();
      expect(t.active, isTrue);
      done();
    });

    testWidgets('여행 기간이 아니면 여행 전 state 를 읽지 않는다', (tester) async {
      final r = TripReplay('scene2_arrival');
      r.state = {...r.state, 'now': '2026-10-12T10:00:00+09:00'};
      final (t, _, _) = await make(r.gw, r.planId);
      await t.load();
      final s0 = r.gw.sent('GET', '/trip/plans/{id}/state').length;
      await tester.pump(const Duration(seconds: 60));
      expect(r.gw.sent('GET', '/trip/plans/{id}/state').length, s0);
      done();
    });
  });

  group('F2 · 다른 클라이언트가 승인한 일정', () {
    testWidgets('동행 중 계획 version 이 바뀌면 다음 도착·출발 전에 다시 읽는다 (목록 version 확인 · state 의 다음 일정 변화)', (tester) async {
      final r = TripReplay('scene2_arrival')..advanceTo('12:35:00');
      final (t, plan, _) = await make(r.gw, r.planId);
      await t.load();
      expect(t.active, isTrue);
      expect(plan.plan!.version, 7);

      // 대시보드에서 승인: 계획만 바뀌고 state 는 그대로
      r.plan['version'] = 8;
      ((r.plan['days'] as List).first['items'] as List).cast<Map<String, dynamic>>().firstWhere((i) => i['id'] == 503)['start_at'] = '13:55';
      await tester.pump(const Duration(seconds: 15)); // state 주기 → version 확인
      await tester.pump();
      expect(plan.plan!.version, 8);
      expect(plan.plan!.item(503)!.startAt, '13:55');
      expect(r.gw.sent('GET', '/planner/plans'), hasLength(1), reason: '확인은 목록 한 번 (20초 주기)');

      // 20초 안이라도 state 의 다음 일정 계획 시작이 바뀌면 바로
      r.plan['version'] = 9;
      r.state = {...r.state, 'next': {...(r.state['next'] as Map? ?? const {}), 'planned_start': '14:05'}};
      await tester.pump(const Duration(seconds: 15));
      await tester.pump();
      expect(plan.plan!.version, 9);
      done();
    });
  });

  group('F3 · 하루 요약의 함께 바꾼 일정', () {
    final s = TripState.fromJson({
      'active': false,
      'day': 1,
      'clock': 'sim',
      'started_at': '2026-10-10T12:25:00+09:00',
      'now': '2026-10-10T18:00:00+09:00',
      'ended_at': '2026-10-10T18:00:00+09:00',
    });
    final realNow = DateTime.utc(2026, 10, 12, 5, 30);

    test('여행일 실제 시작: day_started 의 created_at → 실제 시계면 now − started_at → 모르면 18시간', () {
      final started = TripEvent.fromJson(eventJson(1, 'day_started', level: 0, createdAt: '2026-10-12T05:00:00Z'));
      expect(tripRealStart(s, [started], realNow), DateTime.utc(2026, 10, 12, 5));
      expect(tripRealStart(s, const [], realNow), realNow.subtract(const Duration(hours: 18)), reason: '가상 시각은 실제와 비교 못 함');
      final sys = TripState.fromJson({'active': true, 'day': 1, 'clock': 'system', 'started_at': '2026-10-12T13:25:00+09:00', 'now': '2026-10-12T14:25:00+09:00'});
      expect(tripRealStart(sys, const [], realNow), realNow.subtract(const Duration(hours: 1)));
    });

    testWidgets('계획을 만든 제안은 빼고, 여행을 시작한 뒤 승인한 것만 · 그 장소만 "바꿈" · 지난 날 알림 없음 (F7)', (tester) async {
      final gw = FixtureGateway(const []);
      final items = [
        visited(itemJson(1, 1, '구룡포 시장', '12:30', '13:30'), '12:30', '13:30'),
        visited(itemJson(2, 2, '일본인 가옥거리', '13:40', '14:40', travel: 8), '13:40', '14:40'),
        visited(itemJson(3, 3, '해수욕장 산책', '14:50', '15:50', travel: 8), '14:50', '15:50'),
      ];
      final plan = planJson(items, start: '2026-10-10', end: '2026-10-11');
      Map<String, dynamic> proposal(int id, String decided, List<int> added) => {
            'id': id, 'plan_id': 1, 'status': 'accepted', 'summary': '', 'source': 'llm', 'ops': [],
            'created_at': decided, 'decided_at': decided, 'before': null, 'after': null,
            'changes': [
              for (final i in added) {'kind': 'added', 'message': "추가: 1일차 '${(items[i - 1]['place'] as Map)['title']}' (60분)", 'item_id': i, 'day': 1},
            ],
          };
      gw.on('GET /planner/plans/{id}', (_) => Res.json(200, plan));
      gw.on('GET /trip/plans/{id}/events', (_) => Res.json(200, [
            eventJson(10, 'day_started', level: 0, createdAt: '2026-10-12T05:00:00Z'),
            eventJson(11, 'near_hazard', day: 2, level: 2, message: '침수 감시 구역 근처예요'),
          ]));
      gw.on('GET /planner/plans/{id}/proposals', (_) => Res.json(200, [
            proposal(1, '2026-10-12T04:50:00Z', [1, 2, 3]), // 계획을 만든 제안 (여행 시작 전)
            proposal(2, '2026-10-12T05:20:00Z', [3]), // 여행 중 승인
          ]));
      gw.on('GET /docent/memory', (_) => Res.json(200, {'items': [], 'memory_version': 1, 'style_length': null}));
      final session = await signedInSession(gw);
      await phone(tester);
      await tester.pumpWidget(app(session, TripEndScreen(api: session.api, planId: 1, state: s)));
      await settle(tester);
      expect(find.text('1번'), findsOneWidget, reason: '함께 바꾼 일정 1번');
      expect(find.text('바꿈'), findsOneWidget, reason: '여행 중 들어온 장소만');
      expect(find.textContaining('침수 감시 구역'), findsNothing, reason: '2일차 알림은 1일차 요약에 없다');
    });
  });

  group('F4 · 마지막 장소에 머무는 중', () {
    testWidgets('다음 일정이 없으면 "지금이 마지막 일정이에요"', (tester) async {
      final r = TripReplay('scene2_arrival', flows: ['f4_arrival_docent.json'])..advanceTo('12:35:00');
      final items = ((r.plan['days'] as List).first['items'] as List).cast<Map<String, dynamic>>();
      for (final i in items) {
        i['status'] = i['id'] == 506 ? 'visiting' : 'done';
        i['arrived_at'] = i['start_at'];
        if (i['id'] != 506) i['done_at'] = i['end_at'];
      }
      r.state = {...r.state, 'current': {'item_id': 506, 'state': 'visiting', 'since': '16:55'}, 'next': null, 'expected': null};
      await phone(tester);
      final s = await signedInSession(r.gw, prefs: {'demo_location_mode': true});
      await tester.pumpWidget(app(s, PlannerShell(planId: r.planId, locationSource: FakeLocationSource())));
      await settle(tester, 30);
      final header = find.byKey(const ValueKey('remaining-header'));
      expect(header, findsOneWidget);
      expect(tester.widget<Text>(header).data, startsWith('지금이 마지막 일정이에요'));
      await tester.pumpWidget(const SizedBox());
    });
  });

  group('F5 · 여러 날 계획의 시작 시트', () {
    testWidgets('여행 전이면 지난 여행일이 아니라 서버 시각의 오늘 (2일차)', (tester) async {
      final gw = FixtureGateway(const []);
      gw.on('GET /planner/plans/{id}', (_) => Res.json(200, planJson([
            itemJson(1, 1, '구룡포 시장', '09:00', '10:00'),
            itemJson(2, 1, '호미곶', '10:00', '11:00', day: 2),
            itemJson(3, 2, '과메기문화관', '12:00', '13:00', day: 2),
          ])));
      var now = '2026-10-11T09:00:00+09:00';
      gw.on('GET /trip/plans/{id}/state', (_) => Res.json(200, {
            ...inactiveTripState(1, now: now),
            'day': 1,
            'date': '2026-10-10',
            'started_at': '2026-10-10T09:00:00+09:00',
            'ended_at': '2026-10-10T18:00:00+09:00',
            'ended_reason': 'user',
          }));
      final (t, _, _) = await make(gw, 1);
      await t.load();
      expect(t.today?.day, 2);
      expect(t.today?.items, hasLength(2));
      done();

      now = '2026-10-13T09:00:00+09:00'; // 기간 밖이면 예전처럼 마지막 여행일
      final (t2, _, _) = await make(gw, 1);
      await t2.load();
      expect(t2.today?.day, 1);
      done();
    });
  });

  group('F6 · 이벤트가 50개를 넘을 때', () {
    testWidgets('처음 쌓인 것은 끝까지 이어 읽어 모두 backlog (다시 알리지 않음) · 알림 목록은 최신순 전부', (tester) async {
      final r = TripReplay('scene2_arrival')..advanceTo('12:35:00');
      for (var id = 1000; id < 1120; id++) {
        r.events[id] = eventJson(id, 'departed', message: '출발 알림 $id', payload: {'item_id': 999});
      }
      final (t, _, session) = await make(r.gw, r.planId);
      await t.load();
      await tester.pump();
      expect(t.events.where((e) => e.id >= 1000), hasLength(120));
      expect(t.toast, isNull, reason: '쌓여 있던 출발은 토스트로 다시 알리지 않는다');
      await tester.pump(const Duration(seconds: 5));
      expect(t.toast, isNull, reason: '다음 폴링에서도 (예전: 51번째부터 새것처럼)');

      await phone(tester);
      await tester.pumpWidget(app(session, EventInboxScreen(t: t)));
      await settle(tester);
      expect(find.text('출발 알림 1119'), findsOneWidget, reason: '가장 새 알림이 맨 위');
      final reads = r.gw.sent('GET', '/trip/plans/{id}/events').where((q) => q.query['limit'] == '200');
      expect(reads, isNotEmpty, reason: '알림 목록은 200개씩 끝까지');
      await tester.pumpWidget(const SizedBox());
      done();
    });
  });

  group('F7 · 여러 날 여행의 지난 날 이벤트', () {
    testWidgets('목록·배지·알림은 지금 여행일 것만', (tester) async {
      final r = TripReplay('scene2_arrival')..advanceTo('12:35:00');
      r.state = {...r.state, 'day': 2};
      r.events[2000] = eventJson(2000, 'departed', day: 1, payload: {'item_id': 999});
      final (t, _, _) = await make(r.gw, r.planId);
      await t.load();
      await tester.pump();
      expect(t.events.any((e) => e.id == 2000), isFalse);
      final unseen = t.unseenCount;
      t.ingest([TripEvent.fromJson(eventJson(2001, 'near_hazard', day: 1, level: 2))]);
      expect(t.hazard, isNull, reason: '지난 날 위험 구역 알림은 띄우지 않는다');
      expect(t.unseenCount, unseen);
      t.ingest([TripEvent.fromJson(eventJson(2002, 'near_hazard', day: 2, level: 2))]);
      expect(t.hazard?.id, 2002);
      done();
    });
  });

  group('F8 · 늦게 온 옛 계획 응답', () {
    testWidgets('방금 받은 새 일정(version·progress_version 이 큼)을 옛 GET 이 덮지 않는다', (tester) async {
      final gw = FixtureGateway(const []);
      final base = planJson([itemJson(1, 1, '구룡포 시장', '09:00', '10:00')]);
      var body = {...base, 'version': 7, 'progress_version': 2};
      Completer<void>? gate;
      gw.on('GET /planner/plans/{id}', (_) async {
        final snap = body; // 요청을 받은 때의 일정
        if (gate != null) await gate!.future;
        return Res.json(200, snap);
      });
      final (_, plan, _) = await make(gw, 1);
      expect(plan.plan!.version, 7);

      gate = Completer<void>();
      final late = plan.load(silent: true); // 옛 일정을 읽는 중
      await tester.pump();
      plan.applyPlan(PlanDetail.fromJson({...base, 'version': 8, 'progress_version': 2})); // 승인 응답
      gate!.complete();
      await late;
      expect(plan.plan!.version, 8, reason: '옛 GET 응답은 버린다');

      gate = Completer<void>();
      final late2 = plan.load(silent: true);
      await tester.pump();
      plan.applyPlan(PlanDetail.fromJson({...base, 'version': 8, 'progress_version': 3})); // 도착 기록
      body = {...base, 'version': 8, 'progress_version': 2};
      gate!.complete();
      await late2;
      expect(plan.plan!.progressVersion, 3, reason: '진행 version 이 작아도 옛 것');

      gate = null;
      body = {...base, 'version': 9, 'progress_version': 3};
      await plan.load(silent: true);
      expect(plan.plan!.version, 9, reason: '새 응답은 그대로');
      done();
    });
  });

  test('F9 · 실행 취소는 구간 이동 수단(travel_mode)도 되돌린다', () {
    final before = PlanDetail.fromJson(planJson([
      itemJson(1, 1, '구룡포 시장', '09:00', '10:00'),
      {...itemJson(2, 2, '호미곶', '10:30', '11:30', travel: 20), 'travel_mode': 'car'},
      {...itemJson(3, 3, '과메기문화관', '12:00', '13:00', travel: 10), 'travel_mode': 'car'},
    ]));
    final current = PlanDetail.fromJson(planJson([
      itemJson(1, 1, '구룡포 시장', '09:00', '10:00'),
      itemJson(2, 2, '호미곶', '11:00', '12:00', travel: 50), // AI 가 도보로 (travel_mode: null = 계획 기본)
    ]));
    final ops = buildRestoreOps(before, current);
    expect(ops.firstWhere((o) => o['op'] == 'update_item' && o['item_id'] == 2)['travel_mode'], 'car');
    expect(ops.firstWhere((o) => o['op'] == 'add_item')['travel_mode'], 'car', reason: '다시 만드는 항목도');
    final keep = ops.firstWhere((o) => o['op'] == 'update_item' && o['item_id'] == 1);
    expect(keep.containsKey('travel_mode'), isTrue);
    expect(keep['travel_mode'], isNull, reason: '계획 기본 그대로');
  });

  group('F10 · 같은 장소를 지웠다 다시 넣은 제안', () {
    ProposalDetail moved({required int order, required String start}) => ProposalDetail.fromJson({
          'id': 5, 'plan_id': 1, 'status': 'pending', 'summary': '', 'source': 'llm', 'ops': [],
          'created_at': '2026-10-04T10:00:00Z', 'decided_at': null,
          'changes': [
            {'kind': 'removed', 'message': "삭제: 1일차 '구룡포항'", 'item_id': 4, 'day': 1},
            {'kind': 'added', 'message': "추가: 1일차 '구룡포항' (30분)", 'item_id': -1, 'day': 1},
          ],
          'before': planJson([
            itemJson(1, 1, '구룡포 시장', '09:00', '10:00'),
            itemJson(4, 2, '구룡포항', '15:30', '16:00'),
            itemJson(5, 3, '과메기문화관', '16:10', '17:00'),
          ]),
          'after': planJson([
            itemJson(1, 1, '구룡포 시장', '09:00', '10:00'),
            if (order == 2) itemJson(-1, 2, '구룡포항', start, '17:30', placeId: 'kto-4'),
            itemJson(5, order == 2 ? 3 : 2, '과메기문화관', '16:10', '17:00'),
            if (order == 3) itemJson(-1, 3, '구룡포항', start, '17:30', placeId: 'kto-4'),
          ]),
        });

    test('다른 자리면 "이동" 한 줄 (이전 시각 → 새 시각), 삭제·추가 따로가 아니다', () {
      final rows = buildChangeRows(moved(order: 3, start: '17:00'));
      expect(rows, hasLength(1));
      expect(rows.single.badge, '이동');
      expect(rows.single.title, '구룡포항');
      expect((rows.single.fromText, rows.single.toText), ('15:30', '17:00'));
    });

    test('같은 자리여도 "교체 구룡포항 → 구룡포항" 이 아니라 "이동"', () {
      final rows = buildChangeRows(moved(order: 2, start: '15:50'));
      expect(rows.single.badge, '이동');
      expect((rows.single.fromText, rows.single.toText), ('15:30', '15:50'));
    });
  });

  test('F11 · 승인한 제안의 새 항목(임시 음수 id)은 지금 일정에서 같은 날·같은 장소로 찾는다', () {
    final current = PlanDetail.fromJson(planJson([
      itemJson(1, 1, '구룡포 시장', '09:00', '10:00'),
      itemJson(9, 2, '구룡포 근대문화역사관', '10:10', '11:00', placeId: 'kto-new'),
    ]));
    final after = PlanDetail.fromJson(planJson([
      itemJson(1, 1, '구룡포 시장', '09:00', '10:00'),
      itemJson(-1, 2, '구룡포 근대문화역사관', '10:10', '11:00', placeId: 'kto-new'),
    ]));
    expect(appliedItemId(after.item(-1)!, current), 9);
    expect(appliedItemId(after.item(1)!, current), 1);
    expect(appliedItemId(after.item(-1)!, null), isNull);
  });

  test('F12 · 안전 시각은 기기 시간대가 아니라 KST 벽시계로', () {
    expect(hhmm(DateTime.parse('2026-10-10T15:20:00+09:00')), '15:20');
    expect(hhmm(DateTime.parse('2026-10-10T06:20:00Z')), '15:20');
    expect(kst(DateTime.parse('2026-10-10T16:00:00Z')).day, 11, reason: 'UTC 16시 = KST 다음날 1시');
    expect(hhmm(DateTime(2026, 10, 10, 9, 5)), '09:05', reason: '시간대 없는 값은 이미 벽시계');
  });
}
