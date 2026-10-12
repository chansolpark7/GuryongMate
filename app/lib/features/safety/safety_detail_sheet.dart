import 'package:flutter/material.dart';

import '../../core/format.dart';
import '../../core/models/plan.dart';
import '../../core/models/safety.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../../core/widgets/safety_widgets.dart';
import '../planner/plan_controller.dart';

/// 26_Safety_Detail (Figma 73:4400) — 배지 하나 뒤의 이유(근거·괜찮은 시간대·가는 길·우회). Bottom Sheet (top 260).
Future<void> showSafetyDetailSheet(BuildContext context, PlanController c, PlanItem item) {
  final visit = c.safety?.visit(item.id);
  if (visit == null) return Future.value();
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder: (ctx) => SafetyDetailSheet(
      item: item,
      visit: visit,
      travel: c.safety?.travel(item.id),
      previous: _previous(c, item),
      checkedAt: c.safety?.checkedAt,
      onAskAi: (text) {
        Navigator.pop(ctx);
        c.openAi(prefill: text, send: true);
      },
    ),
  );
}

PlanItem? _previous(PlanController c, PlanItem item) {
  final items = c.plan?.dayOf(item.day)?.items.where((i) => !i.skipped).toList() ?? const <PlanItem>[];
  final idx = items.indexWhere((i) => i.id == item.id);
  return idx > 0 ? items[idx - 1] : null;
}

class SafetyDetailSheet extends StatelessWidget {
  const SafetyDetailSheet({super.key, required this.item, required this.visit, required this.travel, required this.previous, required this.checkedAt, required this.onAskAi});
  final PlanItem item;
  final SafetyItem visit;
  final SafetyItem? travel;
  final PlanItem? previous;
  final DateTime? checkedAt;
  final ValueChanged<String> onAskAi;

  String _confidence() {
    if (visit.horizon == 'now') return '실측·24시간 안 예보';
    if (visit.horizon == 'short_range') {
      final d = visit.date, t = toMinutes(visit.startAt);
      if (d != null && t != null) {
        final at = DateTime(d.year, d.month, d.day).add(Duration(minutes: t));
        return at.difference(checkedAt ?? DateTime.now()).inHours <= 24 ? '신뢰도 높음 · 24시간 안 예보' : '신뢰도 보통';
      }
      return '신뢰도 보통';
    }
    return '신뢰도 낮음';
  }

  @override
  Widget build(BuildContext context) {
    final v = visit;
    final assessed = v.risk != null;
    final ask = '${item.title} ${item.startAt ?? ''} 일정을 안전하게 바꿔줘';
    return Container(
      constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.72),
      decoration: const BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      child: SafeArea(
        top: false,
        child: ListView(shrinkWrap: true, padding: const EdgeInsets.fromLTRB(20, 0, 20, 16), children: [
          const Grabber(),
          Row(crossAxisAlignment: CrossAxisAlignment.baseline, textBaseline: TextBaseline.alphabetic, children: [
            Flexible(child: Text(item.title, style: AppText.title.copyWith(fontSize: 20, height: 28 / 20))),
            const SizedBox(width: 8),
            Text('${item.startAt ?? ''} – ${item.endAt ?? ''}', style: AppText.meta),
          ]),
          const SizedBox(height: 10),
          Wrap(spacing: 6, runSpacing: 6, children: [
            RiskChip(risk: v.risk, label: v.risk == null ? (riskBadgeLabel(v) ?? '판단 불가') : riskWord(v.risk), height: 28),
            for (final s in v.sensitivity) _GreyChip(s),
            if (assessed) _GreyChip(_confidence()),
          ]),
          if (!assessed) ...[
            const SizedBox(height: 16),
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(color: AppColors.field, borderRadius: BorderRadius.circular(12)),
              child: Text(
                v.unassessedReason == 'beyond_forecast'
                    ? '아직 예보 전이라 판단하지 않았어요.${v.recheckFrom == null ? '' : ' ${two(v.recheckFrom!.month)}/${two(v.recheckFrom!.day)}부터 확인할 수 있어요.'}'
                    : '데이터를 받지 못해 판단하지 못했어요. 안전하다는 뜻이 아니에요.',
                style: AppText.meta.copyWith(color: AppColors.text),
              ),
            ),
          ],
          if (v.hazards.isNotEmpty || v.notes.isNotEmpty) ...[
            const SizedBox(height: 18),
            Text('근거', style: AppText.label.copyWith(color: AppColors.text2, fontWeight: FontWeight.w700)),
            const SizedBox(height: 4),
            for (final h in v.hazards) _ReasonRow(icon: hazardIcon(h.type), reason: h.reason, source: h.source, level: h.level),
            for (final n in v.notes) _ReasonRow(icon: n.contains('만조') ? Icons.schedule : Icons.info_outline, reason: n, source: n.contains('만조') ? '국립해양조사원 조석예보 (참고)' : '참고', level: 'info'),
          ],
          if (v.dataMissing) Padding(padding: const EdgeInsets.only(top: 6), child: Text('일부 데이터가 빠져 실제로는 더 높을 수 있어요', style: AppText.caption.copyWith(color: AppColors.warning))),
          if (v.betterTimes.isNotEmpty) ...[
            const SizedBox(height: 16),
            Text('괜찮은 시간대', style: AppText.label.copyWith(color: AppColors.text2, fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            Wrap(spacing: 8, runSpacing: 8, children: [
              for (final w in v.betterTimes)
                ActionChip(
                  label: Text('${windowLabel(w)}${w.hasInfo ? ' · 참고 있음' : ''}', style: AppText.caption.copyWith(color: AppColors.success, fontWeight: FontWeight.w700)),
                  backgroundColor: AppColors.successSoft,
                  side: BorderSide.none,
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                  onPressed: () => onAskAi('$ask ${windowLabel(w)}로'),
                ),
            ]),
          ],
          if (travel != null && previous != null) ...[
            const SizedBox(height: 16),
            Text('가는 길 · ${previous!.title} → ${item.title} ${item.legMode == 'car' ? '차량' : '도보'} ${item.travelMinutes ?? '-'}분',
                style: AppText.label.copyWith(color: AppColors.text2, fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(color: AppColors.background, borderRadius: BorderRadius.circular(12)),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  RiskBadge(item: travel!, compact: true),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      travel!.topHazard?.reason ?? (travel!.risk == 'safe' ? '경로에 주의·위험 신호가 없어요' : (riskBadgeLabel(travel!) ?? '')),
                      style: AppText.meta.copyWith(color: AppColors.text),
                    ),
                  ),
                ]),
                if (travel!.detour?.detourMinutes != null) ...[
                  const SizedBox(height: 6),
                  Text('우회하면 ${travel!.detour!.detourMinutes}분 더 · ${travel!.detour!.avoided.join(', ')}을(를) 지나지 않아요', style: AppText.caption.copyWith(color: AppColors.text2)),
                ] else if (travel!.detour?.note != null) ...[
                  const SizedBox(height: 6),
                  Text(travel!.detour!.note!, style: AppText.caption),
                ],
              ]),
            ),
          ],
          const SizedBox(height: 14),
          Text('예보는 바뀔 수 있어요${checkedAt == null ? '' : ' · ${two(kst(checkedAt!).month)}/${two(kst(checkedAt!).day)} ${hhmm(checkedAt!)} 확인'}', style: AppText.caption),
          if (assessed) ...[
            const SizedBox(height: 16),
            AppButton(label: 'AI에게 대안 묻기', icon: Icons.auto_awesome, kind: ButtonKind.aiSoft, onPressed: () => onAskAi(ask)),
          ],
        ]),
      ),
    );
  }
}

class _GreyChip extends StatelessWidget {
  const _GreyChip(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Container(
        height: 28,
        padding: const EdgeInsets.symmetric(horizontal: 10),
        decoration: BoxDecoration(color: AppColors.field, borderRadius: BorderRadius.circular(14)),
        child: Center(widthFactor: 1, child: Text(text, style: AppText.caption.copyWith(color: AppColors.text2, fontWeight: FontWeight.w500))),
      );
}

class _ReasonRow extends StatelessWidget {
  const _ReasonRow({required this.icon, required this.reason, required this.source, required this.level});
  final IconData icon;
  final String reason;
  final String source;
  final String level;

  @override
  Widget build(BuildContext context) {
    final (word, color) = switch (level) {
      'danger' => ('위험', AppColors.danger),
      'caution' => ('주의', AppColors.warning),
      _ => ('참고', AppColors.text3),
    };
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(children: [
        Container(
          width: 36,
          height: 36,
          decoration: BoxDecoration(color: AppColors.background, borderRadius: BorderRadius.circular(10)),
          child: Icon(icon, size: 20, color: AppColors.text2),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(reason, style: AppText.label),
            Text(source, style: AppText.caption),
          ]),
        ),
        const SizedBox(width: 8),
        Text(word, style: AppText.caption.copyWith(color: color, fontWeight: FontWeight.w700)),
      ]),
    );
  }
}
