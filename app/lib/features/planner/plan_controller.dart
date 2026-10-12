import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:latlong2/latlong.dart';

import '../../core/api/api_client.dart';
import '../../core/api/guryong_api.dart';
import '../../core/models/plan.dart';
import '../../core/models/safety.dart';
import 'restore_ops.dart';

/// AI 가 바꾼 항목 표시 (08_Planner_After_Apply 의 "✨ AI updated 14:00 → 11:00").
class AiUpdate {
  AiUpdate({required this.kind, this.fromTime, this.toTime, required this.reason});
  final String kind; // moved | updated | added
  final String? fromTime;
  final String? toTime;
  final String reason;
}

/// 마지막으로 승인한 AI 제안. 실행 취소 · 지도 비교(이전 경로, 제거된 장소)에 쓴다.
class AppliedProposal {
  AppliedProposal({required this.proposal, required this.before, required this.at});
  final ProposalDetail proposal;
  final PlanDetail before;
  final DateTime at;

  /// 제안 카드 기준 변경 건수 (항목 단위).
  int get changeCount => proposal.changes.where((c) => c.itemId != null).map((c) => c.itemId).toSet().length;

  List<PlanItem> removedItems(PlanDetail current) => before.allItems.where((i) => current.item(i.id) == null).toList();
}

/// 두 장소 사이 실제 길 (routing geometry). 같은 구간·수단은 한 번만 묻는다.
class RouteCache {
  RouteCache(this.api);
  final GuryongApi api;
  final _cache = <String, Future<RouteResult?>>{};

  static String key(String mode, LatLng a, LatLng b, [String? hazards]) =>
      '$mode:${a.latitude.toStringAsFixed(5)},${a.longitude.toStringAsFixed(5)};${b.latitude.toStringAsFixed(5)},${b.longitude.toStringAsFixed(5)}:${hazards ?? ''}';

  /// 실패하면 null (화면은 직선 + "약"으로 그린다).
  Future<RouteResult?> leg(String mode, LatLng a, LatLng b, {String? hazards}) => _cache.putIfAbsent(
        key(mode, a, b, hazards),
        () => api.route(mode, [(a.latitude, a.longitude), (b.latitude, b.longitude)], hazards: hazards).then<RouteResult?>((r) => r).catchError((_) => null),
      );
}

/// 한 계획의 상태. Planner 탭과 Map 탭이 같은 인스턴스를 공유한다 (선택 항목·Day·AI 변경 표시·안전 점검).
class PlanController extends ChangeNotifier {
  PlanController(this.api, this.planId, {this.aiMarkTtl = const Duration(minutes: 10)}) : routes = RouteCache(api);

  /// AI 변경 표시(항목 배지 · 초록 "반영됐어요" 배너 · 지도 [변경 내역])는 마지막 AI 변경에서 이만큼 지나면 사라진다.
  final Duration aiMarkTtl;
  Timer? _aiMarkTimer;

  final GuryongApi api;
  final int planId;
  final RouteCache routes;

  PlanDetail? plan;
  Object? loadError;
  bool loading = false;
  bool busy = false;

  int day = 1;
  int? selectedItemId;
  int tab = 0; // 0 Planner, 1 Map, 2 Today (동행 모드)

  /// 동행 중인지 (셸이 TripController 상태로 맞춘다). Planner ⋯ 메뉴의 여행 시작/종료.
  bool tripActive = false;
  Future<void> Function()? onStartTrip;
  Future<void> Function()? onEndTrip;
  AppliedProposal? lastApplied;

  /// 안전 점검 (25). 일정 version·progress_version 이 바뀌면 다시 부른다.
  SafetyCheck? safety;
  ApiException? safetyError;
  bool safetyLoading = false;
  List<SafetyAlert> alerts = const [];
  int? _safetyFor; // 점검한 (version, progress) 해시
  DateTime? _safetyAt;

  /// 항목 id → AI 변경 표시. 사용자가 그 항목을 직접 고치거나 [aiMarkTtl] 이 지나면 사라진다.
  final aiUpdates = <int, AiUpdate>{};

  /// 셸이 등록한다. AI 시트를 열고(필요하면 문장을 넣어) 바로 보낼지 정한다.
  void Function({String? prefill, bool send})? openAiHandler;

  void openAi({String? prefill, bool send = false}) => openAiHandler?.call(prefill: prefill, send: send);

  bool _disposed = false;

  @override
  void dispose() {
    _disposed = true;
    _aiMarkTimer?.cancel();
    super.dispose();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  PlanDay? get currentDay => plan?.dayOf(day);

  Future<void> load({bool silent = false}) async {
    if (!silent) {
      loading = true;
      loadError = null;
      _notify();
    }
    try {
      final p = await api.plan(planId);
      // 늦게 도착한 옛 GET 이 그사이 받은 새 일정(승인·편집 응답)을 덮지 않게 (audit-2026-10-12 F8).
      // version·progress_version 은 올라가기만 하므로 어느 하나라도 지금보다 작으면 옛 응답이다.
      if (!isStale(p, plan)) _set(p);
      loadError = null;
    } catch (e) {
      loadError = e;
      if (silent) rethrow;
    } finally {
      loading = false;
      _notify();
    }
  }

  /// [got] 이 [cur] 보다 옛 응답인지 (같은 계획에서 version 또는 progress_version 이 작다).
  static bool isStale(PlanDetail got, PlanDetail? cur) =>
      cur != null && got.id == cur.id && (got.version < cur.version || got.progressVersion < cur.progressVersion);

  /// 일정이 바뀌었는지(version) 또는 진행 상태만 바뀌었는지(progress_version) 보고, 바뀌었으면 안전 점검도 다시 (c12).
  void _set(PlanDetail p) {
    plan = p;
    if (day > p.numDays) day = p.numDays;
    if (selectedItemId != null && p.item(selectedItemId!) == null) selectedItemId = null;
    aiUpdates.removeWhere((id, _) => p.item(id) == null);
    final key = Object.hash(p.version, p.progressVersion, p.itemCount);
    if (key != _safetyFor) {
      _safetyFor = key;
      loadSafety();
    }
  }

  /// 승인·추천 추가처럼 다른 경로로 받은 최신 일정을 반영한다.
  void applyPlan(PlanDetail p) {
    _set(p);
    _notify();
  }

  /// 안전 점검과 새 경고. 503 safety_unavailable 이면 배지를 비우고 헤더 줄로 알린다 (25 상태).
  Future<void> loadSafety() async {
    if (_disposed) return;
    safetyLoading = true;
    _notify();
    try {
      safety = await api.safety(planId);
      safetyError = null;
      _safetyAt = DateTime.now();
    } on ApiException catch (e) {
      safetyError = e;
      safety = null;
    } catch (_) {
      safety = null;
    }
    try {
      alerts = await api.safetyAlerts(planId);
    } catch (_) {
      alerts = const [];
    }
    safetyLoading = false;
    _notify();
  }

  /// checked_at 이 10분 넘으면 다시 (25 데이터 갱신).
  void refreshSafetyIfStale() {
    final at = _safetyAt;
    if (at == null || DateTime.now().difference(at) > const Duration(minutes: 10)) loadSafety();
  }

  /// 배너를 보였으면 경고를 확인 표시한다.
  Future<void> markAlertSeen(SafetyAlert a) async {
    try {
      await api.safetyAlertSeen(planId, a.id);
    } catch (_) {}
  }

  void setDay(int d) {
    if (d == day) return;
    day = d;
    selectedItemId = null;
    _notify();
  }

  void select(int? itemId) {
    selectedItemId = itemId;
    final it = itemId == null ? null : plan?.item(itemId);
    if (it != null) day = it.day;
    _notify();
  }

  void setTab(int t) {
    tab = t;
    _notify();
  }

  /// Planner 카드 → 지도에서 해당 핀 focus.
  void showOnMap(int itemId) {
    select(itemId);
    setTab(1);
  }

  /// Map 시트를 펼쳐 열 항목 (Today 지도 작은 창 [자세히]). Map 이 한 번 읽고 지운다.
  int? mapDetailRequest;

  /// Today 작은 창 [자세히] → Map 탭에서 그 장소 시트를 펼친다 (탭이 바뀌는 유일한 곳, redesign §2.2).
  void showDetailOnMap(int itemId) {
    mapDetailRequest = itemId;
    showOnMap(itemId);
  }

  /// 지도 → Planner 에서 해당 카드 하이라이트.
  void showInPlanner(int itemId) {
    select(itemId);
    setTab(0);
  }

  /// 사용자가 직접 바꾸는 요청. 바뀐 항목의 AI 배지는 지운다("내가 수정").
  Future<void> _mutate(Future<PlanDetail> Function() call, {int? touchedItemId}) async {
    busy = true;
    _notify();
    try {
      _set(await call());
      if (touchedItemId != null) aiUpdates.remove(touchedItemId);
    } finally {
      busy = false;
      _notify();
    }
  }

  Future<void> addPlace(String placeId, {int? day, int stayMinutes = 60, int? order, String? startTime}) =>
      _mutate(() => api.addItem(planId, {
            'place_id': placeId,
            'day': day ?? this.day,
            'stay_minutes': stayMinutes,
            'order': ?order,
            'start_time': ?startTime,
          }));

  Future<void> updateItem(int itemId, Map<String, dynamic> fields) =>
      _mutate(() => api.updateItem(planId, itemId, fields), touchedItemId: itemId);

  /// 25 Travel Segment 탭 → 이 구간 수단 바꾸기 (직접 편집, 즉시).
  Future<void> setTravelMode(int itemId, String mode) => updateItem(itemId, {'travel_mode': mode});

  Future<void> removeItem(int itemId) => _mutate(() => api.removeItem(planId, itemId), touchedItemId: itemId);

  /// 드래그로 순서 변경. 서버가 이동시간을 다시 계산한다.
  Future<void> reorder(int itemId, int newOrder) {
    // 결과를 기다리는 동안 화면이 튀지 않도록 로컬에서 먼저 바꿔 둔다.
    final p = plan, d = currentDay;
    if (p != null && d != null) {
      final items = [...d.items];
      final idx = items.indexWhere((i) => i.id == itemId);
      if (idx >= 0) {
        final moved = items.removeAt(idx);
        items.insert(newOrder - 1, moved);
        plan = p.withDay(PlanDay(day: d.day, date: d.date, weekday: d.weekday, endAt: d.endAt, items: items));
        _notify();
      }
    }
    return _mutate(() => api.updateItem(planId, itemId, {'order': newOrder}), touchedItemId: itemId).catchError((Object e) async {
      await load(silent: true).catchError((_) {});
      throw e;
    });
  }

  Future<void> updatePlan(Map<String, dynamic> fields) => _mutate(() => api.updatePlan(planId, fields));

  Future<void> addConstraint(Map<String, dynamic> body) => _mutate(() => api.addConstraint(planId, body));

  Future<void> removeConstraint(int id) => _mutate(() => api.removeConstraint(planId, id));

  /// AI 제안 승인 결과 반영. 바뀐 항목에 AI 배지를 붙인다.
  void applyAccepted(ProposalDetail proposal, PlanDetail after) {
    final before = plan ?? proposal.before;
    if (before != null) {
      final updates = <int, AiUpdate>{};
      final reasons = <int, String>{};
      for (final c in proposal.changes) {
        if (c.itemId != null && (c.kind == 'moved' || c.kind == 'updated')) {
          reasons[c.itemId!] = [if (reasons[c.itemId!] != null) reasons[c.itemId!]!, c.message].join('\n');
        }
      }
      for (final id in reasons.keys) {
        final b = before.item(id), a = after.item(id);
        if (a == null) continue;
        updates[id] = AiUpdate(kind: 'moved', fromTime: b?.startAt, toTime: a.startAt, reason: reasons[id]!);
      }
      for (final a in after.allItems) {
        if (before.item(a.id) == null) {
          updates[a.id] = AiUpdate(kind: 'added', toTime: a.startAt, reason: '추가: ${a.title}');
        }
      }
      lastApplied = AppliedProposal(proposal: proposal, before: before, at: DateTime.now());
      aiUpdates
        ..clear()
        ..addAll(updates);
      final first = updates.keys.isEmpty ? null : after.item(updates.keys.first);
      if (first != null) day = first.day;
      _restartAiMarkTimer();
    }
    _set(after);
    _notify();
  }

  /// 승인 직후 "실행 취소" (스낵바 4초 동안만, c9). 승인 전 일정과의 차이를 ops 로 되돌린다.
  /// 한계: 삭제됐던 항목은 새 id 로 다시 만들어지고, 그 사이 다른 변경이 있으면 함께 되돌아간다.
  /// 다녀온(done) 항목이 바뀌는 되돌리기는 막는다 (동행 중 진행 기록을 지우지 않게).
  Future<void> undoLastApplied() async {
    final applied = lastApplied, current = plan;
    if (applied == null || current == null) return;
    final ops = buildRestoreOps(applied.before, current);
    final touchesDone = ops.any((o) {
      final id = o['item_id'];
      return id is int && current.item(id)?.status == 'done';
    });
    if (touchesDone) throw ApiException(409, '다녀온 곳이 바뀌어 되돌릴 수 없어요');
    if (ops.isEmpty) {
      lastApplied = null;
      aiUpdates.clear();
      _notify();
      return;
    }
    await _mutate(() => api.applyOps(planId, ops));
    lastApplied = null;
    aiUpdates.clear();
    _notify();
  }

  final _impact = <int, Future<(int, int)?>>{};

  /// 22 영향 박스 "위험한 곳 n → m": 지금 점검(남은 방문)과 ops 미리보기의 주의·위험 방문 수. 제안마다 한 번.
  Future<(int, int)?> safetyImpact(ProposalDetail p) => _impact.putIfAbsent(p.id, () async {
        try {
          int risky(SafetyCheck s) => s.items.where((i) => i.isVisit && i.elevated && plan?.item(i.itemId)?.status != 'done').length;
          final before = safety ?? await api.safety(planId);
          final after = await api.safetyPreview(planId, p.ops);
          return (risky(before), after.items.where((i) => i.isVisit && i.elevated && (i.itemId < 0 || plan?.item(i.itemId)?.status != 'done')).length);
        } catch (_) {
          return null;
        }
      });

  void markAiAdded(int itemId, String reason) {
    aiUpdates[itemId] = AiUpdate(kind: 'added', toTime: plan?.item(itemId)?.startAt, reason: reason);
    _restartAiMarkTimer();
    _notify();
  }

  void _restartAiMarkTimer() {
    _aiMarkTimer?.cancel();
    _aiMarkTimer = Timer(aiMarkTtl, () {
      _aiMarkTimer = null;
      if (lastApplied == null && aiUpdates.isEmpty) return;
      lastApplied = null;
      aiUpdates.clear();
      _notify();
    });
  }

  void clearApplied() {
    lastApplied = null;
    _notify();
  }
}
