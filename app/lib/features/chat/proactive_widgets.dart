import 'package:flutter/material.dart';

import '../../core/models/safety.dart';
import '../../core/models/trip.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/safety_widgets.dart';
import '../trip/trip_overlay.dart';

/// 서버가 장소 질문 답변 끝에 붙이는 한 줄 (agent §11.3.1). 앱은 이 줄을 떼어 안내 줄로 그린다.
const pendingNoticeText = '아직 승인 대기 중인 변경안이 있어요.';

/// 답변 끝의 대기 안내 줄을 떼어 낸다 → (본문, 안내가 있었는지).
(String, bool) splitPendingNotice(String text) {
  final t = text.trimRight();
  if (!t.endsWith(pendingNoticeText)) return (text, false);
  return (t.substring(0, t.length - pendingNoticeText.length).trimRight(), true);
}

/// 선제 표시 줄 (19·22): ✨ "구룡메이트가 먼저 알려드려요 · HH:MM" + 이벤트 칩.
class ProactiveHeader extends StatelessWidget {
  const ProactiveHeader({super.key, required this.event});
  final TripEvent event;

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
                if (event.timeText.isNotEmpty) TextSpan(text: ' · ${event.timeText}', style: AppText.caption),
              ]),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          const SizedBox(width: 6),
          EventKindChip(event: event),
        ]),
      );
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
