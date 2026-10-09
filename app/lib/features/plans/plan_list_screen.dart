import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/app_session.dart';
import '../../core/format.dart';
import '../../core/models/account.dart';
import '../../core/models/plan.dart';
import '../../core/models/safety.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../../core/widgets/safety_widgets.dart';
import '../planner/planner_shell.dart';
import '../settings/settings_screen.dart';
import 'create_plan_screen.dart';

class _PlanRow {
  _PlanRow(this.plan, this.pendingProposals);
  final PlanSummary plan;
  final int pendingProposals; // 승인 대기 중인 AI 제안 수
  SafetyCheck? safety; // 시작일이 오늘~3일 안일 때만
  bool safetyFailed = false;
  bool newAlert = false;
  TripState? trip; // 시작일 ≤ 오늘 ≤ 종료일일 때만
}

/// 02_Plan_List → 29_Plan_List_Status (Figma 73:5305). 계정 버튼 · 지금 여행 중 · 안전 배지.
class PlanListScreen extends StatefulWidget {
  const PlanListScreen({super.key});

  /// 오늘 (테스트·데모에서 바꿀 수 있다).
  static DateTime Function() today = () => dateOnly(DateTime.now());

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
    final today = PlanListScreen.today();
    try {
      final plans = await api.plans();
      final rows = await Future.wait(plans.map((p) async {
        int pending = 0;
        try {
          pending = (await api.proposals(p.id, status: 'pending')).length;
        } catch (_) {}
        final row = _PlanRow(p, pending);
        final days = p.startDate.difference(today).inDays;
        // 여행일: 동행 상태 (active 면 "지금 여행 중" 카드)
        if (!today.isBefore(p.startDate) && !today.isAfter(p.endDate)) {
          try {
            row.trip = await api.tripState(p.id);
          } catch (_) {}
        }
        // 오늘~3일 안: 안전 점검 + 새 경고. 3일 넘게 남으면 부르지 않는다 (29).
        if (days <= 3 && !p.endDate.isBefore(today)) {
          try {
            row.safety = await api.safety(p.id);
          } catch (_) {
            row.safetyFailed = true;
          }
          try {
            row.newAlert = (await api.safetyAlerts(p.id)).isNotEmpty;
          } catch (_) {}
        }
        return row;
      }));
      if (!mounted) return;
      setState(() {
        _rows = rows;
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
              'travel_mode': ?i.travelMode,
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

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    final today = PlanListScreen.today();
    final traveling = (rows ?? []).where((r) => r.trip?.active == true).toList()
      ..sort((a, b) => (b.trip!.startedAt ?? DateTime(0)).compareTo(a.trip!.startedAt ?? DateTime(0)));
    final current = traveling.firstOrNull;
    final upcoming = (rows ?? []).where((r) => r != current && !r.plan.endDate.isBefore(today)).toList()..sort((a, b) => a.plan.startDate.compareTo(b.plan.startDate));
    final past = (rows ?? []).where((r) => r.plan.endDate.isBefore(today)).toList()..sort((a, b) => b.plan.startDate.compareTo(a.plan.startDate));

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
                _CircleButton(
                  icon: Icons.person_outline,
                  onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const SettingsScreen())),
                  tooltip: '설정',
                  bg: AppColors.surface,
                  fg: AppColors.text2,
                ),
                const SizedBox(width: 8),
                _CircleButton(icon: Icons.add, onTap: _create, tooltip: '새 여행 만들기'),
              ]),
              const SizedBox(height: 24),
              if (rows == null && _error == null)
                const Padding(padding: EdgeInsets.all(48), child: Center(child: CircularProgressIndicator()))
              else if (rows == null)
                StateView(icon: Icons.dns_outlined, title: '서버에 연결하지 못했어요', message: userMessage(_error!), actionLabel: '다시 시도', onAction: _load)
              else ...[
                if (current != null) ...[
                  const _SectionTitle('지금 여행 중'),
                  _TripCard(row: current, onOpen: () => _open(current.plan.id)),
                  const SizedBox(height: 16),
                ],
                if (upcoming.isNotEmpty) ...[
                  const _SectionTitle('다가오는 여행'),
                  for (final (i, r) in upcoming.indexed) _PlanCard(row: r, index: i, today: today, onTap: () => _open(r.plan.id), onLongPress: () => _actions(r.plan)),
                  const SizedBox(height: 12),
                ],
                if (past.isNotEmpty) ...[
                  const _SectionTitle('지난 여행'),
                  for (final r in past) _PlanCard(row: r, past: true, today: today, onTap: () => _open(r.plan.id), onLongPress: () => _actions(r.plan)),
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
          child: InkWell(customBorder: const CircleBorder(), onTap: onTap, child: SizedBox(width: 44, height: 44, child: Icon(icon, color: fg))),
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

/// 남은 일정 위험 배지 (overall_risk + 주의·위험 수). null 은 회색 "판단 불가".
Widget _overallBadge(SafetyCheck s, {String suffix = ''}) {
  final risk = s.overallRisk;
  final n = (s.counts['caution'] ?? 0) + (s.counts['danger'] ?? 0);
  final label = switch (risk) {
    'safe' => '안전$suffix',
    'caution' || 'danger' => '${riskWord(risk)} · $n곳',
    _ => '판단 불가',
  };
  return RiskChip(risk: risk, label: label);
}

/// 여행 중 카드 (Green 테두리 #CDEFD9 r20). [동행 화면 열기] → 14 (동행 모드 화면은 다음 단계, 지금은 계획 화면을 연다).
class _TripCard extends StatelessWidget {
  const _TripCard({required this.row, required this.onOpen});
  final _PlanRow row;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final t = row.trip!;
    final s = row.safety;
    final line = [if (t.currentState == 'visiting') '지금 방문 중', if (t.nextTitle != null) '다음 ${t.nextTitle}'].join(' · ');
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(20), border: Border.all(color: const Color(0xFFCDEFD9)), boxShadow: AppShadow.card),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Container(width: 72, height: 72, decoration: BoxDecoration(color: AppColors.successSoft, borderRadius: BorderRadius.circular(12)), child: const Icon(Icons.near_me_outlined, color: AppColors.success, size: 28)),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Expanded(child: Text(row.plan.title, style: AppText.section, maxLines: 1, overflow: TextOverflow.ellipsis)),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(color: AppColors.successSoft, borderRadius: BorderRadius.circular(10)),
                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                    Container(width: 6, height: 6, decoration: const BoxDecoration(color: AppColors.success, shape: BoxShape.circle)),
                    const SizedBox(width: 4),
                    Text('여행 중', style: AppText.caption.copyWith(color: AppColors.success, fontWeight: FontWeight.w700)),
                  ]),
                ),
              ]),
              if (line.isNotEmpty) Text(line, style: AppText.meta),
              const SizedBox(height: 8),
              Wrap(spacing: 6, runSpacing: 6, children: [
                if (s != null) _overallBadge(s, suffix: ' · 남은 일정'),
                InfoChip(label: '${row.plan.dayEnd} 종료'),
              ]),
            ]),
          ),
        ]),
        const SizedBox(height: 14),
        AppButton(label: '동행 화면 열기', icon: Icons.near_me_outlined, onPressed: onOpen),
      ]),
    );
  }
}

class _PlanCard extends StatelessWidget {
  const _PlanCard({required this.row, required this.today, this.index = 0, this.past = false, required this.onTap, required this.onLongPress});
  final _PlanRow row;
  final DateTime today;
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
    if (past) return null;
    if (!today.isBefore(p.startDate)) return '오늘 여행';
    return 'D-${p.startDate.difference(today).inDays}';
  }

  @override
  Widget build(BuildContext context) {
    final p = row.plan;
    final (icon, fg, bg) = past ? (Icons.image_outlined, AppColors.text3, AppColors.field) : _thumbs[index % _thumbs.length];
    final dday = _dday(p);
    final titleColor = past ? AppColors.text3 : AppColors.text;
    final daysLeft = p.startDate.difference(today).inDays;
    final checkFrom = p.startDate.subtract(const Duration(days: 3));
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: AppCard(
        radius: 20,
        padding: const EdgeInsets.all(16),
        onTap: onTap,
        onLongPress: onLongPress,
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Container(width: 72, height: 72, decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(12)), child: Icon(icon, color: fg, size: 28)),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
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
              Text('${dateRange(p.startDate, p.endDate)} · ${p.itemCount}곳', style: AppText.meta.copyWith(color: past ? AppColors.text3 : AppColors.text2)),
              if (!past) ...[
                const SizedBox(height: 8),
                Wrap(spacing: 6, runSpacing: 6, crossAxisAlignment: WrapCrossAlignment.center, children: [
                  if (row.safety != null)
                    Stack(clipBehavior: Clip.none, children: [
                      _overallBadge(row.safety!),
                      if (row.newAlert)
                        Positioned(right: -2, top: -2, child: Container(width: 8, height: 8, decoration: const BoxDecoration(color: AppColors.danger, shape: BoxShape.circle))),
                    ])
                  else if (daysLeft > 3)
                    InfoChip(label: '날씨는 ${checkFrom.month}/${two(checkFrom.day)}부터 확인', icon: Icons.shield_outlined)
                  else if (row.safetyFailed)
                    const InfoChip(label: '안전 정보 없음', icon: Icons.cloud_off_outlined),
                  if (row.pendingProposals > 0) InfoChip(label: 'AI 제안 ${row.pendingProposals}개', icon: Icons.auto_awesome, fg: AppColors.ai, bg: AppColors.aiSoft),
                ]),
              ] else ...[
                const SizedBox(height: 6),
                Text(md(p.updatedAt), style: AppText.caption),
              ],
            ]),
          ),
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
                  Text('한 줄만 말하면 구룡메이트가 일정 초안을 만들어요', style: AppText.caption.copyWith(color: AppColors.text2)),
                ]),
              ),
              const Icon(Icons.chevron_right, color: AppColors.ai),
            ]),
          ),
        ),
      );
}
