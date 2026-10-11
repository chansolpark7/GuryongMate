import 'package:flutter/material.dart';

import '../format.dart';
import '../models/safety.dart';
import '../theme/app_theme.dart';

/// 위험 단계 색 (안전 의미에만 쓴다, README §6.2). null 은 절대 Green 이 아니다.
(Color fg, Color bg, Color line) riskColors(String? risk) => switch (risk) {
      'safe' => (AppColors.success, AppColors.successSoft, const Color(0xFFCDEFD9)),
      'caution' => (AppColors.warning, AppColors.warningSoft, AppColors.warningLine),
      'danger' => (AppColors.danger, AppColors.dangerSoft, AppColors.dangerLine),
      _ => (AppColors.text2, AppColors.field, AppColors.line),
    };

String riskWord(String? risk) => switch (risk) { 'safe' => '안전', 'caution' => '주의', 'danger' => '위험', _ => '판단 불가' };

IconData hazardIcon(String type) => switch (type) {
      'wind' => Icons.air,
      'wave' || 'tide' => Icons.waves,
      'rain' => Icons.umbrella_outlined,
      'flood' => Icons.flood_outlined,
      'warning' => Icons.campaign_outlined,
      'cctv' || 'breakwater' => Icons.videocam_outlined,
      _ => Icons.info_outline,
    };

String _num(RegExpMatch m) => m.group(1)!;

/// 근거 한 줄을 배지용 짧은 값으로 ("풍속 15m/s", "파고 3.2m", "시간당 10mm", "수위 등급 2", "풍랑주의보").
/// 숫자를 못 뽑으면 종류 이름. 서버 문구(advice 등 LLM 지시문)는 쓰지 않는다 (25 근거 규칙).
String shortReason(Hazard h) {
  final r = h.reason;
  RegExpMatch? m;
  switch (h.type) {
    case 'wind':
      m = RegExp(r'(\d+(?:\.\d+)?)\s*m/s').firstMatch(r);
      return m == null ? '바람' : '풍속 ${_num(m)}m/s';
    case 'wave':
      m = RegExp(r'(\d+(?:\.\d+)?)\s*m(?!m)').firstMatch(r);
      return m == null ? '파도' : '파고 ${_num(m)}m';
    case 'rain':
      m = RegExp(r'(\d+(?:\.\d+)?)\s*mm').firstMatch(r);
      if (m == null) return '비';
      if (h.level == 'info' && (double.tryParse(_num(m)) ?? 0) < 10) return '약한 비';
      return '시간당 ${_num(m)}mm';
    case 'flood':
      m = RegExp(r'등급\s*(\d+)').firstMatch(r);
      return m == null ? '침수' : '수위 등급 ${_num(m)}';
    case 'warning':
      m = RegExp(r'(\S*(?:주의보|경보))').firstMatch(r);
      return m == null ? '특보' : m.group(1)!;
    case 'cctv':
      return r.contains('실족') ? '실족 감지' : '위험구역 접근';
  }
  return switch (h.type) { 'tide' => '만조', _ => '참고' };
}

/// 위험 배지 문구 (25 Risk Badge 표). past 면 null(숨김).
String? riskBadgeLabel(SafetyItem s) {
  switch (s.risk) {
    case 'safe':
      return s.indoor ? '안전 · 실내' : '안전';
    case 'caution':
    case 'danger':
      final h = s.topHazard;
      return h == null ? riskWord(s.risk) : '${riskWord(s.risk)} · ${shortReason(h)}';
  }
  return switch (s.unassessedReason) {
    'past' => null,
    'beyond_forecast' => s.recheckFrom == null ? '예보 전' : '예보 전 · ${two(s.recheckFrom!.month)}/${two(s.recheckFrom!.day)}부터',
    'data_missing' => '판단 불가 · 데이터 없음',
    _ => '판단 불가',
  };
}

/// Risk Badge (Components_Companion). h24 r12, 아이콘 14.
class RiskBadge extends StatelessWidget {
  const RiskBadge({super.key, required this.item, this.onTap, this.compact = false});
  final SafetyItem item;
  final VoidCallback? onTap;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final label = riskBadgeLabel(item);
    if (label == null) return const SizedBox.shrink();
    return RiskChip(risk: item.risk, label: compact ? riskWord(item.risk) : label, onTap: onTap, unassessed: item.risk == null);
  }
}

class RiskChip extends StatelessWidget {
  const RiskChip({super.key, required this.risk, required this.label, this.onTap, this.unassessed = false, this.height = 24});
  final String? risk;
  final String label;
  final VoidCallback? onTap;
  final bool unassessed;
  final double height;

  @override
  Widget build(BuildContext context) {
    final (fg, bg, _) = riskColors(risk);
    final icon = switch (risk) {
      'safe' => Icons.verified_user_outlined,
      'caution' || 'danger' => Icons.warning_amber_rounded,
      _ => Icons.help_outline,
    };
    return Material(
      color: bg,
      borderRadius: BorderRadius.circular(height / 2),
      child: InkWell(
        borderRadius: BorderRadius.circular(height / 2),
        onTap: onTap,
        child: Container(
          height: height,
          padding: const EdgeInsets.symmetric(horizontal: 8),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(icon, size: 14, color: fg),
            const SizedBox(width: 4),
            Flexible(child: Text(label, overflow: TextOverflow.ellipsis, style: AppText.caption.copyWith(color: fg, fontWeight: FontWeight.w700))),
          ]),
        ),
      ),
    );
  }
}

/// safe + info 근거가 있을 때 배지 옆 회색 참고 칩.
class InfoHazardChip extends StatelessWidget {
  const InfoHazardChip({super.key, required this.hazard, this.onTap});
  final Hazard hazard;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => Material(
        color: AppColors.field,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: onTap,
          child: Container(
            height: 24,
            padding: const EdgeInsets.symmetric(horizontal: 8),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(hazardIcon(hazard.type), size: 14, color: AppColors.text2),
              const SizedBox(width: 4),
              Text('참고 · ${shortReason(hazard)}', style: AppText.caption.copyWith(color: AppColors.text2, fontWeight: FontWeight.w500)),
            ]),
          ),
        ),
      );
}

/// 배지 자리 스켈레톤 칩 (안전 로딩 중).
class SkeletonChip extends StatelessWidget {
  const SkeletonChip({super.key, this.width = 56});
  final double width;

  @override
  Widget build(BuildContext context) => Container(
        width: width,
        height: 24,
        decoration: BoxDecoration(color: AppColors.line, borderRadius: BorderRadius.circular(12)),
      );
}

/// "10/10 12:00–15:00"
String windowLabel(TimeWindow w) {
  final s = w.start.toLocal(), e = w.end.toLocal();
  return '${two(s.month)}/${two(s.day)} ${two(s.hour)}:${two(s.minute)}–${two(e.hour)}:${two(e.minute)}';
}

String hhmm(DateTime t) => '${two(t.toLocal().hour)}:${two(t.toLocal().minute)}';
