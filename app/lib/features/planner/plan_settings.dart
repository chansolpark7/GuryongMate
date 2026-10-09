import 'package:flutter/material.dart';

import '../../core/format.dart';
import '../../core/models/plan.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../guide/guide_prep_screen.dart';
import '../safety/outlook_sheet.dart';
import 'plan_controller.dart';

/// App Bar ⋯ 메뉴.
Future<void> showPlanMenu(BuildContext context, PlanController c) async {
  final action = await showModalBottomSheet<String>(
    context: context,
    isScrollControlled: true, // 동행 메뉴(여행 시작/종료)까지 들어가 기본 높이(9/16)를 넘는다
    showDragHandle: true,
    builder: (ctx) => SafeArea(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        if (c.tripActive && c.onEndTrip != null)
          ListTile(leading: const Icon(Icons.flag_outlined), title: const Text('여행 종료'), subtitle: Text('오늘 동행을 마치고 하루 요약을 봐요', style: AppText.caption), onTap: () => Navigator.pop(ctx, 'trip_end'))
        else if (c.onStartTrip != null)
          ListTile(
            leading: const Icon(Icons.near_me_outlined, color: AppColors.primary),
            title: const Text('여행 시작'),
            subtitle: Text('도착 해설 · 늦어질 때 대안 · 위험 구역 알림', style: AppText.caption),
            onTap: () => Navigator.pop(ctx, 'trip_start'),
          ),
        ListTile(leading: const Icon(Icons.edit_outlined), title: const Text('여행 정보 수정'), onTap: () => Navigator.pop(ctx, 'edit')),
        ListTile(
          leading: const Icon(Icons.rule),
          title: const Text('여행 조건'),
          subtitle: Text('${c.plan?.constraints.length ?? 0}개 · 종료 시각, 이동 시간 상한, 꼭 갈 곳', style: AppText.caption),
          onTap: () => Navigator.pop(ctx, 'rules'),
        ),
        ListTile(leading: const Icon(Icons.history), title: const Text('AI 변경 이력'), onTap: () => Navigator.pop(ctx, 'history')),
        ListTile(
          leading: const Icon(Icons.auto_stories_outlined, color: AppColors.ai),
          title: const Text('현장 가이드 준비'),
          subtitle: Text('장소마다 맞춤 해설을 미리 만들어 둬요', style: AppText.caption),
          onTap: () => Navigator.pop(ctx, 'guide'),
        ),
        ListTile(leading: const Icon(Icons.waves), title: const Text('날씨·바다 전망'), onTap: () => Navigator.pop(ctx, 'outlook')),
        ListTile(
          leading: const Icon(Icons.delete_outline, color: AppColors.danger),
          title: const Text('여행 삭제', style: TextStyle(color: AppColors.danger)),
          onTap: () => Navigator.pop(ctx, 'delete'),
        ),
      ]),
    ),
  );
  if (!context.mounted) return;
  switch (action) {
    case 'trip_start':
      await c.onStartTrip?.call();
    case 'trip_end':
      await c.onEndTrip?.call();
    case 'edit':
      await _editPlan(context, c);
    case 'rules':
      await showRulesSheet(context, c);
    case 'history':
      await showProposalHistory(context, c);
    case 'guide':
      await Navigator.of(context).push(MaterialPageRoute(builder: (_) => GuidePrepScreen(plan: c)));
    case 'outlook':
      final p = c.plan;
      if (p != null) await showOutlookSheet(context, c, p.startDate.add(Duration(days: c.day - 1)));
    case 'delete':
      final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('여행을 삭제할까요?'),
          content: const Text('일정과 AI 대화가 모두 사라져요.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('취소')),
            TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('삭제', style: TextStyle(color: AppColors.danger))),
          ],
        ),
      );
      if (ok != true || !context.mounted) return;
      try {
        await c.api.deletePlan(c.planId);
        if (context.mounted) Navigator.of(context).pop();
      } catch (e) {
        if (context.mounted) showError(context, e);
      }
  }
}

Future<void> _editPlan(BuildContext context, PlanController c) async {
  final p = c.plan;
  if (p == null) return;
  // 컨트롤러를 쓰지 않는다: 시트가 닫히는 동안에도 TextField 가 살아 있어 직접 dispose 하면 오류가 난다.
  var newTitle = p.title;
  var range = DateTimeRange(start: p.startDate, end: p.endDate);
  var start = p.dayStart, end = p.dayEnd, mode = p.travelMode;

  Future<String?> pickTime(BuildContext ctx, String cur) async {
    final m = toMinutes(cur) ?? 600;
    final t = await showTimePicker(context: ctx, initialTime: TimeOfDay(hour: m ~/ 60, minute: m % 60));
    return t == null ? null : '${two(t.hour)}:${two(t.minute)}';
  }

  final save = await showModalBottomSheet<bool>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, set) => Padding(
        padding: EdgeInsets.fromLTRB(20, 0, 20, MediaQuery.of(ctx).viewInsets.bottom + 20),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text('여행 정보 수정', style: AppText.headline),
          const SizedBox(height: 16),
          TextFormField(initialValue: p.title, onChanged: (v) => newTitle = v, decoration: const InputDecoration(labelText: 'Plan 이름')),
          const SizedBox(height: 10),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.calendar_today_outlined),
            title: Text('${dateRange(range.start, range.end)} · ${nightsDays(range.start, range.end)}'),
            onTap: () async {
              final r = await showDateRangePicker(
                context: ctx,
                firstDate: DateTime(2020),
                lastDate: DateTime(2035),
                initialDateRange: range,
              );
              if (r != null) set(() => range = r);
            },
          ),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.schedule),
            title: Text('하루 $start ~ $end'),
            trailing: Wrap(children: [
              TextButton(onPressed: () async => (await pickTime(ctx, start))?.let((v) => set(() => start = v)), child: const Text('시작')),
              TextButton(onPressed: () async => (await pickTime(ctx, end))?.let((v) => set(() => end = v)), child: const Text('종료')),
            ]),
          ),
          SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'walk', label: Text('도보'), icon: Icon(Icons.directions_walk)),
              ButtonSegment(value: 'car', label: Text('차량'), icon: Icon(Icons.directions_car_outlined)),
            ],
            selected: {mode},
            onSelectionChanged: (s) => set(() => mode = s.first),
          ),
          const SizedBox(height: 20),
          AppButton(label: '저장', onPressed: () => Navigator.pop(ctx, true)),
        ]),
      ),
    ),
  );
  newTitle = newTitle.trim();
  if (save != true || !context.mounted) return;
  try {
    await c.updatePlan({
      if (newTitle.isNotEmpty && newTitle != p.title) 'title': newTitle,
      'start_date': isoDate(range.start),
      'end_date': isoDate(range.end),
      'day_start': start,
      'day_end': end,
      'travel_mode': mode,
    });
  } catch (e) {
    if (context.mounted) showError(context, e);
  }
}

extension _Let<T> on T {
  R let<R>(R Function(T) f) => f(this);
}

String ruleLabel(Constraint r, PlanDetail plan) {
  String placeName(String? id) =>
      plan.allItems.where((i) => i.placeId == id).map((i) => i.title).firstOrNull ?? id ?? '';
  return switch (r.type) {
    'must_include' => '꼭 포함: ${placeName(r.params['place_id'] as String?)}',
    'exclude' => '제외: ${placeName(r.params['place_id'] as String?)}',
    'end_before' => '${r.params['day'] == null ? '매일' : 'Day ${r.params['day']}'} ${r.params['time']} 전에 끝내기',
    'max_travel_minutes' => '장소 간 이동 ${r.params['minutes']}분 이하',
    _ => r.type,
  };
}

/// 사용자 조건(규칙). planner 가 결정론적으로 검사해 위반을 표시한다.
Future<void> showRulesSheet(BuildContext context, PlanController c) => showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (ctx) => ListenableBuilder(
        listenable: c,
        builder: (ctx, _) {
          final plan = c.plan;
          if (plan == null) return const SizedBox.shrink();
          return SafeArea(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
              child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                Text('여행 조건', style: AppText.headline),
                const SizedBox(height: 4),
                Text('어기면 일정에 경고가 표시되고, AI도 이 조건을 지켜서 일정을 바꿔요.', style: AppText.caption),
                const SizedBox(height: 12),
                if (plan.constraints.isEmpty) Padding(padding: const EdgeInsets.symmetric(vertical: 12), child: Text('아직 조건이 없어요', style: AppText.meta)),
                for (final r in plan.constraints)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(r.source == 'llm' ? Icons.auto_awesome : Icons.rule, color: r.source == 'llm' ? AppColors.ai : AppColors.text2),
                    title: Text(ruleLabel(r, plan), style: AppText.label),
                    subtitle: Text([r.hard ? '반드시' : '가능하면', if (r.note.isNotEmpty) r.note].join(' · '), style: AppText.caption),
                    trailing: IconButton(
                      icon: const Icon(Icons.close),
                      tooltip: '삭제',
                      onPressed: () => c.removeConstraint(r.id).catchError((Object e) {
                        if (ctx.mounted) showError(ctx, e);
                      }),
                    ),
                  ),
                const SizedBox(height: 8),
                Wrap(spacing: 8, runSpacing: 8, children: [
                  ActionChip(
                    avatar: const Icon(Icons.flag_outlined, size: 16),
                    label: const Text('종료 시각'),
                    onPressed: () async {
                      final t = await showTimePicker(context: ctx, initialTime: const TimeOfDay(hour: 18, minute: 0), helpText: '매일 이 시각 전에 끝내기');
                      if (t == null) return;
                      await c.addConstraint({'type': 'end_before', 'params': {'time': '${two(t.hour)}:${two(t.minute)}'}}).catchError((Object e) {
                        if (ctx.mounted) showError(ctx, e);
                      });
                    },
                  ),
                  ActionChip(
                    avatar: const Icon(Icons.directions_walk, size: 16),
                    label: const Text('이동 시간 상한'),
                    onPressed: () async {
                      final m = await showDialog<int>(
                        context: ctx,
                        builder: (dctx) => SimpleDialog(
                          title: const Text('장소 간 이동 시간 상한'),
                          children: [
                            for (final v in [10, 15, 20, 30])
                              SimpleDialogOption(onPressed: () => Navigator.pop(dctx, v), child: Text('$v분 이하')),
                          ],
                        ),
                      );
                      if (m == null) return;
                      await c.addConstraint({'type': 'max_travel_minutes', 'params': {'minutes': m}}).catchError((Object e) {
                        if (ctx.mounted) showError(ctx, e);
                      });
                    },
                  ),
                ]),
                const SizedBox(height: 8),
                Text('꼭 갈 곳은 일정 카드 ⋮ → "꼭 유지하기"로 정해요.', style: AppText.caption),
              ]),
            ),
          );
        },
      ),
    );

/// 승인된 AI 제안 목록 (배너 "이력").
Future<void> showProposalHistory(BuildContext context, PlanController c) => showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (ctx) => FractionallySizedBox(
        heightFactor: 0.7,
        child: FutureBuilder<List<ProposalDetail>>(
          future: c.api.proposals(c.planId),
          builder: (ctx, snap) {
            if (snap.hasError) return StateView(title: '이력을 불러오지 못했어요', message: '${snap.error}');
            if (!snap.hasData) return const Center(child: CircularProgressIndicator());
            final list = snap.data!..sort((a, b) => b.createdAt.compareTo(a.createdAt));
            return ListView(padding: const EdgeInsets.fromLTRB(20, 0, 20, 24), children: [
              Text('AI 변경 이력', style: AppText.headline),
              const SizedBox(height: 12),
              if (list.isEmpty) Text('아직 AI 제안이 없어요', style: AppText.meta),
              for (final p in list)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(
                    switch (p.status) { 'accepted' => Icons.check_circle, 'rejected' => Icons.cancel_outlined, 'pending' => Icons.hourglass_top, _ => Icons.swap_horiz },
                    color: switch (p.status) { 'accepted' => AppColors.success, 'pending' => AppColors.ai, _ => AppColors.text3 },
                  ),
                  title: Text(p.summary.trim().isEmpty ? '변경 ${p.ops.length}건' : p.summary.trim(), maxLines: 2, overflow: TextOverflow.ellipsis, style: AppText.label),
                  subtitle: Text(
                    '${switch (p.status) { 'accepted' => '적용됨', 'rejected' => '거절됨', 'pending' => '대기 중', _ => '대체됨' }} · '
                    '변경 ${p.ops.length}건 · ${md(p.createdAt)} ${two(p.createdAt.hour)}:${two(p.createdAt.minute)}',
                    style: AppText.caption,
                  ),
                ),
            ]);
          },
        ),
      ),
    );
