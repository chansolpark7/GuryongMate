import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../core/api/api_client.dart';
import '../../core/api/guryong_api.dart';
import '../../core/models/plan.dart';
import '../planner/plan_controller.dart';

/// 도구 이름 → 화면 문구. 함수명·API 표현은 노출하지 않는다 (Spec 05 카피 원칙).
const toolLabels = {
  'search_places': '장소 찾기',
  'get_place': '장소 정보 확인',
  'view_plan': '일정 확인',
  'add_item': '일정 추가',
  'update_item': '일정 조정',
  'remove_item': '일정 정리',
  'update_plan': '여행 정보 변경',
  'add_rule': '조건 추가',
  'remove_rule': '조건 삭제',
  'discard_changes': '변경안 초기화',
};

class ToolStep {
  ToolStep(this.id, this.name);
  final String id;
  final String name;
  bool? ok; // null = 진행 중
  String summary = '';

  String get label => toolLabels[name] ?? '작업 중';
}

enum ProposalState { pending, applying, applied, rejected, superseded }

/// 사용자 메시지 하나와 그에 대한 Agent 응답 (도구 진행 · 답변 · 제안 카드).
class ChatTurn {
  ChatTurn(this.user, {this.fromHistory = false});
  final String user;
  final bool fromHistory;
  final steps = <ToolStep>[];
  final text = StringBuffer();
  bool thinking = false;
  bool done = false;
  bool stopped = false;
  String? error;

  ProposalDetail? proposal;
  ProposalState proposalState = ProposalState.pending;

  /// 06B 추천 카드: add_item ops 순번 → 직접 추가된 일정 항목 id.
  final recommendationAdded = <int, int>{};
  final recommendationBusy = <int>{};

  String get activitySummary {
    final labels = <String>{for (final s in steps) s.label};
    return '${steps.length}단계 완료 · ${labels.join(', ')}';
  }
}

/// AI Planner 대화. 계획 하나당 세션 하나 (여행 전·중 맥락 유지).
class ChatController extends ChangeNotifier {
  ChatController(this.api, this.plan);

  final GuryongApi api;
  final PlanController plan;

  String? sessionId;
  bool initializing = false;
  Object? initError;
  final turns = <ChatTurn>[];
  bool busy = false;
  StreamSubscription<SseEvent>? _sub;
  bool _disposed = false;

  @override
  void dispose() {
    _disposed = true;
    _sub?.cancel();
    super.dispose();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void>? _initFuture;

  /// 최근 세션을 이어서 연다. 없으면 만든다.
  /// 동시에 여러 번 불려도(셸 · 시트 · 첫 전송) 같은 작업을 기다리게 한다. 실패하면 다음 호출에서 다시 시도한다.
  Future<void> init() {
    if (sessionId != null) return Future.value();
    return _initFuture ??= _init().whenComplete(() => _initFuture = null);
  }

  Future<void> _init() async {
    initializing = true;
    initError = null;
    _notify();
    try {
      final list = await api.sessions(plan.planId);
      final id = list.isNotEmpty ? list.first.id : (await api.createSession(plan.planId)).id;
      final detail = await api.session(id);
      sessionId = id;
      turns
        ..clear()
        ..addAll(_turnsFromHistory(detail.messages.map((m) => (m.role, m.content, m.proposalId)).toList()));
      final pending = detail.pendingProposal;
      if (pending != null) {
        final owner = turns.lastWhere((t) => t.proposal?.id == pending.id, orElse: () => turns.isEmpty ? ChatTurn('') : turns.last);
        if (!turns.contains(owner)) turns.add(owner);
        owner
          ..proposal = pending
          ..proposalState = ProposalState.pending;
      }
    } catch (e) {
      initError = e;
    } finally {
      initializing = false;
      _notify();
    }
  }

  List<ChatTurn> _turnsFromHistory(List<(String, String, int?)> messages) {
    final out = <ChatTurn>[];
    for (final (role, content, proposalId) in messages) {
      if (role == 'user' || out.isEmpty) {
        out.add(ChatTurn(role == 'user' ? content : '', fromHistory: true)..done = true);
        if (role == 'user') continue;
      }
      final t = out.last;
      if (t.text.isNotEmpty) t.text.write('\n\n');
      t.text.write(content);
      if (proposalId != null) _loadHistoryProposal(t, proposalId);
    }
    return out;
  }

  /// 지난 제안은 상태만 보여 준다 (적용됨 / 거절됨 / 대체됨).
  Future<void> _loadHistoryProposal(ChatTurn t, int id) async {
    try {
      final p = await api.proposal(plan.planId, id);
      if (t.proposal != null && t.proposal!.status == 'pending') return; // 대기 중 제안이 이미 붙음
      t.proposal = p;
      t.proposalState = switch (p.status) {
        'accepted' => ProposalState.applied,
        'rejected' => ProposalState.rejected,
        'superseded' => ProposalState.superseded,
        _ => ProposalState.pending,
      };
      _notify();
    } catch (_) {}
  }

  /// 새 대화 시작 (대기 중인 제안은 planner 에 그대로 남는다).
  Future<void> newSession() async {
    if (busy) return;
    try {
      final s = await api.createSession(plan.planId);
      sessionId = s.id;
      turns.clear();
      _notify();
    } catch (e) {
      initError = e;
      _notify();
    }
  }

  Future<void> send(String message) async {
    final text = message.trim();
    if (text.isEmpty || busy) return;
    if (sessionId == null) await init();
    final sid = sessionId;
    if (sid == null) return;
    final turn = ChatTurn(text);
    turns.add(turn);
    busy = true;
    _notify();
    _sub = api.chat(sid, text).listen(
      (ev) => _onEvent(turn, ev),
      onError: (Object e) {
        turn.error = e is ApiException ? e.message : '$e';
        _finish(turn);
      },
      onDone: () => _finish(turn),
      cancelOnError: true,
    );
  }

  void _onEvent(ChatTurn turn, SseEvent ev) {
    final d = ev.data;
    switch (ev.event) {
      case 'thinking':
        turn.thinking = true;
      case 'text':
        turn.thinking = false;
        turn.text.write(d['delta'] ?? '');
      case 'tool_call':
        turn.thinking = false;
        turn.steps.add(ToolStep('${d['id']}', '${d['name']}'));
      case 'tool_result':
        for (final s in turn.steps) {
          if (s.id == '${d['id']}') {
            s.ok = d['ok'] == true;
            s.summary = '${d['summary'] ?? ''}';
          }
        }
      case 'proposal':
        final p = ProposalDetail.fromJson(d);
        for (final t in turns) {
          if (t != turn && t.proposal != null && t.proposalState == ProposalState.pending) {
            t.proposalState = ProposalState.superseded; // 이어서 말하면 이전 제안은 대체된다
          }
        }
        turn
          ..proposal = p
          ..proposalState = ProposalState.pending;
      case 'done':
        final full = '${d['text'] ?? ''}';
        if (turn.text.isEmpty && full.isNotEmpty) turn.text.write(full);
      case 'error':
        turn.error = '${d['message'] ?? '오류가 발생했습니다.'}';
    }
    _notify();
  }

  void _finish(ChatTurn turn) {
    turn
      ..done = true
      ..thinking = false;
    for (final s in turn.steps) {
      s.ok ??= false;
    }
    busy = false;
    _sub = null;
    _notify();
  }

  /// 실행 중 Stop. 연결을 끊으면 서버가 턴을 정리한다.
  Future<void> stop() async {
    final turn = turns.isEmpty ? null : turns.last;
    await _sub?.cancel();
    if (turn != null) {
      turn.stopped = true;
      _finish(turn);
    } else {
      busy = false;
      _notify();
    }
  }

  Future<void> accept(ChatTurn turn) async {
    final p = turn.proposal, sid = sessionId;
    if (p == null || sid == null) return;
    turn.proposalState = ProposalState.applying;
    _notify();
    try {
      final after = await api.acceptProposal(sid, p.id);
      plan.applyAccepted(p, after);
      turn.proposalState = ProposalState.applied;
    } catch (e) {
      turn.proposalState = ProposalState.pending;
      _notify();
      rethrow;
    }
    _notify();
  }

  Future<void> reject(ChatTurn turn) async {
    final p = turn.proposal, sid = sessionId;
    if (p == null || sid == null) return;
    await api.rejectProposal(sid, p.id);
    turn.proposalState = ProposalState.rejected;
    _notify();
  }

  /// 06B: 추천 장소를 하나씩 일정에 넣는다. 묶음 제안은 거절 처리해 Agent 가 이어받지 않게 하고,
  /// 고른 장소만 planner 직접 편집으로 추가한다.
  Future<void> addRecommendation(ChatTurn turn, int opIndex) async {
    final p = turn.proposal, sid = sessionId;
    if (p == null || sid == null) return;
    final op = p.ops[opIndex];
    turn.recommendationBusy.add(opIndex);
    _notify();
    try {
      if (turn.proposalState == ProposalState.pending) {
        await api.rejectProposal(sid, p.id);
        turn.proposalState = ProposalState.rejected;
      }
      final before = {for (final i in plan.plan?.allItems ?? const <PlanItem>[]) i.id};
      await plan.addPlace(
        '${op['place_id']}',
        day: op['day'] as int? ?? plan.day,
        stayMinutes: op['stay_minutes'] as int? ?? 60,
        order: op['order'] as int?,
        startTime: op['start_time'] as String?,
      );
      final added = plan.plan?.allItems.where((i) => !before.contains(i.id)).firstOrNull;
      if (added != null) {
        turn.recommendationAdded[opIndex] = added.id;
        plan.markAiAdded(added.id, 'AI 추천: ${added.title}');
      }
    } finally {
      turn.recommendationBusy.remove(opIndex);
      _notify();
    }
  }

  Future<void> cancelRecommendation(ChatTurn turn, int opIndex) async {
    final id = turn.recommendationAdded[opIndex];
    if (id == null) return;
    turn.recommendationBusy.add(opIndex);
    _notify();
    try {
      await plan.removeItem(id);
      turn.recommendationAdded.remove(opIndex);
    } finally {
      turn.recommendationBusy.remove(opIndex);
      _notify();
    }
  }
}
