import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/format.dart';
import '../../core/models/plan.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import 'item_actions.dart';
import 'place_search_sheet.dart';
import 'plan_controller.dart';
import 'plan_settings.dart';

/// 04_Planner / 08_Planner_After_Apply
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
            _AppBar(title: '', subtitle: '', onMore: null),
            Expanded(
              child: c.loadError != null
                  ? StateView(title: '일정을 불러오지 못했어요', message: '${c.loadError}', actionLabel: '다시 시도', onAction: c.load)
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
            subtitle: '${dateRange(plan.startDate, plan.endDate)} · ${nightsDays(plan.startDate, plan.endDate)}',
            onMore: () => showPlanMenu(context, c),
          ),
          _DaySelector(plan: plan, selected: c.day, onSelect: c.setDay),
          if (c.busy) const LinearProgressIndicator(minHeight: 2) else const SizedBox(height: 2),
          _ContextBanner(controller: c, plan: plan),
          Expanded(
            child: RefreshIndicator(
              onRefresh: () => c.load(silent: true).catchError((Object e) {
                if (context.mounted) showError(context, e);
              }),
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
  const PlannerFabs({super.key});

  @override
  Widget build(BuildContext context) {
    final c = context.read<PlanController>();
    return Row(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.center, children: [
      _AddButton(onTap: () => openAddPlace(context, c)),
      const SizedBox(width: 10),
      AiFab(onTap: () => c.openAi()),
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
  const _DaySelector({required this.plan, required this.selected, required this.onSelect});
  final PlanDetail plan;
  final int selected;
  final ValueChanged<int> onSelect;

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
            width: n <= 3 ? null : 100,
            child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
              Text('Day $d', style: AppText.label.copyWith(fontWeight: FontWeight.w700, color: on ? Colors.white : AppColors.text2)),
              Text('${md(date)} ${weekdayKo(date)}', style: AppText.caption.copyWith(color: on ? Colors.white : AppColors.text2)),
            ]),
          ),
        ),
      );
    }

    if (n <= 3) {
      return Padding(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 6),
        child: Row(children: [
          for (var d = 1; d <= n; d++) ...[if (d > 1) const SizedBox(width: 8), Expanded(child: tab(d))],
        ]),
      );
    }
    return SizedBox(
      height: 58,
      child: ListView.separated(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 6),
        scrollDirection: Axis.horizontal,
        itemCount: n,
        separatorBuilder: (_, _) => const SizedBox(width: 8),
        itemBuilder: (_, i) => tab(i + 1),
      ),
    );
  }
}

/// 날씨 경고 자리에 planner 검증 결과(위반)를 보여 준다. AI 적용 직후에는 성공 배너.
class _ContextBanner extends StatelessWidget {
  const _ContextBanner({required this.controller, required this.plan});
  final PlanController controller;
  final PlanDetail plan;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final applied = c.lastApplied;
    if (applied != null && c.aiUpdates.isNotEmpty) {
      final text = applied.proposal.summary.trim().isEmpty ? 'AI 변경안이 일정에 반영됐어요' : applied.proposal.summary.trim().split('\n').first;
      return _Banner(
        icon: Icons.check,
        fg: AppColors.success,
        bg: AppColors.successSoft,
        text: text,
        action: '이력',
        actionColor: AppColors.success,
        actionIcon: null,
        onAction: () => showProposalHistory(context, c),
      );
    }
    final vs = plan.violations.where((v) => v.day == null || v.day == c.day).toList()
      ..sort((a, b) => (a.isError ? 0 : 1).compareTo(b.isError ? 0 : 1));
    if (vs.isEmpty) return const SizedBox(height: 6);
    final more = vs.length > 1 ? ' 외 ${vs.length - 1}건' : '';
    return _Banner(
      icon: vs.first.isError ? Icons.error_outline : Icons.warning_amber_rounded,
      fg: vs.first.isError ? AppColors.danger : AppColors.warning,
      bg: vs.first.isError ? AppColors.dangerSoft : AppColors.warningSoft,
      text: '${vs.first.message}$more',
      action: 'AI로 조정',
      actionColor: AppColors.ai,
      actionIcon: Icons.auto_awesome,
      onAction: () => c.openAi(prefill: 'Day ${c.day} 일정의 문제를 고쳐줘: ${vs.map((v) => v.message).join(' / ')}'),
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({required this.icon, required this.fg, required this.bg, required this.text, required this.action, required this.actionColor, required this.actionIcon, required this.onAction});
  final IconData icon;
  final Color fg;
  final Color bg;
  final String text;
  final String action;
  final Color actionColor;
  final IconData? actionIcon;
  final VoidCallback onAction;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 6, 16, 6),
        child: Container(
          constraints: const BoxConstraints(minHeight: 44),
          padding: const EdgeInsets.only(left: 12),
          decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(12)),
          child: Row(children: [
            Icon(icon, size: 18, color: fg),
            const SizedBox(width: 8),
            Expanded(child: Text(text, style: AppText.meta.copyWith(color: fg == AppColors.success ? AppColors.success : AppColors.text), maxLines: 2, overflow: TextOverflow.ellipsis)),
            TextButton(
              onPressed: onAction,
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                if (actionIcon != null) ...[Icon(actionIcon, size: 14, color: actionColor), const SizedBox(width: 3)],
                Text(action, style: AppText.meta.copyWith(color: actionColor, fontWeight: FontWeight.w700)),
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
      const Icon(Icons.event_note_outlined, size: 44, color: AppColors.text3),
      const SizedBox(height: 12),
      Center(child: Text('Day $day 일정이 비어 있어요', style: AppText.section)),
      const SizedBox(height: 6),
      Center(child: Text('장소를 직접 추가하거나 AI에게 맡겨 보세요', style: AppText.meta)),
      const SizedBox(height: 20),
      Center(
        child: Wrap(spacing: 8, children: [
          AppButton(label: '일정 추가', icon: Icons.add, kind: ButtonKind.soft, height: 44, onPressed: () => openAddPlace(context, c)),
          AppButton(
            label: 'AI로 채우기',
            icon: Icons.auto_awesome,
            kind: ButtonKind.secondary,
            height: 44,
            onPressed: () => c.openAi(prefill: 'Day $day 일정을 짜줘', send: true),
          ),
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
  });

  final int index;
  final PlanItem item;
  final bool isLast;
  final String travelMode;
  final List<Violation> violations;
  final AiUpdate? update;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final warn = violations.isNotEmpty;
    final nodeColor = update != null ? AppColors.ai : (warn ? AppColors.warning : AppColors.primary);
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
              child: Text(item.startAt ?? '--:--', style: AppText.meta.copyWith(fontWeight: FontWeight.w700, color: item.skipped ? AppColors.text3 : AppColors.text)),
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
                decoration: BoxDecoration(color: AppColors.surface, shape: BoxShape.circle, border: Border.all(color: item.skipped ? AppColors.text3 : nodeColor, width: 2)),
              ),
            ),
          ]),
        ),
        Expanded(
          child: Padding(
            padding: const EdgeInsets.only(right: 16),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              if (index > 0) _TravelSegment(minutes: item.travelMinutes, mode: travelMode, skipped: item.skipped),
              _ItemCard(index: index, item: item, violations: violations, update: update, selected: selected),
            ]),
          ),
        ),
      ]),
    );
  }
}

class _TravelSegment extends StatelessWidget {
  const _TravelSegment({required this.minutes, required this.mode, required this.skipped});
  final int? minutes;
  final String mode;
  final bool skipped;

  @override
  Widget build(BuildContext context) => SizedBox(
        height: 32,
        child: Row(children: [
          const SizedBox(width: 4),
          Icon(mode == 'car' ? Icons.directions_car_outlined : Icons.directions_walk, size: 14, color: AppColors.text3),
          const SizedBox(width: 4),
          Text(
            skipped || minutes == null ? '건너뜀' : '${mode == 'car' ? '차량' : '도보'} $minutes분',
            style: AppText.caption,
          ),
        ]),
      );
}

class _ItemCard extends StatelessWidget {
  const _ItemCard({required this.index, required this.item, required this.violations, required this.update, required this.selected});
  final int index;
  final PlanItem item;
  final List<Violation> violations;
  final AiUpdate? update;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final c = context.read<PlanController>();
    final warn = violations.isNotEmpty;
    final border = selected
        ? const BorderSide(color: AppColors.primary, width: 2)
        : update != null
            ? const BorderSide(color: AppColors.aiLine)
            : warn
                ? const BorderSide(color: AppColors.warningLine)
                : BorderSide.none;
    final time = item.startAt == null ? '건너뛴 일정' : '${item.startAt} – ${item.endAt} · ${durationKo(item.stayMinutes)}';
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
                child: Text(item.title, style: AppText.section, maxLines: 2, overflow: TextOverflow.ellipsis),
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
                if (item.startTime != null) InfoChip(label: '${item.startTime} 고정', icon: Icons.push_pin_outlined),
                if (item.status == 'done') InfoChip.success('방문 완료', icon: Icons.check),
                if (item.status == 'visiting') InfoChip.success('방문 중', icon: Icons.directions_walk),
                if (item.skipped) const InfoChip(label: '건너뜀', icon: Icons.redo),
                for (final v in violations)
                  Tooltip(message: v.message, child: v.isError ? InfoChip.error(v.shortLabel) : InfoChip.warning(v.shortLabel)),
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
