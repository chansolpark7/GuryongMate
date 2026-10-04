// 실제 백엔드(게이트웨이)에 붙여 앱의 API 계층 · 모델 파싱을 확인한다.
//   docker compose up -d   (저장소 루트)
//   flutter test test/live_backend_test.dart --dart-define=LIVE_API=http://127.0.0.1:8000
// LIVE_API 가 없으면 건너뛴다. 테스트용 사용자로 계획을 만들고 끝나면 지운다.
@Tags(['live'])
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/api/api_client.dart';
import 'package:guryong_mate/core/api/guryong_api.dart';
import 'package:guryong_mate/core/format.dart';
import 'package:guryong_mate/core/models/plan.dart';
import 'package:guryong_mate/features/planner/restore_ops.dart';

const live = String.fromEnvironment('LIVE_API');

void main() {
  final skip = live.isEmpty ? 'LIVE_API 가 없어 건너뜀' : null;
  final api = GuryongApi(ApiClient(baseUrl: live, userId: 'app-live-test-${DateTime.now().millisecondsSinceEpoch}'));
  PlanDetail? plan;

  tearDownAll(() async {
    if (plan != null) await api.deletePlan(plan!.id);
  });

  test('places: 검색 · 상세 · 태그 · 이야기', () async {
    final kw = await api.searchPlaces(q: '구룡포', mode: 'keyword', limit: 5);
    expect(kw, isNotEmpty);
    final hybrid = await api.searchPlaces(q: '과메기', limit: 5);
    expect(hybrid, isNotEmpty);
    final near = await api.searchPlaces(lat: 35.989, lng: 129.556, radiusM: 3000, limit: 10);
    expect(near.every((p) => p.distanceM != null && p.distanceM! <= 3000), isTrue);
    final detail = await api.place(kw.first.id);
    expect(detail.title, kw.first.title);
    expect(await api.tags(), isNotEmpty);
    final withStory = near.where((p) => p.id.isNotEmpty).toList();
    for (final p in withStory.take(5)) {
      final d = await api.place(p.id);
      if (d.storyCount > 0) {
        expect(await api.placeStories(p.id), hasLength(d.storyCount));
        break;
      }
    }
  }, skip: skip);

  test('planner: 생성 · 추가 · 순서 · 고정 · 규칙 · 위반 · 실행 취소 ops', () async {
    final places = await api.searchPlaces(area: '구룡포읍', limit: 4);
    expect(places.length, greaterThanOrEqualTo(3));
    final start = DateTime.now().add(const Duration(days: 7));
    plan = await api.createPlan({
      'title': '앱 통합 테스트',
      'start_date': isoDate(start),
      'end_date': isoDate(start.add(const Duration(days: 1))),
      'day_start': '10:00',
      'day_end': '18:00',
      'travel_mode': 'walk',
      'memo': '지역: 구룡포읍',
    });
    expect(plan!.numDays, 2);
    final id = plan!.id;

    for (final p in places.take(3)) {
      plan = await api.addItem(id, {'place_id': p.id, 'day': 1, 'stay_minutes': 60});
    }
    final day1 = plan!.dayOf(1)!;
    expect(day1.items.map((i) => i.placeId), places.take(3).map((p) => p.id));
    expect(day1.items.first.startAt, '10:00');
    expect(day1.items[1].travelMinutes, isNotNull);

    final before = plan!;
    final last = day1.items.last;
    plan = await api.updateItem(id, last.id, {'order': 1});
    expect(plan!.dayOf(1)!.items.first.id, last.id);
    plan = await api.updateItem(id, last.id, {'start_time': '11:00', 'stay_minutes': 90, 'memo': '메모'});
    expect(plan!.item(last.id)!.startTime, '11:00');
    plan = await api.updateItem(id, last.id, {'start_time': null});
    expect(plan!.item(last.id)!.startTime, isNull);
    plan = await api.updateItem(id, day1.items.first.id, {'day': 2});
    expect(plan!.dayOf(2)!.items, hasLength(1));

    plan = await api.addConstraint(id, {'type': 'end_before', 'params': {'time': '10:30'}});
    expect(plan!.violations.any((v) => v.code == 'end_before'), isTrue);
    plan = await api.removeConstraint(id, plan!.constraints.single.id);

    // 실행 취소: 지금 상태 → before 로 되돌리는 ops 가 서버에서 그대로 적용되는지
    final ops = buildRestoreOps(before, plan!);
    plan = await api.applyOps(id, ops);
    for (final d in before.days) {
      expect(plan!.dayOf(d.day)!.items.map((i) => (i.placeId, i.stayMinutes, i.startTime, i.memo)),
          d.items.map((i) => (i.placeId, i.stayMinutes, i.startTime, i.memo)));
    }

    final removed = plan!.dayOf(1)!.items.last;
    plan = await api.removeItem(id, removed.id);
    expect(plan!.item(removed.id), isNull);
    expect((await api.plans()).any((p) => p.id == id && p.itemCount == 2), isTrue);
  }, skip: skip);

  test('agent: 세션 생성 · 조회 · 대화 스트림', () async {
    final id = plan!.id;
    final s = await api.createSession(id);
    final detail = await api.session(s.id);
    expect(detail.messages, isEmpty);
    expect((await api.sessions(id)).map((x) => x.id), contains(s.id));

    final events = <String>[];
    String? error;
    await for (final ev in api.chat(s.id, '안녕').timeout(const Duration(minutes: 3))) {
      events.add(ev.event);
      if (ev.event == 'error') error = '${ev.data['message']}';
    }
    // LLM 이 설정돼 있지 않으면 error 이벤트로 끝나야 한다 (스트림이 그냥 끊기면 안 됨)
    printOnFailure('events=$events error=$error');
    expect(events.first, 'start');
    expect(events.last, anyOf('done', 'error'));
    // ignore: avoid_print
    print('agent 스트림: $events${error == null ? '' : ' / error: $error'}');
  }, skip: skip);
}
