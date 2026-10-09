// 동행 모드 로직 — 겹치는 이벤트 우선순위 (events.md §2), 폴링 주기 (§5), 위치 전송 (13, trip §2.2) · 가짜 시계.
import 'dart:async';
import 'dart:convert';

import 'package:flutter/widgets.dart' show AppLifecycleState;

import 'package:clock/clock.dart';
import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/api/api_client.dart';
import 'package:guryong_mate/core/models/trip.dart';
import 'package:guryong_mate/features/planner/plan_controller.dart';
import 'package:guryong_mate/features/trip/location.dart';
import 'package:guryong_mate/features/trip/trip_controller.dart';

import 'support/fixture_gateway.dart';
import 'support/harness.dart';
import 'support/trip_replay.dart';

/// 재생 fixture 의 이벤트 (타입별 첫 번째). agent 가 채워진 것을 고른다.
Map<String, dynamic> eventOf(TripReplay r, String type) {
  r.advanceTo('23:59:59');
  final list = r.events.values.where((e) => e['type'] == type).toList();
  return jsonDecode(jsonEncode(list.lastWhere((e) => e['agent'] != null || !(type == 'delay' || type == 'safety_raised')))) as Map<String, dynamic>;
}

final _live = <void Function()>[];

/// 타이머가 남지 않게 테스트 본문 끝에서 정리 (위젯 테스트는 본문이 끝나면 남은 타이머를 검사한다).
void finish() {
  for (final f in _live) {
    f();
  }
  _live.clear();
}

TripEvent ev(Map<String, dynamic> j, {int? id, String? type}) => TripEvent.fromJson({...j, 'id': ?id, 'type': ?type, 'seen_at': null});

Future<(TripController, TripReplay)> controller(WidgetTester tester, String scene, {bool demo = true, FakeLocationSource? source, String at = '12:30:30'}) async {
  final r = TripReplay(scene);
  r.advanceTo(at);
  final session = await signedInSession(r.gw, prefs: {'demo_location_mode': demo});
  final plan = PlanController(session.api, r.planId);
  await plan.load();
  final t = TripController(api: session.api, plan: plan, session: session, source: source ?? FakeLocationSource());
  _live.add(() {
    t.dispose();
    plan.dispose();
  });
  return (t, r);
}

void main() {
  setUpHarness();

  group('겹치는 이벤트 우선순위 (안전 > 일정 영향 > 안내)', () {
    testWidgets('도착 카드 → 지연(3단계)이 오면 카드는 칩으로, 안전이 오면 안전이 앞, 위험 구역은 따로 최상단', (tester) async {
      final r4 = TripReplay('scene4_safety');
      final arrived = eventOf(r4, 'arrived'), safety = eventOf(r4, 'safety_raised'), hazard = eventOf(r4, 'near_hazard');
      final delay = eventOf(TripReplay('scene3_delay'), 'delay');
      final (t, _) = await controller(tester, 'scene4_safety');

      t.ingest([ev(arrived, id: 900)]);
      expect(t.arrival?.id, 900);
      expect(t.arrivalCollapsed, isFalse);

      t.ingest([ev(delay, id: 901)]);
      await tester.pump();
      expect(t.preview?.id, 901, reason: '3단계 → 선제 미리보기');
      expect(t.arrivalCollapsed, isTrue, reason: '도착 카드는 "해설 듣기" 칩으로 접힌다');
      expect(t.hasSuggestion, isTrue);

      t.ingest([ev(safety, id: 902)]);
      await tester.pump();
      expect(t.preview?.id, 902, reason: '안전 > 일정 영향');

      t.ingest([ev(hazard, id: 903)]);
      expect(t.hazard?.id, 903, reason: '위험 구역 경고는 무엇이 떠 있든 최상단 (따로)');
      expect(t.preview?.id, 902, reason: '경고 패널 1 + 미리보기 1 은 동시에');

      // 안전 미리보기는 8초 뒤에도 접히지 않는다
      await tester.pump(const Duration(seconds: 9));
      expect(t.previewCollapsed, isFalse);
      // 닫으면 줄 서 있던 지연이 이어서
      t.dismissPreview();
      expect(t.preview?.id, 901);
      expect(t.previewCollapsed, isFalse);
      // 지연은 8초 뒤 접히고(점만 남음), 높은 것이 다 닫히면 도착 카드가 다시 펼쳐진다
      await tester.pump(const Duration(seconds: 9));
      expect(t.previewCollapsed, isTrue);
      expect(t.hasSuggestion, isTrue, reason: '점은 제안이 대기인 동안 남는다');
      expect(t.arrivalCollapsed, isFalse);
      finish();
    });

    testWidgets('토스트: 하나씩 4초, 같은 종류가 연달아 오면 마지막 것만 · 출발하면 그 장소 카드는 사라짐', (tester) async {
      final r = TripReplay('scene4_safety');
      final arrived = eventOf(r, 'arrived');
      final departed = {...eventOf(r, 'departed'), 'payload': {'item_id': (arrived['card'] as Map)['item_id'], 'title': 'x', 'stayed_minutes': 40}};
      final eased = {...departed, 'type': 'safety_eased', 'level': 1, 'message': '구룡포 북방파제 풍랑 주의가 해제됐어요', 'payload': <String, dynamic>{}};
      final (t, _) = await controller(tester, 'scene4_safety');
      t.ingest([ev(arrived, id: 800)]);
      t.ingest([ev(departed, id: 801, type: 'departed')]);
      expect(t.toast?.id, 801);
      expect(t.arrival, isNull, reason: '떠난 장소의 도착 카드는 사라진다');
      t.ingest([ev(eased, id: 802)]);
      expect(t.toast?.id, 801, reason: '다른 종류는 뒤에 줄 선다');
      t.ingest([ev(departed, id: 803)]);
      expect(t.toast?.id, 803, reason: '같은 종류는 마지막 것만');
      await tester.pump(const Duration(seconds: 4));
      expect(t.toast?.id, 802);
      await tester.pump(const Duration(seconds: 4));
      expect(t.toast, isNull);
      finish();
    });

    testWidgets('두 경로(위치 응답·폴링)로 같은 id 가 와도 한 번만 · 쌓여 있던 0~1단계는 목록에만', (tester) async {
      final r = TripReplay('scene4_safety');
      final departed = eventOf(r, 'departed'), hazard = eventOf(r, 'near_hazard');
      final (t, replay) = await controller(tester, 'scene4_safety');
      t.ingest([ev(departed, id: 700), ev(hazard, id: 701)], backlog: true);
      expect(t.toast, isNull, reason: '앱을 다시 열었을 때 지난 출발 토스트는 생략');
      expect(t.hazard, isNull, reason: '지난 위험 구역 경고는 다시 띄우지 않는다');
      expect(t.events.map((e) => e.id), containsAll([700, 701]), reason: '알림 목록에는 남는다');
      t.ingest([ev(hazard, id: 702)]);
      t.ingest([ev(hazard, id: 702)]);
      await tester.pump();
      expect(t.hazard?.id, 702);
      expect(replay.gw.sent('POST', '/trip/plans/{id}/events/{eid}/seen').where((q) => q.path.endsWith('/702/seen')).length, 1, reason: 'seen 한 번');
      finish();
    });
  });

  group('폴링 (가짜 시계)', () {
    testWidgets('동행 중 5초 · push 화면 10초 · 선제 턴 기다리는 동안 2초(60초까지) · 앱 복귀 즉시', (tester) async {
      final (t, r) = await controller(tester, 'scene3_delay', at: '13:50:00');
      int polls() => r.gw.sent('GET', '/trip/plans/{id}/events').length;
      await t.load();
      expect(t.active, isTrue);
      final base = polls();
      expect(base, 1, reason: '시작하면 1회 (쌓인 이벤트)');
      await tester.pump(const Duration(seconds: 5));
      expect(polls(), base + 1);
      await tester.pump(const Duration(seconds: 5));
      expect(polls(), base + 2);

      t.pushed(true); // 17 가이드 노트 등
      await tester.pump(const Duration(seconds: 5));
      await tester.pump(const Duration(seconds: 5));
      expect(polls(), base + 3, reason: 'push 화면: 10초');
      t.pushed(false);
      await tester.pump(const Duration(seconds: 10));

      r.advanceTo('13:53:00'); // delay queued (agent 없음)
      final before = polls();
      await tester.pump(const Duration(seconds: 5));
      expect(t.waitingAgent, isTrue);
      await tester.pump(const Duration(seconds: 2));
      await tester.pump(const Duration(seconds: 2));
      expect(polls(), before + 3, reason: '선제 턴을 기다리는 동안 2초');
      final q = r.gw.sent('GET', '/trip/plans/{id}/events').last.query;
      expect(int.parse(q['after']!), lessThan(350), reason: '기다리는 이벤트 앞부터 다시 읽어 agent 를 확인');
      await tester.pump(const Duration(seconds: 62));
      final late = polls();
      await tester.pump(const Duration(seconds: 2));
      expect(polls(), late, reason: '60초가 지나면 다시 5초');
      await tester.pump(const Duration(seconds: 3));
      expect(polls(), late + 1);

      r.advanceTo('14:00:00'); // agent 채워짐
      await tester.pump(const Duration(seconds: 5));
      expect(t.waitingAgent, isFalse);
      expect(t.preview?.type, 'delay');

      final s0 = r.gw.sent('GET', '/trip/plans/{id}/state').length, p0 = polls();
      t.didChangeAppLifecycleState(AppLifecycleState.resumed);
      await tester.pump();
      expect(r.gw.sent('GET', '/trip/plans/{id}/state').length, s0 + 1, reason: '포그라운드 복귀: state 즉시');
      expect(polls(), p0 + 1, reason: '포그라운드 복귀: 이벤트 즉시');
      await tester.pump(const Duration(seconds: 15));
      finish();
    });

    testWidgets('state 는 15초마다 (데모 위치 모드는 5초 — 재생기 위치를 따라 그린다)', (tester) async {
      final (t, r) = await controller(tester, 'scene2_arrival', demo: false);
      int states() => r.gw.sent('GET', '/trip/plans/{id}/state').length;
      await t.load();
      final s0 = states();
      await tester.pump(const Duration(seconds: 14));
      expect(states(), s0);
      await tester.pump(const Duration(seconds: 1));
      expect(states(), s0 + 1);
      await t.session.setDemoLocationMode(true);
      final s1 = states();
      await tester.pump(const Duration(seconds: 5));
      expect(states(), s1 + 1);
      expect(r.gw.sent('POST', '/trip/plans/{id}/location'), isEmpty, reason: '데모 위치 모드는 GPS 를 보내지 않는다');
      await t.session.setDemoLocationMode(false);
      finish();
    });
  });

  group('위치 전송 (가짜 시계)', () {
    LocationResult okResult() => LocationResult.fromJson({'state': inactiveTripState(1)..['active'] = true, 'events': [], 'accepted': 1, 'ignored': 0});

    test('30m 이상 움직이면 바로, 아니면 45초마다 · recorded_at 은 서버 시계', () {
      fakeAsync((fa) {
        final sent = <List<LocationPoint>>[];
        final serverOffset = const Duration(days: 1, hours: 2);
        final s = LocationSender(send: (p) async {
          sent.add(p);
          return okResult();
        }, serverNow: () => clock.now().add(serverOffset));
        final fixes = StreamController<LocationFix>();
        s.start(fixes.stream);
        fixes.add(LocationFix(35.9906, 129.5604, accuracy: 8));
        fa.flushMicrotasks();
        expect(sent.length, 1, reason: '첫 위치');
        expect(sent.last.single.recordedAt.difference(clock.now()).inHours, 26, reason: '가상 시각에 맞춘 recorded_at');
        fixes.add(LocationFix(35.99065, 129.56045)); // ~7m
        fa.flushMicrotasks();
        expect(sent.length, 1, reason: '30m 안이면 기다린다');
        fixes.add(LocationFix(35.9910, 129.5604)); // ~45m
        fa.flushMicrotasks();
        expect(sent.length, 2, reason: '30m 이상');
        fa.elapse(const Duration(seconds: 44));
        expect(sent.length, 2);
        fa.elapse(const Duration(seconds: 16));
        expect(sent.length, 3, reason: '45초가 지나면 그 자리라도 보낸다 (도착 판정 머무름 90초)');
        s.stop();
        fixes.close();
      });
    });

    test('오프라인이면 모아 두었다가 연결되면 {"points": […]} 한 번에 (오래된 순, 100개씩)', () {
      fakeAsync((fa) {
        var online = false;
        final sent = <List<LocationPoint>>[];
        final offline = <bool>[];
        final s = LocationSender(
          send: (p) async {
            if (!online) throw ApiException(0, 'offline', code: 'network');
            sent.add(p);
            return okResult();
          },
          serverNow: clock.now,
          onOffline: offline.add,
        );
        final fixes = StreamController<LocationFix>();
        s.start(fixes.stream);
        for (var i = 0; i < 130; i++) {
          fixes.add(LocationFix(35.98 + i * 0.001, 129.55)); // 매번 ~110m
          fa.flushMicrotasks();
          fa.elapse(const Duration(seconds: 1));
        }
        expect(sent, isEmpty);
        expect(offline.last, isTrue);
        expect(s.pending.length, 130);
        online = true;
        fa.elapse(const Duration(seconds: 15)); // 주기 점검에서 다시 보냄
        expect(sent.length, 2, reason: '100 + 30');
        expect(sent[0].length, 100);
        expect(sent[1].length, 30);
        expect(sent[0].first.recordedAt.isBefore(sent[0].last.recordedAt), isTrue, reason: '오래된 순');
        expect(sent[0].first.lat, closeTo(35.98, 1e-9));
        expect(offline.last, isFalse);
        expect(s.pending, isEmpty);
        s.stop();
        fixes.close();
      });
    });

    test('409 trip_not_active 면 멈추고 state 를 다시 읽게 한다', () {
      fakeAsync((fa) {
        var inactive = 0;
        final s = LocationSender(
          send: (p) async => throw ApiException(409, '여행 중이 아니에요', code: 'trip_not_active'),
          serverNow: clock.now,
          onInactive: () => inactive++,
        );
        final fixes = StreamController<LocationFix>();
        s.start(fixes.stream);
        fixes.add(LocationFix(35.99, 129.56));
        fa.flushMicrotasks();
        expect(inactive, 1);
        expect(s.running, isFalse);
        expect(s.pending, isEmpty);
        fixes.close();
      });
    });

    testWidgets('앱: GPS 위치 → POST location → 응답 이벤트를 바로 그린다 · 끊기면 14-S1 오프라인 → 다시 연결되면 points', (tester) async {
      final loc = FakeLocationSource();
      final (t, r) = await controller(tester, 'scene2_arrival', demo: false, source: loc, at: '12:30:00');
      final locs = r.timeline.where((e) => e['kind'] == 'location').toList();
      var i = 0;
      var down = false;
      r.gw.on('POST /trip/plans/{id}/location', (_) {
        if (down) throw Exception('연결 끊김'); // 네트워크 오류 → 오프라인
        final resp = locs[i++]['response'] as Map<String, dynamic>;
        r.state = resp['state'] as Map<String, dynamic>;
        for (final e in (resp['events'] as List)) {
          r.events[(e as Map)['id'] as int] = Map<String, dynamic>.from(e);
        }
        return Res.json(200, resp);
      });
      await t.load();
      await tester.pump();
      expect(t.sender?.running, isTrue);
      for (final e in locs.take(4)) {
        final b = e['body'] as Map;
        loc.emit((b['lat'] as num).toDouble(), (b['lng'] as num).toDouble());
        await tester.pump();
        await tester.pump(const Duration(seconds: 46));
      }
      expect(t.arrival?.type, 'arrived', reason: '위치 응답의 arrived → 바로 도착 카드 (폴링을 기다리지 않음)');

      down = true;
      final before = r.gw.sent('POST', '/trip/plans/{id}/location').length;
      loc.emit(35.995, 129.565);
      await tester.pump();
      loc.emit(35.999, 129.569);
      await tester.pump();
      expect(t.sender!.pending.length, 2);
      expect(t.offline, isTrue, reason: '14-S1 오프라인 띠');
      down = false;
      await tester.pump(const Duration(seconds: 15));
      final sent = r.gw.sent('POST', '/trip/plans/{id}/location');
      final last = sent.last.body as Map;
      expect(last['points'], isA<List<dynamic>>(), reason: '모아 둔 점은 points 로');
      expect((last['points'] as List).length, 2);
      expect(sent.length, greaterThan(before));
      finish();
    });
  });
}
