import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/format.dart';
import '../../core/models/plan.dart';
import '../../core/models/safety.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../../core/widgets/safety_widgets.dart';
import '../safety/outlook_sheet.dart';
import '../safety/safety_detail_sheet.dart';
import 'item_actions.dart';
import 'place_search_sheet.dart';
import 'plan_controller.dart';
import 'plan_settings.dart';

/// 04_Planner → 25_Planner_Safety (Figma 73:4137) / 08_Planner_After_Apply.
/// 날씨·혼잡 칩 자리에 safety 판단(위험 3단계 + 근거), 날씨 배너 자리에 안전 경고 배너, 구간 수단.
class PlannerScreen extends StatelessWidget {
  const PlannerScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final c = context.watch<PlanController>();
    final plan = c.plan;
    if (plan == null) {
      return Scaffold(
        body: SafeArea(
          child: Column(children: [
            const _AppBar(title: '', subtitle: '', onMore: null),
            Expanded(
              child: c.loadError != null
                  ? StateView(icon: Icons.dns_outlined, title: '일정을 불러오지 못했어요', message: userMessage(c.loadError!), actionLabel: '다시 시도', onAction: c.load)
                  : const Center(child: CircularProgressIndicator()),
            ),
          ]),
        ),
      );
    }
    final day = c.currentDay;
    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: Column(children: [
          _AppBar(
            title: plan.title,
            subtitle: plan.numDays == 1
                ? '${plan.startDate.month}/${two(plan.startDate.day)} (${weekdayKo(plan.startDate)}) · 당일 여행'
                : '${dateRange(plan.startDate, plan.endDate)} · ${nightsDays(plan.startDate, plan.endDate)}',
            onMore: () => showPlanMenu(context, c),
          ),
          _DaySelector(plan: plan, selected: c.day, onSelect: c.setDay, trailing: _SafetyLine(controller: c, plan: plan)),
          if (c.busy || (c.safetyLoading && c.safety != null)) const LinearProgressIndicator(minHeight: 2) else const SizedBox(height: 2),
          _ContextBanner(controller: c, plan: plan),
          Expanded(
            child: RefreshIndicator(
              onRefresh: () async {
                try {
                  await c.load(silent: true);
                  await c.loadSafety();
                } catch (e) {
                  if (context.mounted) showError(context, e);
                }
              },
              child: day == null || day.items.isEmpty ? _EmptyDay(day: c.day) : _Timeline(controller: c, plan: plan, day: day),
            ),
          ),
        ]),
      ),
    );
  }
}

/// 우하단 "일정 추가" + AI FAB. 셸 Scaffold 에 두어 스낵바가 뜨면 함께 위로 비켜난다 (Spec 08).
class PlannerFabs extends StatelessWidget {
  const PlannerFabs({super.key, this.hasSuggestion = false});

  /// 동행 중 대기 선제 제안 → FAB 빨간 점 (14-S3).
  final bool hasSuggestion;

  @override
  Widget build(BuildContext context) {
    final c = context.read<PlanController>();
    return Row(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.center, children: [
      _AddButton(onTap: () => openAddPlace(context, c)),
      const SizedBox(width: 10),
      AiFab(onTap: () => c.openAi(), hasSuggestion: hasSuggestion),
    ]);
  }
}

Future<void> openAddPlace(BuildContext context, PlanController c) async {
  final picked = await showPlaceSearchSheet(context, c);
  if (picked == null || !context.mounted) return;
  try {
    await c.addPlace(picked.placeId, day: picked.day, stayMinutes: picked.stayMinutes);
    if (context.mounted) showInfo(context, 'Day ${picked.day}에 추가했어요');
  } catch (e) {
    if (context.mounted) showError(context, e);
  }
}

class _AppBar extends StatelessWidget {
  const _AppBar({required this.title, required this.subtitle, required this.onMore});
  final String title;
  final String subtitle;
  final VoidCallback? onMore;

  @override
  Widget build(BuildContext context) => SizedBox(
        height: 52,
        child: Row(children: [
          IconButton(icon: const Icon(Icons.arrow_back_ios_new, size: 20), onPressed: () => Navigator.of(context).maybePop(), tooltip: '뒤로'),
          Expanded(
            child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
              Text(title, style: AppText.headline, maxLines: 1, overflow: TextOverflow.ellipsis),
              if (subtitle.isNotEmpty) Text(subtitle, style: AppText.caption),
            ]),
          ),
          IconButton(icon: const Icon(Icons.more_horiz), onPressed: onMore, tooltip: '더보기'),
        ]),
      );
}

class _DaySelector extends StatelessWidget {
  const _DaySelector({required this.plan, required this.selected, required this.onSelect, required this.trailing});
  final PlanDetail plan;
  final int selected;
  final ValueChanged<int> onSelect;
  final Widget trailing;

  @override
  Widget build(BuildContext context) {
    final n = plan.numDays;
    Widget tab(int d) {
      final date = plan.startDate.add(Duration(days: d - 1));
      final on = d == selected;
      return Material(
        color: on ? AppColors.primary : AppColors.surface,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () => onSelect(d),
          child: SizedBox(
            height: 48,
            width: n == 1 ? 114 : (n <= 3 ? null : 100),
            child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
              Text('Day $d', style: AppText.label.copyWith(fontWeight: FontWeight.w700, color: on ? Colors.white : AppColors.text2)),
              Text('${md(date)} ${weekdayKo(date)}', style: AppText.caption.copyWith(color: on ? Colors.white : AppColors.text2)),
            ]),
          ),
        ),
      );
    }

    // 당일 여행: Day 탭 오른쪽에 안전 점검 줄 (Figma 25). 여러 날이면 탭 아래 줄.
    if (n == 1) {
      return Padding(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 6),
        child: Row(children: [tab(1), const SizedBox(width: 12), Expanded(child: Align(alignment: Alignment.centerRight, child: trailing))]),
      );
    }
    final tabs = n <= 3
        ? Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 2),
            child: Row(children: [
              for (var d = 1; d <= n; d++) ...[if (d > 1) const SizedBox(width: 8), Expanded(child: tab(d))],
            ]),
          )
        : SizedBox(
            height: 54,
            child: ListView.separated(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 2),
              scrollDirection: Axis.horizontal,
              itemCount: n,
              separatorBuilder: (_, _) => const SizedBox(width: 8),
              itemBuilder: (_, i) => tab(i + 1),
            ),
          );
    return Column(children: [tabs, Padding(padding: const EdgeInsets.fromLTRB(16, 2, 16, 2), child: Align(alignment: Alignment.centerRight, child: trailing))]);
  }
}

/// "🛡 안전 점검 · 예보 기준 HH:MM ›" → 27. 상태: 로딩 / 503 / 먼 여행 / 일부 데이터 없음.
class _SafetyLine extends StatelessWidget {
  const _SafetyLine({required this.controller, required this.plan});
  final PlanController controller;
  final PlanDetail plan;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final s = c.safety;
    final date = plan.startDate.add(Duration(days: c.day - 1));
    String text;
    var icon = Icons.shield_outlined;
    var color = AppColors.text2;
    VoidCallback? onTap = () => showOutlookSheet(context, c, date);
    if (s == null && c.safetyError != null) {
      text = '안전 정보를 불러오지 못했어요';
      icon = Icons.cloud_off_outlined;
      onTap = c.loadSafety;
    } else if (s == null) {
      text = '안전 점검 중…';
    } else {
      final visits = s.items.where((i) => i.isVisit && (i.day == null || i.day == c.day)).toList();
      final far = visits.isNotEmpty && visits.every((i) => i.unassessedReason == 'beyond_forecast');
      if (far) {
        final from = visits.map((i) => i.recheckFrom).whereType<DateTime>().firstOrNull;
        text = from == null ? '날씨·바다 판단은 예보가 나오면 해요' : '날씨·바다 판단은 ${two(from.month)}/${two(from.day)}부터 해요';
      } else {
        text = s.checkedAt == null ? '안전 점검' : '안전 점검 · 예보 기준 ${hhmm(s.checkedAt!)}';
      }
      if (s.partialData) text = '$text · 일부 데이터 없음';
    }
    return InkWell(
      borderRadius: BorderRadius.circular(8),
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 2),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Icon(icon, size: 16, color: color),
          const SizedBox(width: 4),
          Flexible(child: Text(text, style: AppText.caption.copyWith(color: color, fontWeight: FontWeight.w500), overflow: TextOverflow.ellipsis)),
          const Icon(Icons.chevron_right, size: 16, color: AppColors.text3),
        ]),
      ),
    );
  }
}

/// "{장소} {HH:MM} {단계} · {근거}"
String safetyBannerText(SafetyItem s) {
  final h = s.topHazard;
  final reason = h == null ? '' : ' · ${shortReason(h)}${h.source.contains('예보') ? ' 예보' : ''}';
  final place = s.isVisit ? s.title : '${s.title.split(' → ').last} 가는 길';
  return '$place ${s.startAt ?? ''} ${riskWord(s.risk)}$reason'.replaceAll('  ', ' ');
}

/// Context Banner 우선순위 (25): 새 안전 경고(needs_action) > 해제 알림(4초) > 이날 주의·위험 항목 > 검증 위반 > 없음.
/// 해제만 있는 경고(lowered/cleared)는 Green Eased 배너를 4초 보여 주고 사라진다.
class _ContextBanner extends StatefulWidget {
  const _ContextBanner({required this.controller, required this.plan});
  final PlanController controller;
  final PlanDetail plan;

  @override
  State<_ContextBanner> createState() => _ContextBannerState();
}

class _ContextBannerState extends State<_ContextBanner> {
  final _seen = <int>{};
  final _easedHidden = <int>{};

  void _markSeen(SafetyAlert a) {
    if (_seen.add(a.id)) {
      widget.controller.markAlertSeen(a);
      if (a.level == 'safe') {
        Timer(const Duration(seconds: 4), () {
          if (mounted) setState(() => _easedHidden.add(a.id));
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    // 1. 새 안전 경고
    final alert = c.alerts.where((a) => a.status == 'new' && a.needsAction).firstOrNull;
    if (alert != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _markSeen(alert));
      final item = alert.changes.where((x) => x.kind == 'raised').map((x) => x.item).whereType<SafetyItem>().firstOrNull;
      final danger = alert.level == 'danger';
      return _Banner(
        icon: danger ? Icons.warning_amber_rounded : Icons.air,
        fg: danger ? AppColors.danger : AppColors.warning,
        bg: danger ? AppColors.dangerSoft : AppColors.warningSoft,
        text: item == null ? alert.message.split('\n').first : safetyBannerText(item),
        action: 'AI로 조정',
        actionColor: AppColors.ai,
        actionIcon: Icons.auto_awesome,
        onAction: () => c.openAi(prefill: '${item?.title ?? '위험한 곳'} 일정을 안전하게 조정해줘', send: true),
      );
    }
    final eased = c.alerts.where((a) => a.status == 'new' && a.level == 'safe' && !_easedHidden.contains(a.id)).firstOrNull;
    if (eased != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _markSeen(eased));
      final item = eased.changes.map((x) => x.item).whereType<SafetyItem>().firstOrNull;
      return _Banner(
        icon: Icons.verified_user_outlined,
        fg: AppColors.success,
        bg: AppColors.successSoft,
        text: item == null ? '주의가 해제됐어요' : '${item.title} 주의가 해제됐어요',
      );
    }

    // (AI 적용 직후 초록 배너는 2026-10-12 제거 — 변경 이력은 오른쪽 위 ⋯ → 변경 이력)

    // 2. 이날 주의·위험 항목 중 가장 이른 것
    final all = (c.safety?.items ?? const <SafetyItem>[]).where((i) => i.elevated && (i.day == null || i.day == c.day)).toList();
    final risky = [...all.where((i) => i.isVisit), ...all.where((i) => !i.isVisit)]; // 방문 먼저, 그다음 이동
    if (risky.isNotEmpty) {
      final first = risky.first;
      final danger = first.risk == 'danger';
      return _Banner(
        icon: hazardIcon(first.topHazard?.type ?? ''),
        fg: danger ? AppColors.danger : AppColors.warning,
        bg: danger ? AppColors.dangerSoft : AppColors.warningSoft,
        text: safetyBannerText(first),
        action: 'AI로 조정',
        actionColor: AppColors.ai,
        actionIcon: Icons.auto_awesome,
        onAction: () => c.openAi(prefill: '${first.isVisit ? first.title : first.title.split(' → ').last} 일정을 안전하게 조정해줘', send: true),
      );
    }

    // 3. 검증 위반 (기존 04)
    final vs = widget.plan.violations.where((v) => v.day == null || v.day == c.day).toList()..sort((a, b) => (a.isError ? 0 : 1).compareTo(b.isError ? 0 : 1));
    if (vs.isEmpty) return const SizedBox(height: 6);
    final more = vs.length > 1 ? ' 외 ${vs.length - 1}건' : '';
    return _Banner(
      icon: vs.first.isError ? Icons.error_outline : Icons.warning_amber_rounded,
      fg: AppColors.warning,
      bg: AppColors.warningSoft,
      text: '${vs.first.message}$more',
      action: 'AI로 조정',
      actionColor: AppColors.ai,
      actionIcon: Icons.auto_awesome,
      onAction: () => c.openAi(prefill: '일정에 있는 경고를 해결해줘', send: true),
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({required this.icon, required this.fg, required this.bg, required this.text, this.action, this.actionColor = AppColors.ai, this.actionIcon, this.onAction});
  final IconData icon;
  final Color fg;
  final Color bg;
  final String text;
  final String? action;
  final Color actionColor;
  final IconData? actionIcon;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 6, 16, 6),
        child: Container(
          constraints: const BoxConstraints(minHeight: 44),
          padding: EdgeInsets.only(left: 12, right: action == null ? 12 : 0),
          decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(12)),
          child: Row(children: [
            Icon(icon, size: 18, color: fg),
            const SizedBox(width: 8),
            Expanded(
              child: Text(text,
                  style: AppText.meta.copyWith(color: fg == AppColors.success ? AppColors.success : (fg == AppColors.text2 ? AppColors.text : fg), fontWeight: FontWeight.w500),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis),
            ),
            if (action != null)
              TextButton(
                onPressed: onAction,
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  if (actionIcon != null) ...[Icon(actionIcon, size: 14, color: actionColor), const SizedBox(width: 3)],
                  Text(action!, style: AppText.meta.copyWith(color: actionColor, fontWeight: FontWeight.w700)),
                ]),
              ),
          ]),
        ),
      );
}

class _EmptyDay extends StatelessWidget {
  const _EmptyDay({required this.day});
  final int day;

  @override
  Widget build(BuildContext context) {
    final c = context.read<PlanController>();
    return ListView(children: [
      const SizedBox(height: 60),
      const Icon(Icons.calendar_today_outlined, size: 32, color: AppColors.text3),
      const SizedBox(height: 12),
      Center(child: Text('아직 일정이 없어요', style: AppText.section)),
      const SizedBox(height: 6),
      Center(child: Text('장소를 추가하거나 구룡메이트에게 맡겨 보세요', style: AppText.meta)),
      const SizedBox(height: 20),
      Center(
        child: Wrap(spacing: 8, children: [
          AppButton(label: '일정 추가', icon: Icons.add, kind: ButtonKind.soft, height: 40, onPressed: () => openAddPlace(context, c)),
          AppButton(label: 'AI로 만들기', icon: Icons.auto_awesome, kind: ButtonKind.aiSoft, height: 40, onPressed: () => c.openAi(prefill: 'Day $day 일정을 짜줘', send: true)),
        ]),
      ),
    ]);
  }
}

class _Timeline extends StatelessWidget {
  const _Timeline({required this.controller, required this.plan, required this.day});
  final PlanController controller;
  final PlanDetail plan;
  final PlanDay day;

  @override
  Widget build(BuildContext context) {
    final items = day.items;
    final safety = controller.safety;
    return ReorderableListView.builder(
      padding: const EdgeInsets.fromLTRB(0, 4, 0, 120),
      buildDefaultDragHandles: false,
      itemCount: items.length,
      proxyDecorator: (child, _, _) => Material(color: Colors.transparent, child: child),
      onReorderItem: (oldIndex, newIndex) {
        if (newIndex == oldIndex) return;
        controller.reorder(items[oldIndex].id, newIndex + 1).catchError((Object e) {
          if (context.mounted) showError(context, e);
        });
      },
      itemBuilder: (context, i) => _TimelineRow(
        key: ValueKey(items[i].id),
        index: i,
        item: items[i],
        isLast: i == items.length - 1,
        travelMode: plan.travelMode,
        violations: plan.violationsFor(items[i].id),
        update: controller.aiUpdates[items[i].id],
        selected: controller.selectedItemId == items[i].id,
        visitSafety: safety?.visit(items[i].id),
        travelSafety: safety?.travel(items[i].id),
        safetyLoading: safety == null && controller.safetyLoading,
      ),
    );
  }
}

class _TimelineRow extends StatelessWidget {
  const _TimelineRow({
    super.key,
    required this.index,
    required this.item,
    required this.isLast,
    required this.travelMode,
    required this.violations,
    required this.update,
    required this.selected,
    required this.visitSafety,
    required this.travelSafety,
    required this.safetyLoading,
  });

  final int index;
  final PlanItem item;
  final bool isLast;
  final String travelMode;
  final List<Violation> violations;
  final AiUpdate? update;
  final bool selected;
  final SafetyItem? visitSafety;
  final SafetyItem? travelSafety;
  final bool safetyLoading;

  @override
  Widget build(BuildContext context) {
    final risk = visitSafety?.risk;
    final elevated = risk == 'caution' || risk == 'danger';
    final done = item.status == 'done';
    final nodeColor = done
        ? AppColors.success
        : update != null
            ? AppColors.ai
            : elevated
                ? riskColors(risk).$1
                : (violations.isNotEmpty ? AppColors.warning : AppColors.primary);
    final nodeY = (index > 0 ? 32.0 : 0.0) + 24; // 노드 중심 = 카드 제목 줄
    final single = index == 0 && isLast;
    return IntrinsicHeight(
      child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        // 시간 컬럼 (w44, 13 Bold)
        Padding(
          padding: const EdgeInsets.only(left: 16),
          child: SizedBox(
            width: 44,
            child: Padding(
              padding: EdgeInsets.only(top: nodeY - 9),
              child: Text(item.startAt ?? '--:--', style: AppText.meta.copyWith(fontWeight: FontWeight.w700, color: item.skipped || done ? AppColors.text3 : AppColors.text)),
            ),
          ),
        ),
        // 레일 (w28, 2px 라인 + 노드)
        SizedBox(
          width: 28,
          child: Stack(alignment: Alignment.topCenter, children: [
            if (!single)
              Positioned(
                top: index == 0 ? nodeY : 0.0,
                bottom: isLast ? null : 0.0,
                height: isLast ? nodeY : null,
                child: Container(width: 2, color: AppColors.rail),
              ),
            Positioned(
              top: nodeY - 6,
              child: Container(
                width: 12,
                height: 12,
                decoration: BoxDecoration(
                  color: done ? AppColors.success : AppColors.surface,
                  shape: BoxShape.circle,
                  border: Border.all(color: item.skipped ? AppColors.text3 : nodeColor, width: 2),
                ),
              ),
            ),
          ]),
        ),
        Expanded(
          child: Padding(
            padding: const EdgeInsets.only(right: 16),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              if (index > 0) TravelSegment(item: item, defaultMode: travelMode, safety: travelSafety),
              _ItemCard(index: index, item: item, violations: violations, update: update, selected: selected, safety: visitSafety, travelSafety: travelSafety, safetyLoading: safetyLoading),
            ]),
          ),
        ),
      ]),
    );
  }
}

/// Travel Segment (25): "🚶 도보 8분 · 차량 6분" — 계산에 쓴 수단(leg_mode) 먼저, 다른 수단은 회색. estimate 면 "약", 경로 주의면 Amber.
/// 탭 → 수단 바꾸기 시트 (PATCH travel_mode).
class TravelSegment extends StatelessWidget {
  const TravelSegment({super.key, required this.item, required this.defaultMode, required this.safety});
  final PlanItem item;
  final String defaultMode;
  final SafetyItem? safety;

  static String modeWord(String m) => m == 'car' ? '차량' : '도보';

  @override
  Widget build(BuildContext context) {
    final c = context.read<PlanController>();
    final mode = item.legMode ?? defaultMode;
    final other = mode == 'car' ? 'walk' : 'car';
    final otherOpt = item.travelOptions[other];
    final elevated = safety?.elevated ?? false;
    final color = elevated ? riskColors(safety!.risk).$1 : AppColors.text2;
    final skipped = item.skipped || item.travelMinutes == null;
    return SizedBox(
      height: 32,
      child: InkWell(
        onTap: skipped || item.status == 'done' ? null : () => _pickMode(context, c, mode),
        child: Row(children: [
          const SizedBox(width: 4),
          Icon(mode == 'car' ? Icons.directions_car_outlined : Icons.directions_walk, size: 14, color: color),
          const SizedBox(width: 4),
          if (skipped)
            Text('건너뜀', style: AppText.caption)
          else ...[
            Text('${modeWord(mode)} ${item.estimated ? '약 ' : ''}${item.travelMinutes}분', style: AppText.caption.copyWith(color: color, fontWeight: elevated ? FontWeight.w700 : FontWeight.w400)),
            if (otherOpt != null) Text(' · ${modeWord(other)} ${otherOpt.estimated ? '약 ' : ''}${otherOpt.minutes}분', style: AppText.caption.copyWith(color: AppColors.text3)),
            if (elevated) ...[
              const SizedBox(width: 6),
              Flexible(child: RiskChip(risk: safety!.risk, label: riskBadgeLabel(safety!) ?? '', height: 20, onTap: () => showSafetyDetailSheet(context, c, item))),
            ],
          ],
        ]),
      ),
    );
  }

  Future<void> _pickMode(BuildContext context, PlanController c, String current) async {
    final picked = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Padding(padding: const EdgeInsets.fromLTRB(20, 0, 20, 8), child: Text('${item.title}까지 이동 수단', style: AppText.headline)),
          for (final m in ['walk', 'car'])
            ListTile(
              leading: Icon(m == 'car' ? Icons.directions_car_outlined : Icons.directions_walk),
              title: Text('${modeWord(m)} ${item.travelOptions[m] == null ? '' : '${item.travelOptions[m]!.estimated ? '약 ' : ''}${item.travelOptions[m]!.minutes}분'}'),
              trailing: m == current ? const Icon(Icons.check, color: AppColors.primary) : null,
              onTap: () => Navigator.pop(ctx, m),
            ),
          const SizedBox(height: 8),
        ]),
      ),
    );
    if (picked == null || picked == current || !context.mounted) return;
    try {
      await c.setTravelMode(item.id, picked);
    } catch (e) {
      if (context.mounted) showError(context, e);
    }
  }
}

class _ItemCard extends StatelessWidget {
  const _ItemCard({
    required this.index,
    required this.item,
    required this.violations,
    required this.update,
    required this.selected,
    required this.safety,
    required this.travelSafety,
    required this.safetyLoading,
  });
  final int index;
  final PlanItem item;
  final List<Violation> violations;
  final AiUpdate? update;
  final bool selected;
  final SafetyItem? safety;
  final SafetyItem? travelSafety;
  final bool safetyLoading;

  @override
  Widget build(BuildContext context) {
    final c = context.read<PlanController>();
    final risk = safety?.risk;
    final elevated = risk == 'caution' || risk == 'danger';
    final done = item.status == 'done';
    final border = selected || item.status == 'visiting'
        ? const BorderSide(color: AppColors.primary, width: 2)
        : update != null
            ? const BorderSide(color: AppColors.aiLine)
            : elevated
                ? BorderSide(color: riskColors(risk).$3)
                : (violations.isNotEmpty ? const BorderSide(color: AppColors.warningLine) : BorderSide.none);
    final time = item.startAt == null ? '건너뛴 일정' : '${item.startAt} – ${item.endAt} · ${durationKo(item.stayMinutes)}';
    final info = risk == 'safe' ? safety?.firstInfo : null;
    void openDetail() => showSafetyDetailSheet(context, c, item);
    return Opacity(
      opacity: item.skipped ? 0.55 : 1,
      child: AppCard(
        padding: const EdgeInsets.fromLTRB(14, 12, 4, 10),
        border: border,
        onTap: () => c.select(selected ? null : item.id),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (update != null) _AiBadge(update: update!),
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(
              child: Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(item.title, style: AppText.section.copyWith(color: done ? AppColors.text3 : AppColors.text), maxLines: 2, overflow: TextOverflow.ellipsis),
              ),
            ),
            SizedBox(
              width: 36,
              height: 32,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: const Icon(Icons.more_vert, size: 20, color: AppColors.text2),
                tooltip: '일정 편집',
                onPressed: () => showItemActions(context, c, item),
              ),
            ),
          ]),
          const SizedBox(height: 2),
          Text(time, style: AppText.meta),
          if (item.memo.isNotEmpty) Text(item.memo, style: AppText.meta.copyWith(color: AppColors.text3), maxLines: 2, overflow: TextOverflow.ellipsis),
          const SizedBox(height: 8),
          Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
            Expanded(
              child: Wrap(spacing: 6, runSpacing: 6, children: [
                if (done) InfoChip.success('다녀옴${item.arrivedAt == null ? '' : ' ${item.arrivedAt}–${item.doneAt ?? ''}'}', icon: Icons.check),
                if (item.status == 'visiting') InfoChip(label: '지금 여기', icon: Icons.my_location, fg: AppColors.primary, bg: AppColors.primarySoft),
                if (safety != null && !done) RiskBadge(item: safety!, onTap: openDetail) else if (safetyLoading && !item.skipped) const SkeletonChip(),
                if (info != null) InfoHazardChip(hazard: info, onTap: openDetail),
                if (item.startTime != null) InfoChip(label: '${item.startTime} 고정', icon: Icons.push_pin_outlined),
                if (item.skipped) const InfoChip(label: '건너뜀', icon: Icons.redo),
                for (final v in violations) Tooltip(message: v.message, child: v.isError ? InfoChip.error(v.shortLabel) : InfoChip.warning(v.shortLabel)),
                if (selected)
                  ActionChip(
                    visualDensity: VisualDensity.compact,
                    avatar: const Icon(Icons.map_outlined, size: 14, color: AppColors.primary),
                    label: Text('지도에서 보기', style: AppText.caption.copyWith(color: AppColors.primary, fontWeight: FontWeight.w700)),
                    backgroundColor: AppColors.primarySoft,
                    side: BorderSide.none,
                    onPressed: () => c.showOnMap(item.id),
                  ),
              ]),
            ),
            ReorderableDragStartListener(
              index: index,
              child: const Padding(
                padding: EdgeInsets.fromLTRB(8, 4, 10, 0),
                child: Icon(Icons.drag_indicator, size: 18, color: AppColors.text3),
              ),
            ),
          ]),
        ]),
      ),
    );
  }
}

class _AiBadge extends StatelessWidget {
  const _AiBadge({required this.update});
  final AiUpdate update;

  @override
  Widget build(BuildContext context) {
    final label = update.kind == 'added'
        ? 'AI added${update.toTime == null ? '' : ' ${update.toTime}'}'
        : 'AI updated ${update.fromTime ?? '-'} → ${update.toTime ?? '-'}';
    return Padding(
      padding: const EdgeInsets.only(right: 10, bottom: 6),
      child: Material(
        color: AppColors.aiSoft,
        borderRadius: BorderRadius.circular(8),
        child: InkWell(
          borderRadius: BorderRadius.circular(8),
          onTap: () => _showReason(context),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            child: Row(children: [
              const Icon(Icons.auto_awesome, size: 12, color: AppColors.ai),
              const SizedBox(width: 4),
              Expanded(child: Text(label, style: AppText.caption.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700), overflow: TextOverflow.ellipsis)),
              Text('변경 이유 ›', style: AppText.caption.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700)),
            ]),
          ),
        ),
      ),
    );
  }

  void _showReason(BuildContext context) {
    final c = context.read<PlanController>();
    final summary = c.lastApplied?.proposal.summary.trim() ?? '';
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Row(children: [Icon(Icons.auto_awesome, color: AppColors.ai, size: 20), SizedBox(width: 6), Text('무엇이 왜 바뀌었나요')]),
        content: SingleChildScrollView(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            Text(update.reason, style: AppText.label),
            if (summary.isNotEmpty) ...[const SizedBox(height: 12), Text(summary, style: AppText.meta)],
          ]),
        ),
        actions: [TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('확인'))],
      ),
    );
  }
}

class _AddButton extends StatelessWidget {
  const _AddButton({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Container(
        decoration: BoxDecoration(borderRadius: BorderRadius.circular(24), boxShadow: AppShadow.floating),
        child: Material(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(24),
          child: InkWell(
            borderRadius: BorderRadius.circular(24),
            onTap: onTap,
            child: SizedBox(
              height: 48,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 18),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  const Icon(Icons.add, color: AppColors.primary, size: 20),
                  const SizedBox(width: 6),
                  Text('일정 추가', style: AppText.section.copyWith(fontSize: 15, color: AppColors.primary)),
                ]),
              ),
            ),
          ),
        ),
      );
}
