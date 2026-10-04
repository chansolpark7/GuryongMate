import 'package:flutter/foundation.dart';

import '../../core/api/guryong_api.dart';
import '../../core/models/plan.dart';
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
  AppliedProposal({required this.proposal, required this.before});
  final ProposalDetail proposal;
  final PlanDetail before;

  /// 제안 카드 기준 변경 건수 (항목 단위).
  int get changeCount => proposal.changes.where((c) => c.itemId != null).map((c) => c.itemId).toSet().length;

  List<PlanItem> removedItems(PlanDetail current) => before.allItems.where((i) => current.item(i.id) == null).toList();
}

/// 한 계획의 상태. Planner 탭과 Map 탭이 같은 인스턴스를 공유한다 (선택 항목·Day·AI 변경 표시).
class PlanController extends ChangeNotifier {
  PlanController(this.api, this.planId);

  final GuryongApi api;
  final int planId;

  PlanDetail? plan;
  Object? loadError;
  bool loading = false;
  bool busy = false;

  int day = 1;
  int? selectedItemId;
  int tab = 0; // 0 Planner, 1 Map
  AppliedProposal? lastApplied;

  /// 항목 id → AI 변경 표시. 사용자가 그 항목을 직접 고치면 사라진다.
  final aiUpdates = <int, AiUpdate>{};

  /// 셸이 등록한다. AI 시트를 열고(필요하면 문장을 넣어) 바로 보낼지 정한다.
  void Function({String? prefill, bool send})? openAiHandler;

  void openAi({String? prefill, bool send = false}) => openAiHandler?.call(prefill: prefill, send: send);

  bool _disposed = false;

  @override
  void dispose() {
    _disposed = true;
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
      _set(await api.plan(planId));
      loadError = null;
    } catch (e) {
      loadError = e;
      if (silent) rethrow;
    } finally {
      loading = false;
      _notify();
    }
  }

  void _set(PlanDetail p) {
    plan = p;
    if (day > p.numDays) day = p.numDays;
    if (selectedItemId != null && p.item(selectedItemId!) == null) selectedItemId = null;
    aiUpdates.removeWhere((id, _) => p.item(id) == null);
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
        final days = p.days.map((x) => x.day == d.day ? PlanDay(day: d.day, date: d.date, weekday: d.weekday, endAt: d.endAt, items: items) : x).toList();
        plan = PlanDetail(id: p.id, title: p.title, startDate: p.startDate, endDate: p.endDate, dayStart: p.dayStart, dayEnd: p.dayEnd, travelMode: p.travelMode, memo: p.memo, createdAt: p.createdAt, updatedAt: p.updatedAt, itemCount: p.itemCount, days: days, constraints: p.constraints, violations: p.violations);
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
    final before = proposal.before ?? plan;
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
      lastApplied = AppliedProposal(proposal: proposal, before: before);
      aiUpdates
        ..clear()
        ..addAll(updates);
      final first = updates.keys.isEmpty ? null : after.item(updates.keys.first);
      if (first != null) day = first.day;
    }
    _set(after);
    _notify();
  }

  /// 승인 직후 "실행 취소": 승인 전 상태로 되돌린다.
  Future<void> undoLastApplied() async {
    final applied = lastApplied, current = plan;
    if (applied == null || current == null) return;
    final ops = buildRestoreOps(applied.before, current);
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

  /// 06B 추천 카드에서 하나씩 추가한 항목 표시.
  void markAiAdded(int itemId, String reason) {
    aiUpdates[itemId] = AiUpdate(kind: 'added', toTime: plan?.item(itemId)?.startAt, reason: reason);
    _notify();
  }

  void clearApplied() {
    lastApplied = null;
    _notify();
  }
}
