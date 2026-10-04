import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/models/plan.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../planner/plan_controller.dart';
import 'chat_controller.dart';

/// 제안 카드의 한 줄 (항목 단위로 묶은 변경).
class ChangeRow {
  ChangeRow({required this.badge, required this.title, this.fromText, this.toText, this.reason = '', this.strike = false, this.itemId, this.day});
  final String badge; // 이동 · 교체 · 삭제 · 추가 · 변경 · 조건 · 여행
  final String title;
  final String? fromText;
  final String? toText;
  final String reason;
  final bool strike;
  final int? itemId;
  final int? day;
}

String _stripPrefix(String m) {
  final i = m.indexOf(': ');
  return i >= 0 && i < 8 ? m.substring(i + 2) : m;
}

/// planner Change 목록 → 카드 줄. 같은 자리의 삭제+추가는 "교체"로 묶는다.
List<ChangeRow> buildChangeRows(ProposalDetail p) {
  final before = p.before, after = p.after;
  final rows = <ChangeRow>[];
  final byItem = <int, List<Change>>{};
  for (final c in p.changes) {
    if (c.itemId != null) byItem.putIfAbsent(c.itemId!, () => []).add(c);
  }
  final removed = p.changes.where((c) => c.kind == 'removed').toList();
  final added = p.changes.where((c) => c.kind == 'added').toList();
  final pairedAdded = <int>{};
  final pairedRemoved = <int>{};
  for (final r in removed) {
    final b = before?.item(r.itemId!);
    if (b == null) continue;
    for (final a in added) {
      final ai = after?.item(a.itemId!);
      if (ai != null && !pairedAdded.contains(a.itemId) && ai.day == b.day && ai.order == b.order) {
        pairedAdded.add(a.itemId!);
        pairedRemoved.add(r.itemId!);
        rows.add(ChangeRow(
          badge: '교체',
          title: '${ai.startAt ?? ''} ${b.title}'.trim(),
          fromText: b.title,
          toText: ai.title,
          reason: '${ai.place?.contentType ?? ''} · ${ai.stayMinutes}분',
          itemId: ai.id,
          day: ai.day,
        ));
        break;
      }
    }
  }
  for (final entry in byItem.entries) {
    final id = entry.key;
    if (pairedAdded.contains(id) || pairedRemoved.contains(id)) continue;
    final kinds = entry.value.map((c) => c.kind).toSet();
    final b = before?.item(id), a = after?.item(id);
    final reason = entry.value.map((c) => _stripPrefix(c.message)).join(' · ');
    if (kinds.contains('added')) {
      rows.add(ChangeRow(badge: '추가', title: a?.title ?? reason, toText: a?.startAt, reason: a == null ? '' : '${a.day}일차 ${a.order}번째 · ${a.stayMinutes}분', itemId: id, day: a?.day));
    } else if (kinds.contains('removed')) {
      rows.add(ChangeRow(badge: '삭제', title: b?.title ?? reason, fromText: b?.startAt, strike: true, reason: '일정에서 빠져요', itemId: id, day: b?.day));
    } else {
      rows.add(ChangeRow(
        badge: kinds.contains('moved') ? '이동' : '변경',
        title: a?.title ?? b?.title ?? '',
        fromText: b?.startAt,
        toText: a?.startAt,
        reason: reason,
        itemId: id,
        day: a?.day,
      ));
    }
  }
  for (final c in p.changes) {
    if (c.kind == 'rule_added' || c.kind == 'rule_removed') {
      rows.add(ChangeRow(badge: '조건', title: _stripPrefix(c.message), reason: c.kind == 'rule_added' ? '조건 추가' : '조건 삭제'));
    } else if (c.kind == 'plan') {
      rows.add(ChangeRow(badge: '여행', title: c.message));
    }
  }
  return rows;
}

(Color, Color) badgeColors(String badge) => switch (badge) {
      '이동' || '변경' => (AppColors.primary, AppColors.primarySoft),
      '교체' => (AppColors.ai, AppColors.aiSoft),
      '삭제' => (AppColors.danger, AppColors.dangerSoft),
      '추가' => (AppColors.success, AppColors.successSoft),
      _ => (AppColors.text2, AppColors.field),
    };

/// 06_AI_Change_Preview 의 AI Change Card. 승인 전까지 Planner 는 바뀌지 않는다.
class ProposalCard extends StatelessWidget {
  const ProposalCard({super.key, required this.turn, this.readOnly = false});
  final ChatTurn turn;
  final bool readOnly;

  @override
  Widget build(BuildContext context) {
    final p = turn.proposal!;
    final state = turn.proposalState;
    final rows = p.before != null ? buildChangeRows(p) : <ChangeRow>[];
    final count = rows.isNotEmpty ? rows.length : p.ops.length;

    // 지난 대화의 결정된 제안: 상태만 간단히
    if (rows.isEmpty && state != ProposalState.pending) {
      return _Compact(proposal: p, state: state);
    }
    final faded = state == ProposalState.rejected || state == ProposalState.superseded;
    return Opacity(
      opacity: faded ? 0.7 : 1,
      child: Container(
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppColors.aiLine),
        ),
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            const Icon(Icons.auto_awesome, size: 16, color: AppColors.ai),
            const SizedBox(width: 6),
            Expanded(child: Text('일정 변경 제안', style: AppText.section)),
            _HeaderChip(state: state, count: count),
          ]),
          const SizedBox(height: 6),
          for (final (i, r) in rows.indexed) ...[
            if (i > 0) const Divider(height: 1, color: AppColors.line),
            ChangeRowView(row: r),
          ],
          if (p.before != null && p.after != null) _RouteImpact(proposal: p),
          if (!p.applicable && state == ProposalState.pending)
            Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Text(p.error ?? '지금 일정에는 그대로 적용할 수 없어요. 다시 요청해 주세요.', style: AppText.caption.copyWith(color: AppColors.danger)),
            ),
          if (!readOnly) ...[
            const Divider(height: 24, color: AppColors.line),
            _Actions(turn: turn),
          ],
        ]),
      ),
    );
  }
}

class _HeaderChip extends StatelessWidget {
  const _HeaderChip({required this.state, required this.count});
  final ProposalState state;
  final int count;

  @override
  Widget build(BuildContext context) {
    final (label, fg, bg) = switch (state) {
      ProposalState.applied => ('$count건 적용됨', AppColors.success, AppColors.successSoft),
      ProposalState.rejected => ('거절됨', AppColors.text2, AppColors.field),
      ProposalState.superseded => ('대체됨', AppColors.text2, AppColors.field),
      _ => ('변경 $count건', AppColors.ai, AppColors.aiSoft),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(10)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        if (state == ProposalState.applied) ...[Icon(Icons.check, size: 12, color: fg), const SizedBox(width: 2)],
        Text(label, style: AppText.caption.copyWith(color: fg, fontWeight: FontWeight.w700)),
      ]),
    );
  }
}

class ChangeRowView extends StatelessWidget {
  const ChangeRowView({super.key, required this.row});
  final ChangeRow row;

  @override
  Widget build(BuildContext context) {
    final (fg, bg) = badgeColors(row.badge);
    final hasArrow = row.fromText != null && row.toText != null && row.fromText != row.toText;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(
          margin: const EdgeInsets.only(top: 1),
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
          decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(6)),
          child: Text(row.badge, style: AppText.caption.copyWith(color: fg, fontWeight: FontWeight.w700)),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(row.badge == '교체' ? row.title : row.title,
                style: AppText.section.copyWith(fontSize: 15, decoration: row.strike ? TextDecoration.lineThrough : null, color: row.strike ? AppColors.text3 : AppColors.text)),
            if (hasArrow || row.toText != null || row.fromText != null) ...[
              const SizedBox(height: 4),
              Row(children: [
                if (row.fromText != null)
                  Flexible(
                    child: Text(row.fromText!,
                        overflow: TextOverflow.ellipsis,
                        style: AppText.meta.copyWith(color: AppColors.text3, decoration: hasArrow ? TextDecoration.lineThrough : null)),
                  ),
                if (hasArrow) const Padding(padding: EdgeInsets.symmetric(horizontal: 6), child: Icon(Icons.chevron_right, size: 16, color: AppColors.text3)),
                if (row.toText != null && (hasArrow || row.fromText == null))
                  Flexible(child: Text(row.toText!, overflow: TextOverflow.ellipsis, style: AppText.meta.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700))),
              ]),
            ],
            if (row.reason.isNotEmpty) ...[
              const SizedBox(height: 2),
              Text(row.reason, style: AppText.caption, maxLines: 2, overflow: TextOverflow.ellipsis),
            ],
          ]),
        ),
      ]),
    );
  }
}

/// Route Impact 박스: 이동 시간 변화 · 종료 시각 · 남은 위반.
class _RouteImpact extends StatelessWidget {
  const _RouteImpact({required this.proposal});
  final ProposalDetail proposal;

  @override
  Widget build(BuildContext context) {
    final before = proposal.before!, after = proposal.after!;
    final day = proposal.changes.map((c) => c.day).whereType<int>().firstOrNull ?? 1;
    final b = before.dayOf(day), a = after.dayOf(day);
    if (a == null) return const SizedBox.shrink();
    final delta = a.travelMinutes - (b?.travelMinutes ?? 0);
    final errors = after.violations.where((v) => v.isError && (v.day == null || v.day == day)).length;
    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(color: AppColors.background, borderRadius: BorderRadius.circular(12)),
      child: Row(children: [
        const Icon(Icons.route, size: 18, color: AppColors.text2),
        const SizedBox(width: 8),
        Text('이동 ', style: AppText.meta),
        Text('${delta >= 0 ? '+' : ''}$delta분', style: AppText.meta.copyWith(color: AppColors.text, fontWeight: FontWeight.w700)),
        Flexible(child: Text(' · 총 ${a.travelMinutes}분', style: AppText.caption, overflow: TextOverflow.ellipsis)),
        const Spacer(),
        if (errors > 0) ...[
          const Icon(Icons.warning_amber_rounded, size: 16, color: AppColors.danger),
          const SizedBox(width: 4),
          Text('위반 $errors건', style: AppText.caption.copyWith(color: AppColors.danger, fontWeight: FontWeight.w700)),
        ] else if (a.endAt != null) ...[
          const Icon(Icons.check, size: 16, color: AppColors.success),
          const SizedBox(width: 4),
          Text('Day $day ${a.endAt} 종료', style: AppText.caption.copyWith(color: AppColors.success, fontWeight: FontWeight.w700)),
        ],
      ]),
    );
  }
}

class _Actions extends StatelessWidget {
  const _Actions({required this.turn});
  final ChatTurn turn;

  @override
  Widget build(BuildContext context) {
    final chat = context.watch<ChatController>();
    final plan = context.watch<PlanController>();
    final p = turn.proposal!;
    final details = Row(children: [
      Expanded(child: Text('적용 전까지 Planner는 바뀌지 않아요 · ', style: AppText.caption, overflow: TextOverflow.ellipsis)),
      InkWell(
        onTap: () => showProposalDetails(context, p),
        child: Text('자세히 보기 ›', style: AppText.caption.copyWith(color: AppColors.text, fontWeight: FontWeight.w700)),
      ),
    ]);
    switch (turn.proposalState) {
      case ProposalState.pending:
      case ProposalState.applying:
        return Column(children: [
          details,
          const SizedBox(height: 12),
          Row(children: [
            SizedBox(
              width: 96,
              child: AppButton(
                label: '거절',
                kind: ButtonKind.secondary,
                height: 44,
                onPressed: chat.busy || turn.proposalState == ProposalState.applying
                    ? null
                    : () => chat.reject(turn).catchError((Object e) {
                          if (context.mounted) showError(context, e);
                        }),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: AppButton(
                label: '전체 적용',
                icon: Icons.check,
                height: 44,
                loading: turn.proposalState == ProposalState.applying,
                onPressed: chat.busy || !p.applicable
                    ? null
                    : () => chat.accept(turn).catchError((Object e) {
                          if (context.mounted) showError(context, e);
                        }),
              ),
            ),
          ]),
          if (chat.busy) Padding(padding: const EdgeInsets.only(top: 6), child: Text('AI 응답이 끝나면 적용할 수 있어요', style: AppText.caption)),
        ]);
      case ProposalState.applied:
        final canUndo = plan.lastApplied?.proposal.id == p.id;
        return Row(children: [
          const Icon(Icons.check_circle, size: 16, color: AppColors.success),
          const SizedBox(width: 6),
          Expanded(child: Text('Planner에 반영했어요', style: AppText.caption.copyWith(color: AppColors.success, fontWeight: FontWeight.w700))),
          if (canUndo)
            TextButton.icon(
              onPressed: () => plan.undoLastApplied().catchError((Object e) {
                if (context.mounted) showError(context, e);
              }),
              icon: const Icon(Icons.undo, size: 16),
              label: const Text('실행 취소'),
            ),
        ]);
      case ProposalState.rejected:
      case ProposalState.superseded:
        return Row(children: [
          Expanded(
            child: Text(turn.proposalState == ProposalState.rejected ? '거절했어요. 일정은 그대로예요.' : '새 제안으로 대체됐어요.', style: AppText.caption),
          ),
          TextButton(onPressed: () => showProposalDetails(context, p), child: const Text('다시 보기')),
        ]);
    }
  }
}

class _Compact extends StatelessWidget {
  const _Compact({required this.proposal, required this.state});
  final ProposalDetail proposal;
  final ProposalState state;

  @override
  Widget build(BuildContext context) {
    final (icon, label, color) = switch (state) {
      ProposalState.applied => (Icons.check_circle, '적용됨', AppColors.success),
      ProposalState.rejected => (Icons.cancel_outlined, '거절됨', AppColors.text3),
      _ => (Icons.swap_horiz, '대체됨', AppColors.text3),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(borderRadius: BorderRadius.circular(12), border: Border.all(color: AppColors.aiLine)),
      child: Row(children: [
        const Icon(Icons.auto_awesome, size: 14, color: AppColors.ai),
        const SizedBox(width: 6),
        Expanded(child: Text('일정 변경 제안 · 변경 ${proposal.ops.length}건', style: AppText.label)),
        Icon(icon, size: 16, color: color),
        const SizedBox(width: 4),
        Text(label, style: AppText.caption.copyWith(color: color, fontWeight: FontWeight.w700)),
      ]),
    );
  }
}

/// "자세히 보기": 승인 시 일정(after) 전체와 남는 위반.
Future<void> showProposalDetails(BuildContext context, ProposalDetail p) => showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (ctx) {
        final after = p.after;
        final days = p.changes.map((c) => c.day).whereType<int>().toSet();
        return FractionallySizedBox(
          heightFactor: 0.85,
          child: ListView(padding: const EdgeInsets.fromLTRB(20, 0, 20, 24), children: [
            Text('변경 내역', style: AppText.headline),
            if (p.summary.trim().isNotEmpty) ...[const SizedBox(height: 8), Text(p.summary.trim(), style: AppText.meta)],
            const SizedBox(height: 12),
            for (final c in p.changes)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  const Text('•  '),
                  Expanded(child: Text(c.message, style: AppText.meta.copyWith(color: AppColors.text))),
                ]),
              ),
            if (p.changes.isEmpty) Text('변경 ${p.ops.length}건 (상세 정보는 승인 대기 중일 때만 볼 수 있어요)', style: AppText.meta),
            if (after != null) ...[
              for (final d in after.days.where((d) => days.isEmpty || days.contains(d.day))) ...[
                const SizedBox(height: 16),
                Text('적용 후 Day ${d.day} · ${d.endAt ?? '-'} 종료', style: AppText.section),
                const SizedBox(height: 6),
                for (final i in d.items)
                  ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    leading: SizedBox(width: 48, child: Text(i.startAt ?? '-', style: AppText.meta.copyWith(fontWeight: FontWeight.w700))),
                    title: Text(i.title, style: AppText.label),
                    subtitle: Text('${i.stayMinutes}분${i.travelMinutes == null ? '' : ' · 이동 ${i.travelMinutes}분'}', style: AppText.caption),
                  ),
              ],
              if (after.violations.isNotEmpty) ...[
                const SizedBox(height: 12),
                Text('남는 경고', style: AppText.section),
                for (final v in after.violations)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Icon(Icons.warning_amber_rounded, size: 16, color: v.isError ? AppColors.danger : AppColors.warning),
                      const SizedBox(width: 6),
                      Expanded(child: Text(v.message, style: AppText.meta)),
                    ]),
                  ),
              ],
            ],
          ]),
        );
      },
    );

/// 06B_AI_Place_Recommendation: 추가만 있는 제안을 장소별 카드로. 카드마다 따로 추가/취소.
class RecommendationCards extends StatelessWidget {
  const RecommendationCards({super.key, required this.turn});
  final ChatTurn turn;

  @override
  Widget build(BuildContext context) {
    final chat = context.watch<ChatController>();
    final plan = context.watch<PlanController>();
    final p = turn.proposal!;
    final after = p.after;
    final mode = (after ?? plan.plan)?.travelMode ?? 'walk';
    return Column(children: [
      for (var i = 0; i < p.ops.length; i++)
        Builder(builder: (context) {
          final op = p.ops[i];
          final previewItem = after?.item(-(i + 1));
          final addedId = turn.recommendationAdded[i];
          final realItem = addedId == null ? null : plan.plan?.item(addedId);
          final item = realItem ?? previewItem;
          final busy = turn.recommendationBusy.contains(i);
          final title = item?.title ?? '${op['place_id']}';
          final type = item?.place?.contentType ?? '';
          final travel = item?.travelMinutes;
          final meta = [if (type.isNotEmpty) type, if (travel != null) '${mode == 'car' ? '차량' : '도보'} $travel분'].join(' · ');
          final memo = '${op['memo'] ?? ''}'.trim();
          final reason = memo.isNotEmpty ? memo : (item?.place?.addr ?? '');
          final time = item?.startAt == null ? null : '${item!.startAt} – ${item.endAt}';
          return Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(16), border: Border.all(color: const Color(0xFFECEEF2))),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                Row(children: [
                  PlaceThumb(contentType: type),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(title, style: AppText.section.copyWith(fontSize: 15)),
                      if (meta.isNotEmpty) Text(meta, style: AppText.caption.copyWith(color: AppColors.text2)),
                      if (reason.isNotEmpty) Text(reason, style: AppText.caption, maxLines: 1, overflow: TextOverflow.ellipsis),
                    ]),
                  ),
                ]),
                const SizedBox(height: 10),
                Row(children: [
                  if (time != null)
                    Container(
                      height: 28,
                      padding: const EdgeInsets.symmetric(horizontal: 10),
                      decoration: BoxDecoration(color: AppColors.background, borderRadius: BorderRadius.circular(14)),
                      child: Row(children: [
                        const Icon(Icons.schedule, size: 14, color: AppColors.text2),
                        const SizedBox(width: 4),
                        Text(time, style: AppText.caption.copyWith(color: AppColors.text2)),
                      ]),
                    ),
                  const Spacer(),
                  if (addedId != null) ...[
                    const Icon(Icons.check, size: 16, color: AppColors.success),
                    const SizedBox(width: 4),
                    Text('추가됨', style: AppText.label.copyWith(color: AppColors.success, fontWeight: FontWeight.w700)),
                    TextButton(
                      onPressed: busy ? null : () => chat.cancelRecommendation(turn, i).catchError((Object e) {
                            if (context.mounted) showError(context, e);
                          }),
                      child: Text('취소', style: AppText.label.copyWith(color: AppColors.text3)),
                    ),
                  ] else
                    AppButton(
                      label: '일정에 추가',
                      icon: Icons.add,
                      kind: ButtonKind.soft,
                      height: 36,
                      loading: busy,
                      onPressed: chat.busy || turn.proposalState == ProposalState.superseded
                          ? null
                          : () => chat.addRecommendation(turn, i).catchError((Object e) {
                                if (context.mounted) showError(context, e);
                              }),
                    ),
                ]),
              ]),
            ),
          );
        }),
    ]);
  }
}
