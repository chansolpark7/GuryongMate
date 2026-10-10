import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/models/agent.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../planner/plan_controller.dart';
import '../settings/memory_screen.dart';
import '../trip/trip_overlay.dart';
import 'chat_controller.dart';
import 'proactive_widgets.dart';
import 'proposal_card.dart';

const _half = 0.77; // Figma: Half top 196 / 844
const _full = 0.94; // Full top 56

/// 시트가 아직 맨 위일 때만 닫는다. 이미 닫히는 중(최소 높이 도달로 자동 pop, X 두 번 탭)에 또 pop 하면
/// 아래의 PlannerShell 까지 닫혀 계획 목록으로 돌아가 버린다.
void _closeSheet(BuildContext context) {
  if (ModalRoute.of(context)?.isCurrent ?? false) Navigator.of(context).pop();
}

/// 05_Planner_AI_BottomSheet. FAB → Half 로 slide-up, 위로 끌거나 확장 아이콘 → Full.
Future<void> showAiSheet(
  BuildContext context, {
  required PlanController plan,
  required ChatController chat,
  String? prefill,
  String? sendNow,
  bool full = false,
}) =>
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      barrierColor: const Color(0x59111827), // #111827 35%
      sheetAnimationStyle: const AnimationStyle(duration: Duration(milliseconds: 300), curve: Curves.easeOut),
      builder: (_) => MultiProvider(
        providers: [ChangeNotifierProvider.value(value: plan), ChangeNotifierProvider.value(value: chat)],
        child: _AiSheet(prefill: prefill, sendNow: sendNow, full: full),
      ),
    );

class _AiSheet extends StatefulWidget {
  const _AiSheet({this.prefill, this.sendNow, this.full = false});
  final String? prefill;
  final String? sendNow;

  /// 선제 메시지에서 열 때: Full 로 (events.md §3.2).
  final bool full;

  @override
  State<_AiSheet> createState() => _AiSheetState();
}

class _AiSheetState extends State<_AiSheet> {
  final _sheet = DraggableScrollableController();
  final _input = TextEditingController();
  final _focus = FocusNode();
  ScrollController? _scroll;
  late final ChatController _chat;
  int _lastLen = 0;

  /// 선제 메시지로 스크롤하기 위한 턴 키 (message id 별).
  final _turnKeys = <int, GlobalKey>{};

  GlobalKey _keyFor(int messageId) => _turnKeys.putIfAbsent(messageId, GlobalKey.new);

  @override
  void initState() {
    super.initState();
    _chat = context.read<ChatController>();
    _chat.addListener(_autoScroll);
    _chat.addListener(_focusMessage);
    _chat.onNotice = (e) {
      if (mounted) showError(context, e);
    };
    _input.text = widget.prefill ?? '';
    _input.addListener(() => setState(() {}));
    _chat.init().then((_) {
      final s = widget.sendNow;
      if (s != null && mounted) {
        _chat.send(s);
      } else if (mounted) {
        _chat.reloadSession(); // 다시 열면 카드도 서버 상태로 (다른 곳에서 승인·대체됐을 수 있다)
      }
    });
  }

  @override
  void dispose() {
    _chat.removeListener(_autoScroll);
    _chat.removeListener(_focusMessage);
    _chat.onNotice = null;
    _sheet.dispose();
    _input.dispose();
    _focus.dispose();
    super.dispose();
  }

  /// 새 내용이 오면 맨 아래로 (사용자가 위로 올려 읽는 중이면 그대로 둔다).
  void _autoScroll() {
    final s = _scroll;
    final len = _chat.turns.fold<int>(0, (n, t) => n + t.text.length + t.steps.length * 10 + t.cards.length * 1000 + (t.collapsed ? 5 : 0));
    if (s == null || !s.hasClients || len == _lastLen) return;
    _lastLen = len;
    final nearBottom = s.position.maxScrollExtent - s.position.pixels < 160;
    if (!nearBottom) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (s.hasClients) s.animateTo(s.position.maxScrollExtent, duration: const Duration(milliseconds: 200), curve: Curves.easeOut);
    });
  }

  /// 새 선제 메시지가 오면 그 메시지로 스크롤 (입력창의 글은 그대로, events.md §3.1).
  void _focusMessage() {
    final mid = _chat.focusMessageId;
    if (mid == null) return;
    WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToMessage(mid));
  }

  void _scrollToMessage(int mid, {int tries = 0}) {
    if (!mounted) return;
    final ctx = _turnKeys[mid]?.currentContext;
    if (ctx == null) {
      // 기록을 다시 읽는 중이면 조금 뒤에 다시
      if (tries < 20) Future<void>.delayed(const Duration(milliseconds: 150), () => _scrollToMessage(mid, tries: tries + 1));
      return;
    }
    if (_chat.focusMessageId == mid) _chat.focusMessageId = null;
    Scrollable.ensureVisible(ctx, duration: const Duration(milliseconds: 250), curve: Curves.easeOut, alignment: 0.02);
  }

  /// 대기 안내 줄 [제안 보기] → 대기 중인 제안이 있는 턴으로.
  void _scrollToPending() {
    for (final t in _chat.turns.reversed) {
      if (t.proposals.any((e) => e.state == ProposalState.pending) && t.messageId != null) {
        _scrollToMessage(t.messageId!);
        return;
      }
    }
  }

  void _send([String? text]) {
    final msg = (text ?? _input.text).trim();
    if (msg.isEmpty) return;
    if (_chat.busy) {
      showInfo(context, '구룡메이트가 답하는 중이에요. 잠시 후 다시 눌러 주세요');
      return;
    }
    _input.clear();
    _chat.send(msg);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final s = _scroll;
      if (s != null && s.hasClients) s.animateTo(s.position.maxScrollExtent + 200, duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
    });
  }

  void _toggleSize() {
    final full = _sheet.isAttached && _sheet.size > (_half + _full) / 2;
    _sheet.animateTo(full ? _half : _full, duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final chat = context.watch<ChatController>();
    final plan = context.watch<PlanController>();
    final bottomInset = MediaQuery.viewInsetsOf(context).bottom;
    return Padding(
      padding: EdgeInsets.only(bottom: bottomInset),
      child: DraggableScrollableSheet(
        controller: _sheet,
        initialChildSize: widget.full ? _full : _half,
        minChildSize: 0.35,
        maxChildSize: _full,
        snap: true,
        snapSizes: const [_half],
        shouldCloseOnMinExtent: true,
        expand: false,
        builder: (context, scroll) {
          _scroll = scroll;
          final full = _sheet.isAttached && _sheet.size > (_half + _full) / 2;
          return Container(
            decoration: const BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
            child: Column(children: [
              GestureDetector(
                behavior: HitTestBehavior.opaque,
                onVerticalDragUpdate: (d) {
                  if (!_sheet.isAttached) return;
                  final h = MediaQuery.sizeOf(context).height;
                  _sheet.jumpTo((_sheet.size - d.delta.dy / h).clamp(0.0, _full));
                },
                onVerticalDragEnd: (d) {
                  if (!_sheet.isAttached || !(ModalRoute.of(context)?.isCurrent ?? false)) return;
                  final v = d.primaryVelocity ?? 0;
                  if (v > 700 || _sheet.size < 0.5) {
                    _closeSheet(context);
                  } else {
                    _sheet.animateTo(v < -300 || _sheet.size > (_half + _full) / 2 ? _full : _half, duration: const Duration(milliseconds: 200), curve: Curves.easeOut);
                  }
                },
                child: Column(children: [
                  const Grabber(),
                  _Header(plan: plan, label: chat.contextLabel, full: full, onToggle: _toggleSize, onNew: chat.busy ? null : chat.newSession),
                ]),
              ),
              Expanded(
                child: ListView(
                  controller: scroll,
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
                  children: [
                    if (chat.initializing && chat.turns.isEmpty)
                      const Padding(padding: EdgeInsets.all(32), child: Center(child: CircularProgressIndicator()))
                    else if (chat.initError != null && chat.sessionId == null)
                      StateView(
                        icon: Icons.dns_outlined,
                        title: '구룡메이트에 연결하지 못했어요',
                        message: '잠시 후 다시 시도해 주세요. 일정은 그대로예요.',
                        actionLabel: '다시 시도',
                        onAction: chat.init,
                      )
                    else if (chat.turns.isEmpty)
                      _Welcome(day: plan.day, onPick: _send)
                    else
                      for (final (i, t) in chat.turns.indexed)
                        TurnView(
                          key: t.messageId == null ? null : _keyFor(t.messageId!),
                          turn: t,
                          isLast: i == chat.turns.length - 1,
                          onFollowUp: _send,
                          onShowPending: _scrollToPending,
                        ),
                    // 시트가 열려 있을 때 온 도착 카드 (대화 기록에는 남지 않는 로컬 카드, 16)
                    if (chat.arrivalCard != null && tripHub.controller != null)
                      Padding(padding: const EdgeInsets.only(bottom: 16), child: ArrivalCard(event: chat.arrivalCard!, c: tripHub.controller!, inChat: true)),
                  ],
                ),
              ),
              _InputBar(controller: _input, focus: _focus, busy: chat.busy, onSend: _send, onStop: chat.stop),
            ]),
          );
        },
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.plan, required this.label, required this.full, required this.onToggle, required this.onNew});
  final PlanController plan;

  /// 동행 중 헤더 컨텍스트 ("계획명 · 동행 중" / "장소명 · 현장 해설"). null 이면 "계획명 · Day n".
  final String? label;
  final bool full;
  final VoidCallback onToggle;
  final VoidCallback? onNew;

  @override
  Widget build(BuildContext context) => SizedBox(
        height: 56,
        child: Padding(
          padding: const EdgeInsets.only(left: 16, right: 4),
          child: Row(children: [
            const AiAvatar(),
            const SizedBox(width: 10),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisAlignment: MainAxisAlignment.center, children: [
                Text('구룡메이트', style: AppText.headline),
                Text(label ?? '${plan.plan?.title ?? ''} · Day ${plan.day}', style: AppText.caption, overflow: TextOverflow.ellipsis),
              ]),
            ),
            PopupMenuButton<String>(
              tooltip: '대화 메뉴',
              icon: const Icon(Icons.more_horiz, color: AppColors.text2),
              onSelected: (_) => onNew?.call(),
              itemBuilder: (_) => [PopupMenuItem(value: 'new', enabled: onNew != null, child: const Text('새 대화 시작'))],
            ),
            IconButton(
              tooltip: full ? '줄이기' : '크게 보기',
              icon: Icon(full ? Icons.close_fullscreen : Icons.open_in_full, size: 20, color: AppColors.text2),
              onPressed: onToggle,
            ),
            IconButton(tooltip: '닫기', icon: const Icon(Icons.close, color: AppColors.text2), onPressed: () => _closeSheet(context)),
          ]),
        ),
      );
}

class _Welcome extends StatelessWidget {
  const _Welcome({required this.day, required this.onPick});
  final int day;
  final ValueChanged<String> onPick;

  @override
  Widget build(BuildContext context) {
    // 빠른 실행 (c10): agent 에 check_safety·safety_outlook 이 있으므로 "날씨·안전 확인"을 둔다.
    final actions = [
      (Icons.route, '경로 최적화', 'Day $day 동선을 이동 시간이 짧아지도록 최적화해줘'),
      (Icons.verified_user_outlined, '날씨·안전 확인', 'Day $day 날씨와 안전 확인해줘'),
      (Icons.restaurant, '점심 장소 찾기', 'Day $day 점심 먹을 곳을 추천해줘'),
      (Icons.add, '빈 시간 채우기', 'Day $day 빈 시간에 갈 만한 곳 추천해줘'),
    ];
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text('무엇을 도와드릴까요? 일정을 직접 바꿔드릴 수 있어요.', style: AppText.body),
      const SizedBox(height: 14),
      GridView.count(
        crossAxisCount: 2,
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        mainAxisSpacing: 8,
        crossAxisSpacing: 8,
        childAspectRatio: 167 / 44,
        children: [
          for (final (icon, label, prompt) in actions)
            Material(
              color: AppColors.background,
              borderRadius: BorderRadius.circular(12),
              child: InkWell(
                borderRadius: BorderRadius.circular(12),
                onTap: () => onPick(prompt),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  child: Row(children: [
                    Icon(icon, size: 16, color: AppColors.ai),
                    const SizedBox(width: 8),
                    Expanded(child: Text(label, style: AppText.label, overflow: TextOverflow.ellipsis)),
                  ]),
                ),
              ),
            ),
        ],
      ),
    ]);
  }
}

/// 한 턴: 사용자 말풍선 → 진행 영역(status + 단계, steps_done 이면 한 줄로 접힘) → 답변 → 기억 칩 → 오류 → 카드.
class TurnView extends StatelessWidget {
  const TurnView({super.key, required this.turn, required this.isLast, required this.onFollowUp, this.onShowPending});
  final ChatTurn turn;
  final bool isLast;
  final ValueChanged<String> onFollowUp;
  final VoidCallback? onShowPending;

  @override
  Widget build(BuildContext context) {
    final chat = context.read<ChatController>();
    final (text, pendingNotice) = splitPendingNotice(turn.text.toString().trim());
    final lastPending = turn.proposals.where((e) => e.state == ProposalState.pending).lastOrNull;
    // 선제 메시지: 이벤트의 agent.message_id 와 같은 id 의 메시지 (events.md §3.1)
    final event = turn.messageId == null ? null : chat.proactive[turn.messageId];
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        if (turn.user.isNotEmpty) _UserBubble(text: turn.user),
        if (event != null) ProactiveHeader(event: event),
        if (turn.hasProgress) ...[
          const SizedBox(height: 12),
          turn.collapsed ? ActivitySummary(turn: turn) : ActivityCard(turn: turn),
        ] else if (!turn.done && text.isEmpty) ...[
          const SizedBox(height: 12),
          const _Thinking(label: '생각 중…'),
        ],
        if (text.isNotEmpty) ...[
          SizedBox(height: event != null ? 4 : 12),
          Text(text, style: AppText.body),
        ],
        if (event?.type == 'safety_raised') HazardEvidenceBox(event: event!),
        if (pendingNotice) PendingNoticeLine(onTap: onShowPending),
        if (turn.remembered.isNotEmpty) ...[
          const SizedBox(height: 8),
          for (final s in turn.remembered) Padding(padding: const EdgeInsets.only(bottom: 6), child: _MemoryChip(text: s.detail, planId: chat.plan.planId)),
        ],
        if (turn.error != null) ...[
          const SizedBox(height: 10),
          _ErrorBox(turn: turn, onRetry: turn.retryable && turn.user.isNotEmpty ? () => chat.retry(turn) : null),
        ],
        if (turn.stopped) Padding(padding: const EdgeInsets.only(top: 8), child: Text('응답을 중단했어요', style: AppText.caption)),
        for (final c in turn.cards) ...[
          const SizedBox(height: 12),
          switch (c) {
            ProposalEntry() => ProposalCard(entry: c),
            RecommendationEntry() => RecommendationCard(entry: c),
          },
        ],
        if (isLast && lastPending != null && turn.done) _FollowUps(entry: lastPending, eventType: event?.type, text: text, onPick: onFollowUp),
      ]),
    );
  }
}

class _UserBubble extends StatelessWidget {
  const _UserBubble({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) => Align(
        alignment: Alignment.centerRight,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 270),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: const BoxDecoration(
              color: AppColors.primary,
              borderRadius: BorderRadius.only(
                topLeft: Radius.circular(18),
                topRight: Radius.circular(18),
                bottomLeft: Radius.circular(18),
                bottomRight: Radius.circular(6),
              ),
            ),
            child: Text(text, style: AppText.body.copyWith(color: Colors.white)),
          ),
        ),
      );
}

class _Thinking extends StatelessWidget {
  const _Thinking({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) => Row(children: [
        const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.ai)),
        const SizedBox(width: 8),
        Text(label, style: AppText.meta.copyWith(color: AppColors.ai)),
      ]);
}

/// 기억 칩 (18): "기억했어요 · {text}" + "나에 대해 ›" (31).
class _MemoryChip extends StatelessWidget {
  const _MemoryChip({required this.text, required this.planId});
  final String text;
  final int planId;

  @override
  Widget build(BuildContext context) => Material(
        color: AppColors.aiSoft,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          key: const ValueKey('memory-chip'),
          borderRadius: BorderRadius.circular(12),
          onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => MemoryScreen(planId: planId))),
          child: Container(
            height: 40,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Row(children: [
              const Icon(Icons.bookmark_border, size: 16, color: AppColors.ai),
              const SizedBox(width: 6),
              Expanded(child: Text('기억했어요 · $text', style: AppText.meta.copyWith(color: AppColors.ai, fontWeight: FontWeight.w500), overflow: TextOverflow.ellipsis)),
              Text('나에 대해 ›', style: AppText.caption.copyWith(color: AppColors.text, fontWeight: FontWeight.w700)),
            ]),
          ),
        ),
      );
}

class _ErrorBox extends StatelessWidget {
  const _ErrorBox({required this.turn, required this.onRetry});
  final ChatTurn turn;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(12, 8, 4, 8),
        decoration: BoxDecoration(color: AppColors.dangerSoft, borderRadius: BorderRadius.circular(12)),
        child: Row(children: [
          const Icon(Icons.error_outline, color: AppColors.danger, size: 18),
          const SizedBox(width: 8),
          Expanded(child: Text(turn.error!, style: AppText.meta.copyWith(color: AppColors.danger))),
          if (onRetry != null)
            TextButton.icon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh, size: 16, color: AppColors.danger),
              label: Text('다시 시도', style: AppText.meta.copyWith(color: AppColors.danger, fontWeight: FontWeight.w700)),
            ),
        ]),
      );
}

/// 단계 kind 별 아이콘 (Figma 05 Tool Activity 의 단계 줄 앞).
IconData stepIcon(String kind) => switch (kind) {
      'search' => Icons.search,
      'place' => Icons.place_outlined,
      'plan' => Icons.event_note_outlined,
      'edit' => Icons.edit_calendar_outlined,
      'rule' => Icons.rule,
      'review' => Icons.fact_check_outlined,
      'recommend' => Icons.recommend_outlined,
      'proposal' => Icons.auto_awesome,
      'route' => Icons.route,
      'safety' => Icons.verified_user_outlined,
      'memory' || 'forget' || 'recall' => Icons.bookmark_border,
      'research' => Icons.menu_book_outlined,
      'guide' => Icons.description_outlined,
      _ => Icons.more_horiz,
    };

/// 진행 중: "✨ {status}" + 단계 목록 (running 스피너 Purple / done Green ✓ + detail / error Red + detail).
class ActivityCard extends StatelessWidget {
  const ActivityCard({super.key, required this.turn});
  final ChatTurn turn;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(borderRadius: BorderRadius.circular(16), border: Border.all(color: AppColors.aiLine)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.auto_awesome, size: 16, color: AppColors.ai),
            const SizedBox(width: 6),
            Expanded(child: Text((turn.status ?? '요청을 확인하고 있어요').replaceFirst(RegExp(r'\.$'), ''), style: AppText.section.copyWith(fontSize: 15))),
          ]),
          if (turn.steps.isNotEmpty) ...[const SizedBox(height: 8), StepList(steps: turn.steps)],
        ]),
      );
}

class StepList extends StatelessWidget {
  const StepList({super.key, required this.steps});
  final List<AgentStep> steps;

  @override
  Widget build(BuildContext context) {
    const spinner = SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.ai));
    return Column(children: [
      for (final (i, s) in steps.indexed)
        IntrinsicHeight(
          key: ValueKey('step-${s.id}'),
          child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            SizedBox(
              width: 20,
              child: Column(children: [
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: s.running ? spinner : Icon(s.failed ? Icons.error : Icons.check_circle, size: 20, color: s.failed ? AppColors.danger : AppColors.success),
                ),
                if (i < steps.length - 1) Expanded(child: Container(width: 2, color: AppColors.line, margin: const EdgeInsets.symmetric(vertical: 2))),
              ]),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    Icon(stepIcon(s.kind), size: 14, color: s.running ? AppColors.ai : AppColors.text3),
                    const SizedBox(width: 4),
                    Flexible(
                      child: Text(s.title,
                          style: AppText.label.copyWith(color: s.running ? AppColors.ai : (s.failed ? AppColors.danger : AppColors.text))),
                    ),
                  ]),
                  if (s.running || s.detail.isNotEmpty)
                    Text(s.running && s.detail.isEmpty ? '잠시만요…' : s.detail,
                        style: AppText.caption.copyWith(color: s.failed ? AppColors.danger : AppColors.text3), maxLines: 2, overflow: TextOverflow.ellipsis),
                ]),
              ),
            ),
          ]),
        ),
    ]);
  }
}

/// steps_done 뒤: 접힌 요약 "✓ n단계 완료 · …" (펼치면 단계 목록).
class ActivitySummary extends StatefulWidget {
  const ActivitySummary({super.key, required this.turn});
  final ChatTurn turn;

  @override
  State<ActivitySummary> createState() => _ActivitySummaryState();
}

class _ActivitySummaryState extends State<ActivitySummary> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final t = widget.turn;
    final failed = t.steps.any((s) => s.failed);
    final summary = (t.stepsSummary ?? '').replaceFirst(' : ', ' · ');
    return Material(
      color: AppColors.background,
      borderRadius: BorderRadius.circular(12),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => setState(() => _open = !_open),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          child: Column(children: [
            Row(children: [
              Icon(failed ? Icons.error : Icons.check_circle, size: 20, color: failed ? AppColors.warning : AppColors.success),
              const SizedBox(width: 8),
              Expanded(child: Text(summary, style: AppText.meta.copyWith(color: AppColors.text), maxLines: _open ? 3 : 1, overflow: TextOverflow.ellipsis)),
              Icon(_open ? Icons.expand_less : Icons.expand_more, color: AppColors.text3),
            ]),
            if (_open) ...[const SizedBox(height: 10), StepList(steps: t.steps)],
          ]),
        ),
      ),
    );
  }
}

/// 제안에 답하지 않고 이어서 요청하는 칩 (탭하면 새 요청으로 전송, 이전 제안은 대체).
class _FollowUps extends StatelessWidget {
  const _FollowUps({required this.entry, required this.onPick, this.eventType, this.text = ''});
  final ProposalEntry entry;
  final ValueChanged<String> onPick;

  /// 선제 메시지의 이벤트 종류 (지연·안전이면 그에 맞는 칩, 19·22).
  final String? eventType;
  final String text;

  @override
  Widget build(BuildContext context) {
    final rows = entry.proposal.before == null ? const <ChangeRow>[] : buildChangeRows(entry.proposal);
    final changed = rows.where((r) => r.badge == '교체' || r.badge == '삭제').map((r) => r.badge == '삭제' ? r.title : (r.fromText ?? r.title)).firstOrNull;
    final chips = switch (eventType) {
      // 19: 답변에 나온 대안에 맞춘 칩 + "그대로 둘게요" (서버가 칩을 주지 않는다)
      'delay' => [
          if (changed != null) '$changed은 유지해줘',
          if (text.contains('②')) '②번으로 해줘',
          '그대로 둘게요',
        ],
      // 22: 위험한 곳은 다른 시간에 / 다른 대체 장소
      'safety_raised' => [
          if (changed != null) '$changed은 다른 시간에 갈게',
          '실내 말고 다른 곳은?',
        ],
      _ => [
          if (changed != null) '$changed은 그대로 둬줘',
          '이동 시간 더 줄여줘',
          '조금 더 여유롭게',
        ],
    };
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Wrap(spacing: 8, runSpacing: 8, children: [
        for (final c in chips)
          Material(
            color: AppColors.surface,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: const BorderSide(color: AppColors.aiLine)),
            child: InkWell(
              borderRadius: BorderRadius.circular(16),
              onTap: () => onPick(c),
              child: Container(
                height: 32,
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: Center(widthFactor: 1, child: Text(c, style: AppText.meta.copyWith(color: AppColors.ai, fontWeight: FontWeight.w500))),
              ),
            ),
          ),
      ]),
    );
  }
}

class _InputBar extends StatelessWidget {
  const _InputBar({required this.controller, required this.focus, required this.busy, required this.onSend, required this.onStop});
  final TextEditingController controller;
  final FocusNode focus;
  final bool busy;
  final VoidCallback onSend;
  final VoidCallback onStop;

  @override
  Widget build(BuildContext context) {
    final typing = controller.text.trim().isNotEmpty;
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
        child: Container(
          height: 48,
          padding: const EdgeInsets.only(left: 16, right: 6),
          decoration: BoxDecoration(color: AppColors.field, borderRadius: BorderRadius.circular(24)),
          child: Row(children: [
            Expanded(
              child: TextField(
                controller: controller,
                focusNode: focus,
                textInputAction: TextInputAction.send,
                onSubmitted: (_) => onSend(),
                style: AppText.body,
                decoration: const InputDecoration(
                  hintText: '구룡메이트에게 요청하기…', // Figma 18·19 (README §1 이름)
                  border: InputBorder.none,
                  filled: false,
                  isCollapsed: true,
                ),
              ),
            ),
            IconButton(
              tooltip: '음성으로 말하기',
              icon: const Icon(Icons.mic_none, color: AppColors.text2, size: 20),
              onPressed: () => showInfo(context, '음성 대화는 준비 중이에요. 지금은 글로 요청해 주세요.'),
            ),
            _SendButton(state: busy ? 2 : (typing ? 1 : 0), onTap: busy ? onStop : (typing ? onSend : null)),
          ]),
        ),
      ),
    );
  }
}

class _SendButton extends StatelessWidget {
  const _SendButton({required this.state, required this.onTap});
  final int state; // 0 empty · 1 typing · 2 running
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final bg = switch (state) { 2 => AppColors.text, 1 => AppColors.primary, _ => const Color(0xFFD1D5DB) };
    return Tooltip(
      message: state == 2 ? '중단' : '보내기',
      child: Material(
        color: bg,
        shape: const CircleBorder(),
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: onTap,
          child: SizedBox(
            width: 36,
            height: 36,
            child: Icon(state == 2 ? Icons.stop_rounded : Icons.arrow_upward, color: Colors.white, size: 20),
          ),
        ),
      ),
    );
  }
}
