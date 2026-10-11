import 'package:flutter/material.dart';

import '../../core/models/safety.dart';
import '../../core/models/trip.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../../core/widgets/safety_widgets.dart';
import 'chat_controller.dart';

/// 서버가 장소 질문 답변 끝에 붙이는 한 줄 (agent §11.3.1). 앱은 이 줄을 떼어 안내 줄로 그린다.
const pendingNoticeText = '아직 승인 대기 중인 변경안이 있어요.';

/// 답변 끝의 대기 안내 줄을 떼어 낸다 → (본문, 안내가 있었는지).
(String, bool) splitPendingNotice(String text) {
  final t = text.trimRight();
  if (!t.endsWith(pendingNoticeText)) return (text, false);
  return (t.substring(0, t.length - pendingNoticeText.length).trimRight(), true);
}

/// 선제 메시지 카드 (19·22): Agent 가 먼저 말을 건 답을 앞 턴과 떨어진 별도 카드로 묶는다 —
/// 머리줄 + 답변·근거 박스·제안 카드·후속 칩. aiSoft 배경 + aiLine 테두리.
class ProactiveCard extends StatelessWidget {
  const ProactiveCard({super.key, required this.info, required this.children});
  final ProactiveInfo info;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Container(
        key: const ValueKey('proactive-card'),
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 14),
        decoration: BoxDecoration(
          color: AppColors.aiSoft,
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: AppColors.aiLine),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [ProactiveHeader(info: info), ...children]),
      );
}

/// 선제 표시 줄: ✨ "구룡메이트가 먼저 알려드려요 · HH:MM" + 종류 칩.
class ProactiveHeader extends StatelessWidget {
  const ProactiveHeader({super.key, required this.info});
  final ProactiveInfo info;

  @override
  Widget build(BuildContext context) => SizedBox(
        key: const ValueKey('proactive-header'),
        height: 28,
        child: Row(children: [
          const Icon(Icons.auto_awesome, size: 14, color: AppColors.ai),
          const SizedBox(width: 4),
          Expanded(
            child: Text.rich(
              TextSpan(children: [
                TextSpan(text: '구룡메이트가 먼저 알려드려요', style: AppText.caption.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700)),
                if (info.timeText.isNotEmpty) TextSpan(text: ' · ${info.timeText}', style: AppText.caption),
              ]),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          const SizedBox(width: 6),
          ProactiveKindChip(info: info),
        ]),
      );
}

/// 선제 메시지 종류 칩: 지연 = warning(일정 지연 / 밀림이면 일정 밀림), 안전 = danger, 그 밖 = ai.
class ProactiveKindChip extends StatelessWidget {
  const ProactiveKindChip({super.key, required this.info});
  final ProactiveInfo info;

  static String label(ProactiveInfo i) => switch (i.type) {
        'delay' => i.reason == 'behind_schedule' ? '일정 밀림' : '일정 지연',
        'safety_raised' => switch (i.severity) { 'danger' => '안전 · 위험으로 바뀜', 'warning' => '안전 · 주의로 바뀜', _ => '안전 경고' },
        'safety_eased' => '안전 · 풀림',
        'near_hazard' => '위험 구역 근처',
        _ => '상황 알림',
      };

  @override
  Widget build(BuildContext context) {
    final text = label(info);
    return switch (info.type) {
      'delay' => InfoChip(key: const ValueKey('proactive-kind'), label: text, icon: Icons.schedule, fg: AppColors.warning, bg: AppColors.warningSoft),
      'safety_raised' || 'near_hazard' =>
        InfoChip(key: const ValueKey('proactive-kind'), label: text, icon: Icons.shield_outlined, fg: AppColors.danger, bg: AppColors.dangerSoft),
      'safety_eased' => InfoChip(key: const ValueKey('proactive-kind'), label: text, icon: Icons.shield_outlined, fg: AppColors.success, bg: AppColors.successSoft),
      _ => InfoChip(key: const ValueKey('proactive-kind'), label: text, icon: Icons.notifications_none, fg: AppColors.ai, bg: AppColors.surface),
    };
  }
}

/// 근거 박스 (22, Components_Companion › Hazard Evidence Box): safety_raised payload 의 hazards (caution·danger).
class HazardEvidenceBox extends StatelessWidget {
  const HazardEvidenceBox({super.key, required this.event});
  final TripEvent event;

  static List<Hazard> hazardsOf(TripEvent e) {
    final out = <Hazard>[];
    final seen = <String>{};
    for (final ch in (e.payload['changes'] as List?) ?? const []) {
      final item = ch is Map ? ch['item'] : null;
      for (final h in (item is Map ? item['hazards'] as List? : null) ?? const []) {
        if (h is! Map) continue;
        final hz = Hazard.fromJson(Map<String, dynamic>.from(h));
        if (hz.level == 'info' || !seen.add(hz.reason)) continue;
        out.add(hz);
      }
    }
    out.sort((a, b) => (b.level == 'danger' ? 1 : 0).compareTo(a.level == 'danger' ? 1 : 0));
    return out;
  }

  @override
  Widget build(BuildContext context) {
    final hz = hazardsOf(event);
    if (hz.isEmpty) return const SizedBox.shrink();
    final shown = hz.take(4).toList();
    final sources = {for (final h in shown) h.source}.where((s) => s.isNotEmpty).join(' · ');
    return Container(
      key: const ValueKey('hazard-evidence'),
      margin: const EdgeInsets.only(top: 12),
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      decoration: BoxDecoration(color: const Color(0xFFFFF8F8), borderRadius: BorderRadius.circular(14), border: Border.all(color: AppColors.dangerLine)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          Text('근거', style: AppText.caption.copyWith(color: AppColors.danger, fontWeight: FontWeight.w700)),
          if (event.timeText.isNotEmpty) Text(' · ${event.timeText} 기준 예보·실측', style: AppText.caption),
        ]),
        const SizedBox(height: 6),
        for (final h in shown)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Row(children: [
              Icon(hazardIcon(h.type), size: 16, color: h.level == 'danger' ? AppColors.danger : AppColors.warning),
              const SizedBox(width: 8),
              Expanded(child: Text(h.reason, style: AppText.meta.copyWith(color: AppColors.text), maxLines: 2, overflow: TextOverflow.ellipsis)),
              const SizedBox(width: 6),
              RiskChip(risk: h.level, label: riskWord(h.level), height: 22),
            ]),
          ),
        if (hz.length > shown.length) Text('그 밖에 ${hz.length - shown.length}개', style: AppText.caption),
        if (sources.isNotEmpty) ...[const SizedBox(height: 4), Text(sources, style: AppText.caption)],
      ]),
    );
  }
}

/// 장소 질문 답변 끝: "아직 승인 대기 중인 변경안이 있어요 · 제안 보기 ›".
class PendingNoticeLine extends StatelessWidget {
  const PendingNoticeLine({super.key, required this.onTap});
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => InkWell(
        key: const ValueKey('pending-notice'),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Row(children: [
            const Icon(Icons.auto_awesome, size: 14, color: AppColors.ai),
            const SizedBox(width: 4),
            Expanded(child: Text('아직 승인 대기 중인 변경안이 있어요', style: AppText.caption.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700))),
            if (onTap != null) Text('제안 보기 ›', style: AppText.caption.copyWith(color: AppColors.text, fontWeight: FontWeight.w700)),
          ]),
        ),
      );
}
