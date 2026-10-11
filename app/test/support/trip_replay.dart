// 여행 재생 fixture(test/fixtures/trip/scene*.json)를 데모 위치 모드처럼 흘려 동행 화면을 검증한다 (docs/app/fixtures.md §4.3).
// 앱은 위치를 보내지 않고, 재생 시각까지의 서버 상태(state)·이벤트(폴링 응답)·선제 세션을 가짜 게이트웨이가 돌려준다.
import 'dart:async';
import 'dart:convert';

import 'package:guryong_mate/features/trip/location.dart';

import 'fixture_gateway.dart';
import 'harness.dart';

Map<String, dynamic> _copy(Object? v) => jsonDecode(jsonEncode(v)) as Map<String, dynamic>;

class TripReplay {
  TripReplay(this.scene, {List<String> flows = const []})
      : fx = loadFixture('trip/$scene.json'),
        gw = FixtureGateway(flows) {
    plan = _copy(fx['plan']);
    planId = fx['plan_id'] as int;
    sessionId = fx['session_id'] as String;
    state = inactiveTripState(planId, now: '${(timeline.first['at'] as String).substring(0, 19)}+09:00');
    _install();
  }

  final String scene;
  final Map<String, dynamic> fx;
  final FixtureGateway gw;
  late final int planId;
  late final String sessionId;
  late Map<String, dynamic> plan;
  late Map<String, dynamic> state;
  final events = <int, Map<String, dynamic>>{};
  Map<String, dynamic>? session;
  int cursor = -1;

  /// 서버 응답을 바꿔 끼울 수 있게 (예: 노트 404).
  Res Function(Req r)? noteOverride;

  List<Map<String, dynamic>> get timeline => (fx['timeline'] as List).cast<Map<String, dynamic>>();

  String _hhmmss(Map<String, dynamic> e) => (e['at'] as String).substring(11, 19);

  /// 재생 시각 [until] ("HH:MM:SS") 까지의 동작을 적용한다.
  void advanceTo(String until) {
    while (cursor + 1 < timeline.length && _hhmmss(timeline[cursor + 1]).compareTo(until) <= 0) {
      _apply(timeline[++cursor]);
    }
  }

  /// [type] 이벤트가 (agent 까지) 나올 때까지.
  void advanceUntilEvent(String type, {bool agent = false}) {
    while (cursor + 1 < timeline.length) {
      _apply(timeline[++cursor]);
      if (events.values.any((e) => e['type'] == type && (!agent || e['agent'] != null))) return;
    }
    throw StateError('$scene 에 $type 이벤트 없음');
  }

  void _apply(Map<String, dynamic> e) {
    final r = e['response'];
    switch (e['kind']) {
      case 'location' || 'tick':
        if (r is Map && r['state'] != null) state = _copy(r['state']);
        for (final ev in (r is Map ? r['events'] as List? : null) ?? const []) {
          _event(_copy(ev));
        }
      case 'poll':
        for (final ev in (r as List)) {
          _event(_copy(ev));
        }
      case 'http':
        final route = e['route'] as String;
        if (route.endsWith('/start') && r is Map) state = _copy(r);
        if (e['for_event'] != null) session = _copy(r);
        if (route.endsWith('/accept') && r is Map && r['plan'] != null) {
          final keep = {for (final i in _items) i['id']: i};
          plan = _copy(r['plan']);
          for (final i in _items) {
            final old = keep[i['id']];
            if (old != null && i['status'] == 'planned' && old['status'] != 'planned') {
              i['status'] = old['status'];
              i['arrived_at'] = old['arrived_at'];
              i['done_at'] = old['done_at'];
            }
          }
        }
    }
  }

  Iterable<Map<String, dynamic>> get _items => (plan['days'] as List).expand((d) => (d as Map)['items'] as List).cast<Map<String, dynamic>>();

  /// 이벤트를 기억하고, 도착·출발이면 planner 진행 상태처럼 항목을 바꾼다 (planner §8.1).
  void _event(Map<String, dynamic> ev) {
    final id = ev['id'] as int;
    final old = events[id];
    events[id] = ev;
    if (old != null) return;
    final itemId = (ev['payload'] as Map?)?['item_id'];
    final at = (ev['now'] as String).substring(11, 16);
    for (final i in _items) {
      if (i['id'] != itemId) continue;
      if (ev['type'] == 'arrived') {
        i['status'] = 'visiting';
        i['arrived_at'] = at;
      } else if (ev['type'] == 'departed') {
        i['status'] = 'done';
        i['done_at'] = at;
      }
    }
    plan['progress_version'] = (plan['progress_version'] as int? ?? 0) + 1;
  }

  Map<String, dynamic>? flowResponse(String name) => gw.named[name]?['response'] as Map<String, dynamic>?;

  void _install() {
    gw.on('GET /planner/plans/{id}', (_) => Res.json(200, plan));
    gw.on('GET /trip/plans/{id}/state', (_) => Res.json(200, state));
    gw.on('GET /trip/plans/{id}/events', (r) {
      final after = int.tryParse(r.query['after'] ?? '0') ?? 0;
      final limit = int.tryParse(r.query['limit'] ?? '50') ?? 50;
      final list = events.values.where((e) => (e['id'] as int) > after).toList()..sort((a, b) => (a['id'] as int).compareTo(b['id'] as int));
      return Res.json(200, list.take(limit).toList());
    });
    gw.on('POST /trip/plans/{id}/events/{eid}/seen', (r) {
      final id = int.parse(r.path.split('/')[5]);
      final e = events[id];
      if (e == null) return Res.error(404, 'event_not_found');
      e['seen_at'] = state['now'];
      return Res.json(200, e);
    });
    gw.on('POST /trip/plans/{id}/end', (_) {
      state = {...state, 'active': false, 'ended_reason': 'user', 'ended_at': state['now']};
      return Res.json(200, state);
    });
    gw.on('GET /agent/sessions', (_) => Res.json(200, [
          {'id': sessionId, 'plan_id': planId, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z'}
        ]));
    // 세션 기록은 재생 시각의 것 (선제 턴 뒤면 그 세션, 그 전에는 빈 대화)
    gw.on('GET /agent/sessions/{sid}', (_) => Res.json(200, session ?? {'id': sessionId, 'plan_id': planId, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z', 'messages': []}));
    gw.on('GET /planner/plans/{id}/proposals/{pid}', (r) {
      final pid = int.parse(r.path.split('/').last);
      for (final m in (session?['messages'] as List?) ?? const []) {
        for (final c in ((m as Map)['cards'] as List?) ?? const []) {
          final p = (c as Map)['proposal'];
          if (p is Map && p['id'] == pid) return Res.json(200, p);
        }
      }
      return Res.error(404, 'proposal_not_found');
    });
    if (!gw.queues.containsKey('GET /planner/plans/{plan_id}/safety')) gw.on('GET /planner/plans/{id}/safety', (_) => Res.error(503, 'safety_unavailable'));
    gw.on('GET /planner/plans/{id}/safety/alerts', (_) => Res.json(200, []));
    gw.on('GET /safety/avoid', (_) => Res.json(200, flowResponse('avoid_zones') ?? {'now': state['now'], 'min_risk': 'caution', 'zones': []}));
    gw.on('POST /routing/route', (r) => (r.body as Map)['hazards'] == 'avoid' && gw.named.containsKey('detour_route') ? Res.json(200, flowResponse('detour_route')) : Res.error(503, 'routing_unavailable'));
    if (!gw.queues.containsKey('GET /docent/memory')) gw.on('GET /docent/memory', (_) => Res.json(200, {'items': [], 'memory_version': 1, 'style_length': null}));
    gw.on('GET /docent/plans/{id}/notes/{place}', (r) {
      if (noteOverride != null) return noteOverride!(r);
      final n = flowResponse('note');
      return n == null ? Res.error(404, 'note_not_found') : Res.json(200, n);
    });
    gw.on('GET /docent/plans/{id}/notes', (_) => Res.json(200, []));
  }
}

/// 가짜 위치: 권한 상태와 위치를 테스트가 정한다.
class FakeLocationSource implements LocationSource {
  FakeLocationSource({this.access = LocationAccess.granted, this.afterRequest});
  LocationAccess access;
  LocationAccess? afterRequest;
  final controller = StreamController<LocationFix>.broadcast();
  int requests = 0;

  @override
  Future<LocationAccess> check() async => access;

  @override
  Future<LocationAccess> request() async {
    requests++;
    if (afterRequest != null) access = afterRequest!;
    return access;
  }

  @override
  Stream<LocationFix> positions() => controller.stream;

  @override
  Future<void> openSettings(LocationAccess access) async {}

  void emit(double lat, double lng, {double accuracy = 10}) => controller.add(LocationFix(lat, lng, accuracy: accuracy));
}
