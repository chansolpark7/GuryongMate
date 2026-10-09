import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/models/agent.dart';
import '../../core/models/plan.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../planner/plan_controller.dart';
import 'chat_controller.dart';
import 'compare_screen.dart';
import 'proposal_actions.dart';

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

/// planner Change 목록 → 카드 줄. 같은 자리의 삭제+추가는 "교체"로 묶는다. day_end 는 Route Impact 박스에서 보여 준다.
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
          title: '${b.startAt ?? ''} ${b.title}'.trim(),
          fromText: b.title,
          toText: ai.title,
          reason: ai.memo.isNotEmpty ? ai.memo : '${ai.place?.contentType ?? ''} · ${ai.stayMinutes}분',
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
    final reason = entry.value.map((c) => stripChangePrefix(c.message)).join(' · ');
    if (kinds.contains('added')) {
      rows.add(ChangeRow(badge: '추가', title: a?.title ?? reason, toText: a?.startAt, reason: a == null ? '' : (a.memo.isNotEmpty ? a.memo : '${a.stayMinutes}분'), itemId: id, day: a?.day));
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
      rows.add(ChangeRow(badge: '조건', title: ruleText(c.message), reason: c.kind == 'rule_added' ? '조건 추가' : '조건 삭제'));
    } else if (c.kind == 'plan') {
      rows.add(ChangeRow(badge: '여행', title: stripChangePrefix(c.message)));
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

/// "1일차 종료: 17:30 → 17:22" → ("17:30", "17:22")
(String?, String?) dayEndChange(ProposalDetail p) {
  final c = p.changes.where((c) => c.kind == 'day_end').firstOrNull;
  if (c == null) return (null, null);
  final m = RegExp(r'([0-9:]+|없음)\s*→\s*([0-9:]+|없음)').firstMatch(c.message);
  if (m == null) return (null, null);
  String? v(String s) => s == '없음' ? null : s;
  return (v(m.group(1)!), v(m.group(2)!));
}

/// 06_AI_Change_Preview 의 AI Change Card. 승인 전까지 일정은 바뀌지 않는다.
class ProposalCard extends StatelessWidget {
  const ProposalCard({super.key, required this.entry, this.readOnly = false});
  final ProposalEntry entry;
  final bool readOnly;

  @override
  Widget build(BuildContext context) {
    final p = entry.proposal;
    final state = entry.state;
    if (state == ProposalState.superseded) return _Compact(entry: entry);
    final rows = p.before != null ? buildChangeRows(p) : <ChangeRow>[];
    final count = rows.isNotEmpty ? rows.length : p.changes.length;
    return Opacity(
      opacity: state == ProposalState.rejected ? 0.7 : 1,
      child: Container(
        decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(16), border: Border.all(color: AppColors.aiLine)),
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
          if (p.before != null && p.after != null) RouteImpact(proposal: p),
          if (state == ProposalState.pending && p.progressChanged.isNotEmpty)
            const _Note(text: '그사이 다녀온 곳이 있어 이 제안을 그대로 반영할 수 없어요')
          else if (state == ProposalState.pending && p.baseChanged)
            const _Note(text: '제안 이후 일정이 바뀌었어요 · 적용 전에 확인해요'),
          if (!readOnly) ...[
            const Divider(height: 24, color: AppColors.line),
            _Actions(entry: entry),
          ],
        ]),
      ),
    );
  }
}

class _Note extends StatelessWidget {
  const _Note({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.only(top: 8),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        decoration: BoxDecoration(color: AppColors.warningSoft, borderRadius: BorderRadius.circular(10)),
        child: Row(children: [
          const Icon(Icons.info_outline, size: 16, color: AppColors.warning),
          const SizedBox(width: 6),
          Expanded(child: Text(text, style: AppText.caption.copyWith(color: AppColors.warning, fontWeight: FontWeight.w700))),
        ]),
      );
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
            Text(row.title,
                style: AppText.section.copyWith(fontSize: 15, decoration: row.strike ? TextDecoration.lineThrough : null, color: row.strike ? AppColors.text3 : AppColors.text)),
            if (hasArrow || row.toText != null || row.fromText != null) ...[
              const SizedBox(height: 4),
              Row(children: [
                if (row.fromText != null)
                  Flexible(
                    child: Text(row.fromText!,
                        overflow: TextOverflow.ellipsis, style: AppText.meta.copyWith(color: AppColors.text3, decoration: hasArrow && row.badge != '교체' ? TextDecoration.lineThrough : null)),
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

/// Route Impact 박스: 이동 시간 변화 · 종료 시각(day_end) · 남은 위반.
class RouteImpact extends StatelessWidget {
  const RouteImpact({super.key, required this.proposal});
  final ProposalDetail proposal;

  @override
  Widget build(BuildContext context) {
    final before = proposal.before!, after = proposal.after!;
    final day = proposal.day;
    final b = before.dayOf(day), a = after.dayOf(day);
    if (a == null) return const SizedBox.shrink();
    final delta = a.travelMinutes - (b?.travelMinutes ?? 0);
    final errors = after.violations.where((v) => v.isError && (v.day == null || v.day == day)).length;
    final (_, endTo) = dayEndChange(proposal);
    final end = endTo ?? a.endAt;
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
        ] else if (end != null) ...[
          const Icon(Icons.check, size: 16, color: AppColors.success),
          const SizedBox(width: 4),
          Text('$end 종료', style: AppText.caption.copyWith(color: AppColors.success, fontWeight: FontWeight.w700)),
        ],
      ]),
    );
  }
}

class _Actions extends StatelessWidget {
  const _Actions({required this.entry});
  final ProposalEntry entry;

  @override
  Widget build(BuildContext context) {
    final chat = context.watch<ChatController>();
    final plan = context.read<PlanController>();
    final p = entry.proposal;
    final details = Row(children: [
      Expanded(child: Text('적용 전까지 일정은 바뀌지 않아요 · ', style: AppText.caption, overflow: TextOverflow.ellipsis)),
      InkWell(
        onTap: () => openCompare(context, chat: chat, plan: plan, entry: entry),
        child: Text('비교 보기 ›', style: AppText.caption.copyWith(color: AppColors.text, fontWeight: FontWeight.w700)),
      ),
    ]);
    switch (entry.state) {
      case ProposalState.pending:
      case ProposalState.applying:
        final applying = entry.state == ProposalState.applying;
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
                onPressed: chat.busy || applying ? null : () => rejectWithFlow(context, chat, entry),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: AppButton(
                label: '전체 적용',
                icon: Icons.check,
                height: 44,
                loading: applying,
                onPressed: chat.busy ? null : () => acceptWithFlow(context, chat, entry),
              ),
            ),
          ]),
          if (chat.busy) Padding(padding: const EdgeInsets.only(top: 6), child: Text('구룡메이트가 답을 마치면 적용할 수 있어요', style: AppText.caption)),
        ]);
      case ProposalState.applied:
        return Row(children: [
          const Icon(Icons.check_circle, size: 16, color: AppColors.success),
          const SizedBox(width: 6),
          Expanded(child: Text(p.decision == 'overwritten' ? '카드 그대로 일정에 반영했어요' : '일정에 반영했어요', style: AppText.caption.copyWith(color: AppColors.success, fontWeight: FontWeight.w700))),
          TextButton(onPressed: () => openCompare(context, chat: chat, plan: plan, entry: entry), child: const Text('비교 보기')),
        ]);
      case ProposalState.rejected:
      case ProposalState.superseded:
        return Row(children: [
          Expanded(child: Text(entry.state == ProposalState.rejected ? '거절했어요. 일정은 그대로예요.' : '새 제안으로 대체됐어요.', style: AppText.caption)),
          TextButton(onPressed: () => openCompare(context, chat: chat, plan: plan, entry: entry), child: const Text('다시 보기')),
        ]);
    }
  }
}

/// 대체된 제안 (20 의 접힘 카드).
class _Compact extends StatelessWidget {
  const _Compact({required this.entry});
  final ProposalEntry entry;

  @override
  Widget build(BuildContext context) {
    final p = entry.proposal;
    final title = p.summary.trim().split('\n').first;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(color: AppColors.background, borderRadius: BorderRadius.circular(12)),
      child: Row(children: [
        const Icon(Icons.swap_horiz, size: 16, color: AppColors.text3),
        const SizedBox(width: 6),
        Expanded(child: Text(title.isEmpty ? '일정 변경 제안 · 변경 ${p.changes.length}건' : title, style: AppText.caption.copyWith(color: AppColors.text2), maxLines: 1, overflow: TextOverflow.ellipsis)),
        const SizedBox(width: 6),
        Text('새 제안으로 대체됨', style: AppText.caption.copyWith(color: AppColors.text3, fontWeight: FontWeight.w700)),
      ]),
    );
  }
}

/// 06B_AI_Place_Recommendation: 후보마다 state 로 버튼 — not_in_plan [일정에 추가] / in_plan [✓ 추가됨 · 취소] / day_out_of_range 버튼 없음.
class RecommendationCard extends StatelessWidget {
  const RecommendationCard({super.key, required this.entry});
  final RecommendationEntry entry;

  @override
  Widget build(BuildContext context) {
    final chat = context.watch<ChatController>();
    final plan = context.watch<PlanController>();
    final rec = entry.recommendation;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      for (final c in rec.candidates)
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: _CandidateCard(
            candidate: c,
            item: c.itemId == null ? null : plan.plan?.item(c.itemId!),
            travelMode: plan.plan?.travelMode ?? 'walk',
            busy: entry.busy.contains(c.id),
            disabled: chat.busy,
            onToggle: () => chat.toggleCandidate(entry, c).catchError((Object e) {
              if (context.mounted) showError(context, e);
            }),
          ),
        ),
    ]);
  }
}

class _CandidateCard extends StatelessWidget {
  const _CandidateCard({required this.candidate, required this.item, required this.travelMode, required this.busy, required this.disabled, required this.onToggle});
  final RecommendationCandidate candidate;
  final PlanItem? item;
  final String travelMode;
  final bool busy;
  final bool disabled;
  final VoidCallback onToggle;

  @override
  Widget build(BuildContext context) {
    final c = candidate;
    final type = c.place?.contentType ?? '';
    final leg = item?.legMode ?? travelMode;
    final travel = item?.travelMinutes;
    final meta = [if (type.isNotEmpty) type, if (travel != null) '${leg == 'car' ? '차량' : '도보'} ${item!.estimated ? '약 ' : ''}$travel분'].join(' · ');
    final time = c.startAt != null ? '${c.startAt} – ${c.endAt}' : (c.timeHint == null ? null : '${c.timeHint} 무렵 · ${c.stayMinutes}분');
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(16), border: Border.all(color: const Color(0xFFECEEF2))),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          PlaceThumb(contentType: type),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(c.title, style: AppText.section.copyWith(fontSize: 15)),
              if (meta.isNotEmpty) Text(meta, style: AppText.caption.copyWith(color: AppColors.text2)),
              if (c.reason.isNotEmpty) Text(c.reason, style: AppText.caption, maxLines: 2, overflow: TextOverflow.ellipsis),
            ]),
          ),
        ]),
        const SizedBox(height: 10),
        Row(children: [
          if (time != null)
            Flexible(
              child: Container(
                height: 28,
                padding: const EdgeInsets.symmetric(horizontal: 10),
                decoration: BoxDecoration(color: AppColors.background, borderRadius: BorderRadius.circular(14)),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  const Icon(Icons.schedule, size: 14, color: AppColors.text2),
                  const SizedBox(width: 4),
                  Flexible(child: Text(time, style: AppText.caption.copyWith(color: AppColors.text2), overflow: TextOverflow.ellipsis)),
                ]),
              ),
            ),
          const Spacer(),
          if (c.inPlan) ...[
            const Icon(Icons.check, size: 16, color: AppColors.success),
            const SizedBox(width: 4),
            Text('추가됨', style: AppText.label.copyWith(color: AppColors.success, fontWeight: FontWeight.w700)),
            TextButton(
              onPressed: busy || disabled ? null : onToggle,
              child: busy
                  ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                  : Text('취소', style: AppText.label.copyWith(color: AppColors.text3)),
            ),
          ] else if (c.notInPlan)
            AppButton(label: '일정에 추가', icon: Icons.add, kind: ButtonKind.soft, height: 36, loading: busy, onPressed: disabled ? null : onToggle)
          else
            Text('여행 기간 밖이에요', style: AppText.caption),
        ]),
      ]),
    );
  }
}
