import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../core/api/api_client.dart';
import '../../core/api/guryong_api.dart';
import '../../core/models/agent.dart';
import '../../core/models/plan.dart';
import '../../core/models/trip.dart';
import '../planner/plan_controller.dart';

enum ProposalState { pending, applying, applied, rejected, superseded }

ProposalState stateOf(ProposalDetail p) => switch (p.status) {
      'accepted' => ProposalState.applied,
      'rejected' => ProposalState.rejected,
      'superseded' => ProposalState.superseded,
      _ => ProposalState.pending,
    };

/// 대화 안의 카드 하나 (06 변경 제안 / 06B 장소 추천).
sealed class ChatCard {}

class ProposalEntry extends ChatCard {
  ProposalEntry(this.proposal) : state = stateOf(proposal);
  ProposalDetail proposal;
  ProposalState state;
}

class RecommendationEntry extends ChatCard {
  RecommendationEntry(this.recommendation);
  Recommendation recommendation;

  /// 버튼을 누르고 응답을 기다리는 후보 id.
  final busy = <int>{};
}

/// 사용자 메시지 하나와 그에 대한 구룡메이트 응답 (진행 단계 · 답변 · 카드).
/// 선제 메시지(동행 모드)처럼 사용자 말 없이 시작하면 [user] 가 비어 있다.
class ChatTurn {
  ChatTurn(this.user, {this.fromHistory = false, this.placeId});
  final String user;
  final bool fromHistory;

  /// 현장 가이드(17)에서 그 장소에 대해 나눈 턴 (메시지 place_id). 17 은 이 장소 턴만 보여준다.
  String? placeId;

  /// 진행 영역 맨 위 한 줄 (새 status 가 오면 바꾼다).
  String? status;

  /// 같은 id 는 그 줄을 갱신, 새 id 는 추가 (agent §3.1).
  final steps = <AgentStep>[];

  /// steps_done 이 오면 진행 영역을 이 한 줄로 접는다.
  String? stepsSummary;

  bool thinking = false;
  final text = StringBuffer();
  final cards = <ChatCard>[];
  bool done = false;
  bool stopped = false;
  int? messageId;

  /// 오류 (error 이벤트·스트림 시작 전 503 등). [retryable] 이면 [다시 시도] 로 같은 말을 다시 보낸다.
  String? error;
  String? errorCode;
  bool retryable = false;

  bool get collapsed => stepsSummary != null;
  bool get hasProgress => steps.isNotEmpty || status != null;

  /// "기억했어요 · {detail}" 칩 (gaps b4).
  Iterable<AgentStep> get remembered => steps.where((s) => s.kind == 'memory' && s.status == 'done' && s.detail.isNotEmpty);

  Iterable<ProposalEntry> get proposals => cards.whereType<ProposalEntry>();

  void upsertStep(AgentStep s) {
    for (final old in steps) {
      if (old.id == s.id) {
        old.update(s);
        return;
      }
    }
    steps.add(s);
  }
}

/// 구룡메이트 대화. 계획 하나당 세션 하나 (여행 전·중 맥락 유지).
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

  /// 화면에 잠깐 알릴 오류 (스낵바). 시트가 등록한다.
  void Function(Object error)? onNotice;

  // ---------- 동행 모드 (L2) ----------

  /// 선제 메시지 id → 그 이벤트. 대화 기록에는 숨김 `[이벤트]` 메시지가 안 보이므로 이벤트의 agent.message_id 로 맞춘다 (events.md §3.1).
  final proactive = <int, TripEvent>{};

  /// 시트를 이 메시지로 스크롤 (새 선제 메시지). 시트가 읽고 비운다.
  int? focusMessageId;

  /// 시트가 열려 있을 때 온 도착 카드 (대화 기록에 남지 않는 로컬 카드, 16 진입).
  TripEvent? arrivalCard;

  /// 헤더 컨텍스트 (README §3.3): null 이면 "계획명 · Day n", 동행 중이면 "계획명 · 동행 중" / "장소명 · 현장 해설".
  String? contextLabel;

  void focus(int? messageId) {
    focusMessageId = messageId;
    _notify();
  }

  void showArrivalInChat(TripEvent? event) {
    arrivalCard = event;
    _notify();
  }

  /// 대화의 제안 카드 (선제 제안 상태 확인용).
  ProposalEntry? proposalEntry(int proposalId) {
    for (final t in turns) {
      for (final e in t.proposals) {
        if (e.proposal.id == proposalId) return e;
      }
    }
    return null;
  }

  /// 선제 턴이 다른 세션에 들어갔으면 그 세션으로 (trip 이 정한 세션, trip §1.3).
  Future<void> switchSession(String sid) async {
    if (busy || sid == sessionId) return;
    try {
      final d = await api.session(sid);
      sessionId = sid;
      _setHistory(d);
      _notify();
    } catch (_) {}
  }

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
      if (list.isEmpty) {
        sessionId = (await api.createSession(plan.planId)).id; // 새 세션은 기록이 없다
        turns.clear();
      } else {
        final detail = await api.session(list.first.id);
        sessionId = list.first.id;
        _setHistory(detail);
      }
    } catch (e) {
      initError = e;
    } finally {
      initializing = false;
      _notify();
    }
  }

  /// 기록의 카드도 서버 상태(조회할 때의 status·base_changed·후보 state)로 다시 그린다.
  /// [force]: 선제 메시지가 왔을 때. 응답 중이면 끝난 뒤 다시 읽는다 (서버도 사용자 턴이 끝난 뒤 선제 턴을 넣는다).
  Future<void> reloadSession({bool force = false}) async {
    final sid = sessionId;
    if (sid == null) return;
    if (busy) {
      if (force) _reloadAfterTurn = true;
      return;
    }
    final count = turns.length;
    try {
      final d = await api.session(sid);
      // 기다리는 사이 새 말을 보냈으면 그 턴을 지우지 않는다
      if (busy || turns.length != count || sid != sessionId) return;
      _setHistory(d);
      _notify();
    } catch (_) {}
  }

  void _setHistory(AgentSessionDetail d) {
    turns
      ..clear()
      ..addAll(turnsFromHistory(d.messages));
  }

  /// 메시지 기록 → 턴. user 메시지마다 새 턴, user 없이 온 assistant(선제 메시지)도 새 턴.
  static List<ChatTurn> turnsFromHistory(List<AgentMessage> messages) {
    final out = <ChatTurn>[];
    for (final m in messages) {
      if (m.role == 'user') {
        out.add(ChatTurn(m.content, fromHistory: true, placeId: m.placeId)..done = true);
        continue;
      }
      if (out.isEmpty || out.last.text.isNotEmpty || out.last.cards.isNotEmpty) {
        out.add(ChatTurn('', fromHistory: true)..done = true);
      }
      final t = out.last
        ..messageId = m.id
        ..placeId ??= m.placeId;
      t.text.write(m.content);
      t.steps.addAll(m.steps);
      if (m.steps.isNotEmpty) t.stepsSummary = m.stepsSummary ?? '${m.steps.length}단계 완료';
      for (final c in m.cards) {
        if (c.proposal != null) t.cards.add(ProposalEntry(c.proposal!));
        if (c.recommendation != null) t.cards.add(RecommendationEntry(c.recommendation!));
      }
    }
    return out;
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

  /// [placeId]: 현장 가이드(17)에서 보낸 말 — 같은 세션이라 AI 시트에도 그대로 남는다.
  Future<void> send(String message, {String? placeId}) async {
    final text = message.trim();
    if (text.isEmpty || busy) return;
    if (sessionId == null) await init();
    final sid = sessionId;
    if (sid == null) return;
    _run(ChatTurn(text, placeId: placeId), api.chat(sid, text, placeId: placeId));
  }

  /// 36 [현재 일정에 맞춰 다시 제안] / progress_changed [현재 상황에 맞춰 다시 제안].
  void repropose(ProposalEntry entry) {
    final sid = sessionId;
    if (sid == null || busy) return;
    _run(ChatTurn('이전 제안을 현재 일정에 맞춰 다시 만들어줘.'), api.repropose(sid, entry.proposal.id));
  }

  void _run(ChatTurn turn, Stream<SseEvent> stream) {
    turns.add(turn);
    busy = true;
    _notify();
    _sub = stream.listen(
      (ev) => _onEvent(turn, ev),
      onError: (Object e) {
        if (e is ApiException && e.code == 'agent_busy') {
          // 스트림이 시작되지 않았다: 말풍선을 거두고 스낵바로 알린다 (README §6.3)
          turns.remove(turn);
          onNotice?.call(e);
        } else {
          turn
            ..error = e is ApiException && (e.isNetwork || e.status >= 500) ? '구룡메이트에 연결하지 못했어요' : (e is ApiException ? e.message : '$e')
            ..errorCode = e is ApiException ? e.code : null
            ..retryable = true;
        }
        _finish(turn);
      },
      onDone: () => _finish(turn),
      cancelOnError: true,
    );
  }

  void _onEvent(ChatTurn turn, SseEvent ev) {
    final d = ev.data;
    switch (ev.event) {
      case 'status':
        turn.status = '${d['text'] ?? ''}';
      case 'step':
        turn
          ..thinking = false
          ..upsertStep(AgentStep.fromJson(d));
      case 'thinking':
        turn.thinking = true; // 내용은 보이지 않는다 ("생각 중"만)
      case 'text':
        turn
          ..thinking = false
          ..text.write(d['delta'] ?? '');
      case 'proposal':
        final p = ProposalDetail.fromJson(d);
        // 같은 대화의 새 제안이 이전 대기 제안을 대체한다 (서버도 superseded 로 바꾼다)
        for (final t in turns) {
          for (final e in t.proposals) {
            if (e.proposal.id != p.id && e.state == ProposalState.pending) e.state = ProposalState.superseded;
          }
        }
        turn.cards.add(ProposalEntry(p));
      case 'recommendation':
        turn.cards.add(RecommendationEntry(Recommendation.fromJson(d)));
      case 'steps_done':
        turn.stepsSummary = '${d['summary'] ?? '${d['count']}단계 완료'}';
      case 'done':
        final full = '${d['text'] ?? ''}';
        if (turn.text.isEmpty && full.isNotEmpty) turn.text.write(full);
        turn.messageId = d['message_id'] as int?;
      case 'error':
        turn
          ..error = '${d['message'] ?? '지금은 답할 수 없어요. 잠시 후 다시 시도해 주세요.'}'
          ..errorCode = d['code'] as String?
          ..retryable = d['code'] == 'llm_unavailable' || d['code'] == 'llm_quota';
    }
    _notify();
  }

  void _finish(ChatTurn turn) {
    turn
      ..done = true
      ..thinking = false;
    for (final s in turn.steps) {
      if (s.running) s.status = turn.stopped || turn.error != null ? 'error' : 'done';
    }
    if (turn.steps.isNotEmpty && turn.stepsSummary == null && (turn.text.isNotEmpty || turn.cards.isNotEmpty)) {
      turn.stepsSummary = '${turn.steps.length}단계 완료';
    }
    busy = false;
    _sub = null;
    _notify();
    if (_reloadAfterTurn) {
      _reloadAfterTurn = false;
      reloadSession();
    }
  }

  bool _reloadAfterTurn = false;

  /// 실행 중 Stop. 연결을 끊으면 서버가 턴을 중단하고 그때까지의 기록을 남긴다.
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

  /// [다시 시도]: 같은 말을 새 턴으로 보낸다.
  void retry(ChatTurn turn) {
    if (busy || turn.user.isEmpty) return;
    turns.remove(turn);
    send(turn.user, placeId: turn.placeId);
  }

  /// 승인. 409 (base_changed · progress_changed · agent_busy · proposal_not_pending) 는 ApiException 으로 던진다 → proposal_actions.dart.
  Future<void> accept(ProposalEntry entry, {bool overwrite = false}) async {
    final sid = sessionId;
    if (sid == null) return;
    entry.state = ProposalState.applying;
    _notify();
    try {
      final (p, after) = await api.acceptProposal(sid, entry.proposal.id, overwrite: overwrite);
      entry
        ..proposal = p
        ..state = ProposalState.applied;
      plan.applyAccepted(p, after);
    } on ApiException catch (e) {
      entry.state = ProposalState.pending;
      if (e.code == 'proposal_not_pending') await reloadSession();
      _notify();
      rethrow;
    }
    _notify();
  }

  Future<void> reject(ProposalEntry entry) async {
    final sid = sessionId;
    if (sid == null) return;
    try {
      entry
        ..proposal = await api.rejectProposal(sid, entry.proposal.id)
        ..state = ProposalState.rejected;
    } on ApiException catch (e) {
      if (e.code == 'proposal_not_pending') await reloadSession();
      rethrow;
    } finally {
      _notify();
    }
  }

  /// 06B 후보 [일정에 추가] / [취소]. 응답의 recommendation 으로 카드를, plan 으로 일정을 다시 그린다.
  /// 409 already_in_plan / not_in_plan 은 카드만 서버 상태로 갱신 (00-existing §06B).
  Future<void> toggleCandidate(RecommendationEntry entry, RecommendationCandidate c) async {
    final sid = sessionId;
    if (sid == null) return;
    final add = !c.inPlan;
    entry.busy.add(c.id);
    _notify();
    try {
      final (rec, after) = await api.candidate(sid, entry.recommendation.id, c.id, add: add);
      entry.recommendation = rec;
      plan.applyPlan(after);
      if (add) {
        final added = rec.candidates.where((x) => x.id == c.id).firstOrNull?.itemId;
        if (added != null) plan.markAiAdded(added, '구룡메이트 추천: ${c.title}');
      }
    } on ApiException catch (e) {
      if (e.code == 'already_in_plan' || e.code == 'not_in_plan') {
        await _refreshRecommendation(entry);
        await plan.load(silent: true).catchError((_) {});
        return;
      }
      rethrow;
    } finally {
      entry.busy.remove(c.id);
      _notify();
    }
  }

  Future<void> _refreshRecommendation(RecommendationEntry entry) async {
    final sid = sessionId;
    if (sid == null) return;
    try {
      final d = await api.session(sid);
      for (final m in d.messages) {
        for (final c in m.cards) {
          if (c.recommendation?.id == entry.recommendation.id) entry.recommendation = c.recommendation!;
        }
      }
    } catch (_) {}
  }
}
