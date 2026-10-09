import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/app_session.dart';
import '../../core/format.dart';
import '../../core/models/plan.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../planner/planner_shell.dart';
import 'create_plan_screen.dart';

class _PlanRow {
  _PlanRow(this.plan, this.pendingOps);
  final PlanSummary plan;
  final int pendingOps; // 승인 대기 중인 AI 제안의 변경 수
}

/// 02_Plan_List
class PlanListScreen extends StatefulWidget {
  const PlanListScreen({super.key});

  @override
  State<PlanListScreen> createState() => _PlanListScreenState();
}

class _PlanListScreenState extends State<PlanListScreen> {
  List<_PlanRow>? _rows;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final api = context.read<AppSession>().api;
    try {
      final plans = await api.plans();
      final pending = await Future.wait(plans.map((p) async {
        try {
          final list = await api.proposals(p.id, status: 'pending');
          return list.fold<int>(0, (s, x) => s + x.ops.length);
        } catch (_) {
          return 0;
        }
      }));
      if (!mounted) return;
      setState(() {
        _rows = [for (var i = 0; i < plans.length; i++) _PlanRow(plans[i], pending[i])];
        _error = null;
      });
    } catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  Future<void> _open(int planId, {String? aiPrompt}) async {
    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => PlannerShell(planId: planId, initialAiPrompt: aiPrompt)));
    _load();
  }

  Future<void> _create({bool withAi = false}) async {
    final result = await Navigator.of(context).push<CreatePlanResult>(
      MaterialPageRoute(fullscreenDialog: true, builder: (_) => CreatePlanScreen(startWithAi: withAi)),
    );
    if (result == null || !mounted) return;
    _open(result.plan.id, aiPrompt: result.aiPrompt);
  }

  Future<void> _actions(PlanSummary p) async {
    final api = context.read<AppSession>().api;
    final action = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Padding(padding: const EdgeInsets.only(bottom: 8), child: Text(p.title, style: AppText.headline)),
          ListTile(leading: const Icon(Icons.copy_outlined), title: const Text('복제'), onTap: () => Navigator.pop(ctx, 'copy')),
          ListTile(
            leading: const Icon(Icons.delete_outline, color: AppColors.danger),
            title: const Text('삭제', style: TextStyle(color: AppColors.danger)),
            onTap: () => Navigator.pop(ctx, 'delete'),
          ),
        ]),
      ),
    );
    if (!mounted || action == null) return;
    try {
      if (action == 'delete') {
        final ok = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: const Text('여행을 삭제할까요?'),
            content: Text("'${p.title}'의 일정과 AI 대화가 모두 사라져요."),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('취소')),
              TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('삭제', style: TextStyle(color: AppColors.danger))),
            ],
          ),
        );
        if (ok != true) return;
        await api.deletePlan(p.id);
      } else {
        // 복제: 같은 정보로 계획을 만들고 일정·규칙을 ops 한 번으로 옮긴다
        final src = await api.plan(p.id);
        final copy = await api.createPlan({
          'title': '${src.title} (복사본)',
          'start_date': isoDate(src.startDate),
          'end_date': isoDate(src.endDate),
          'day_start': src.dayStart,
          'day_end': src.dayEnd,
          'travel_mode': src.travelMode,
          'memo': src.memo,
        });
        final ops = [
          for (final i in src.allItems)
            {
              'op': 'add_item',
              'place_id': i.placeId,
              'day': i.day,
              'stay_minutes': i.stayMinutes,
              'memo': i.memo,
              'start_time': ?i.startTime,
            },
          for (final c in src.constraints)
            {'op': 'add_constraint', 'type': c.type, 'params': c.params, 'hard': c.hard, 'source': c.source, 'note': c.note},
        ];
        if (ops.isNotEmpty) await api.applyOps(copy.id, ops);
      }
      _load();
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  void _account() {
    final session = context.read<AppSession>();
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(leading: const Icon(Icons.person_outline), title: Text(session.userId ?? ''), subtitle: const Text('로그인 계정')),
          ListTile(leading: const Icon(Icons.dns_outlined), title: Text(session.baseUrl), subtitle: const Text('서버 주소')),
          ListTile(
            leading: const Icon(Icons.logout),
            title: const Text('로그아웃'),
            onTap: () {
              Navigator.pop(ctx);
              session.signOut();
            },
          ),
        ]),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    final today = dateOnly(DateTime.now());
    final upcoming = (rows ?? []).where((r) => !r.plan.endDate.isBefore(today)).toList()
      ..sort((a, b) => a.plan.startDate.compareTo(b.plan.startDate));
    final past = (rows ?? []).where((r) => r.plan.endDate.isBefore(today)).toList()
      ..sort((a, b) => b.plan.startDate.compareTo(a.plan.startDate));

    return Scaffold(
      body: SafeArea(
        child: RefreshIndicator(
          onRefresh: _load,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
            children: [
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('My Plans', style: AppText.title),
                    const SizedBox(height: 2),
                    Text('여행 계획 ${rows?.length ?? 0}개', style: AppText.meta.copyWith(color: AppColors.text3)),
                  ]),
                ),
                _CircleButton(icon: Icons.person_outline, onTap: _account, tooltip: '계정', bg: AppColors.surface, fg: AppColors.text2),
                const SizedBox(width: 8),
                _CircleButton(icon: Icons.add, onTap: _create, tooltip: '새 여행 만들기'),
              ]),
              const SizedBox(height: 24),
              if (rows == null && _error == null)
                const Padding(padding: EdgeInsets.all(48), child: Center(child: CircularProgressIndicator()))
              else if (rows == null)
                StateView(title: '여행 목록을 불러오지 못했어요', message: '$_error', actionLabel: '다시 시도', onAction: _load)
              else ...[
                if (upcoming.isNotEmpty) ...[
                  _SectionTitle('다가오는 여행'),
                  for (final (i, r) in upcoming.indexed)
                    _PlanCard(row: r, index: i, onTap: () => _open(r.plan.id), onLongPress: () => _actions(r.plan)),
                  const SizedBox(height: 12),
                ],
                if (past.isNotEmpty) ...[
                  _SectionTitle('지난 여행'),
                  for (final r in past) _PlanCard(row: r, past: true, onTap: () => _open(r.plan.id), onLongPress: () => _actions(r.plan)),
                  const SizedBox(height: 12),
                ],
                if (rows.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 24),
                    child: StateView(icon: Icons.luggage_outlined, title: '아직 여행 계획이 없어요', message: '+ 버튼이나 아래 카드로 첫 여행을 만들어 보세요.'),
                  ),
                const SizedBox(height: 12),
                _AiPromo(onTap: () => _create(withAi: true)),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _CircleButton extends StatelessWidget {
  const _CircleButton({required this.icon, required this.onTap, required this.tooltip, this.bg = AppColors.primarySoft, this.fg = AppColors.primary});
  final IconData icon;
  final VoidCallback onTap;
  final String tooltip;
  final Color bg;
  final Color fg;

  @override
  Widget build(BuildContext context) => Tooltip(
        message: tooltip,
        child: Material(
          color: bg,
          shape: const CircleBorder(),
          child: InkWell(customBorder: const CircleBorder(), onTap: onTap, child: SizedBox(width: 40, height: 40, child: Icon(icon, color: fg))),
        ),
      );
}

class _SectionTitle extends StatelessWidget {
  const _SectionTitle(this.text);
  final String text;

  @override
  Widget build(BuildContext context) =>
      Padding(padding: const EdgeInsets.only(bottom: 10), child: Text(text, style: AppText.label.copyWith(color: AppColors.text3, fontWeight: FontWeight.w700)));
}

class _PlanCard extends StatelessWidget {
  const _PlanCard({required this.row, this.index = 0, this.past = false, required this.onTap, required this.onLongPress});
  final _PlanRow row;
  final int index;
  final bool past;
  final VoidCallback onTap;
  final VoidCallback onLongPress;

  static const _thumbs = [
    (Icons.location_on_outlined, AppColors.primary, Color(0xFFDCE8FB)),
    (Icons.map_outlined, AppColors.success, Color(0xFFDDF3E6)),
    (Icons.calendar_month_outlined, Color(0xFFE8833A), Color(0xFFFDEEE3)),
  ];

  String? _dday(PlanSummary p) {
    final today = dateOnly(DateTime.now());
    if (past) return null;
    if (!today.isBefore(p.startDate)) return '여행 중';
    return 'D-${p.startDate.difference(today).inDays}';
  }

  @override
  Widget build(BuildContext context) {
    final p = row.plan;
    final (icon, fg, bg) = past ? (Icons.image_outlined, AppColors.text3, AppColors.field) : _thumbs[index % _thumbs.length];
    final dday = _dday(p);
    final titleColor = past ? AppColors.text3 : AppColors.text;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: AppCard(
        padding: const EdgeInsets.all(12),
        onTap: onTap,
        onLongPress: onLongPress,
        child: Column(children: [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Container(width: 72, height: 72, decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(12)), child: Icon(icon, color: fg, size: 28)),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const SizedBox(height: 2),
                Row(children: [
                  Expanded(child: Text(p.title, style: AppText.section.copyWith(color: titleColor), maxLines: 1, overflow: TextOverflow.ellipsis)),
                  if (dday != null)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                      decoration: BoxDecoration(color: AppColors.primarySoft, borderRadius: BorderRadius.circular(8)),
                      child: Text(dday, style: AppText.caption.copyWith(color: AppColors.primary, fontWeight: FontWeight.w700)),
                    ),
                ]),
                const SizedBox(height: 2),
                Text(dateRange(p.startDate, p.endDate), style: AppText.meta.copyWith(color: past ? AppColors.text3 : AppColors.text2)),
                const SizedBox(height: 10),
                Row(children: [
                  const Icon(Icons.place_outlined, size: 14, color: AppColors.text3),
                  const SizedBox(width: 3),
                  Text('${p.itemCount} places', style: AppText.caption.copyWith(color: past ? AppColors.text3 : AppColors.text2)),
                  Flexible(child: Text(' · ${past ? md(p.updatedAt) : editedAgo(p.updatedAt)}', style: AppText.caption, overflow: TextOverflow.ellipsis)),
                ]),
              ]),
            ),
          ]),
          if (row.pendingOps > 0 && !past) ...[
            const Divider(height: 20, color: AppColors.line),
            Row(children: [
              const Icon(Icons.auto_awesome, size: 14, color: AppColors.ai),
              const SizedBox(width: 4),
              Text('AI가 일정 ${row.pendingOps}개를 제안했어요', style: AppText.caption.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700)),
            ]),
          ],
        ]),
      ),
    );
  }
}

class _AiPromo extends StatelessWidget {
  const _AiPromo({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Material(
        color: AppColors.aiSoft,
        borderRadius: BorderRadius.circular(16),
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
            child: Row(children: [
              Container(
                width: 32,
                height: 32,
                decoration: const BoxDecoration(color: AppColors.surface, shape: BoxShape.circle),
                child: const Icon(Icons.auto_awesome, color: AppColors.ai, size: 18),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('어디로 떠나고 싶으세요?', style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
                  Text('한 줄만 말하면 AI가 일정 초안을 만들어요', style: AppText.caption.copyWith(color: AppColors.text2)),
                ]),
              ),
              const Icon(Icons.chevron_right, color: AppColors.ai),
            ]),
          ),
        ),
      );
}
