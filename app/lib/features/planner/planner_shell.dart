import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/app_session.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../chat/ai_sheet.dart';
import '../chat/chat_controller.dart';
import '../map/map_screen.dart';
import 'plan_controller.dart';
import 'planner_screen.dart';

/// 계획 하나의 화면 묶음: Planner / Map 탭 + AI 시트. 두 탭은 같은 PlanController 를 공유한다.
class PlannerShell extends StatefulWidget {
  const PlannerShell({super.key, required this.planId, this.initialAiPrompt});
  final int planId;
  final String? initialAiPrompt;

  @override
  State<PlannerShell> createState() => _PlannerShellState();
}

class _PlannerShellState extends State<PlannerShell> {
  late final PlanController plan;
  late final ChatController chat;
  int? _shownAppliedId;
  bool _sheetOpen = false;

  @override
  void initState() {
    super.initState();
    final api = context.read<AppSession>().api;
    plan = PlanController(api, widget.planId)..openAiHandler = _openAi;
    chat = ChatController(api, plan);
    plan.addListener(_onPlanChanged);
    plan.load().then((_) {
      if (!mounted) return;
      chat.init();
      final prompt = widget.initialAiPrompt;
      if (prompt != null && plan.plan != null) _openAi(prefill: prompt, send: true);
    });
  }

  @override
  void dispose() {
    plan.removeListener(_onPlanChanged);
    chat.dispose();
    plan.dispose();
    super.dispose();
  }

  /// 제안 적용 직후: 시트를 내리고 "AI가 일정 N개를 변경했어요 · 실행 취소" 스낵바 (4초).
  void _onPlanChanged() {
    final applied = plan.lastApplied;
    if (applied == null || applied.proposal.id == _shownAppliedId) return;
    _shownAppliedId = applied.proposal.id;
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      if (_sheetOpen) {
        await Future<void>.delayed(const Duration(milliseconds: 500));
        if (mounted && _sheetOpen) Navigator.of(context).pop();
      }
      if (!mounted) return;
      final n = applied.changeCount;
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(
          duration: const Duration(seconds: 4), // 실행 취소는 승인 직후 4초만 (c9)
          content: Row(children: [
            const Icon(Icons.auto_awesome, color: Color(0xFFB9A8FF), size: 18),
            const SizedBox(width: 8),
            Expanded(child: Text(n > 0 ? 'AI가 일정 $n개를 변경했어요' : 'AI 변경안을 적용했어요')),
          ]),
          action: SnackBarAction(
            label: '실행 취소',
            textColor: const Color(0xFF9DB8FF),
            onPressed: () async {
              try {
                await plan.undoLastApplied();
                if (mounted) showInfo(context, '변경 전 일정으로 되돌렸어요');
              } catch (e) {
                if (mounted) showError(context, e);
              }
            },
          ),
        ));
    });
  }

  Future<void> _openAi({String? prefill, bool send = false}) async {
    if (_sheetOpen) {
      if (send && prefill != null) chat.send(prefill);
      return;
    }
    _sheetOpen = true;
    await showAiSheet(context, plan: plan, chat: chat, prefill: send ? null : prefill, sendNow: send ? prefill : null);
    _sheetOpen = false;
  }

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: plan),
        ChangeNotifierProvider.value(value: chat),
      ],
      child: Consumer<PlanController>(
        builder: (context, c, _) => Scaffold(
          body: IndexedStack(index: c.tab, children: const [PlannerScreen(), MapScreen()]),
          floatingActionButton: c.tab == 0 && c.plan != null ? const PlannerFabs() : null,
          bottomNavigationBar: _BottomNav(
            index: c.tab,
            onTap: (t) {
              if (t != c.tab) ScaffoldMessenger.of(context).hideCurrentSnackBar();
              c.setTab(t);
            },
          ),
        ),
      ),
    );
  }
}

class _BottomNav extends StatelessWidget {
  const _BottomNav({required this.index, required this.onTap});
  final int index;
  final ValueChanged<int> onTap;

  @override
  Widget build(BuildContext context) {
    Widget tab(int i, IconData icon, String label) {
      final on = i == index;
      final color = on ? AppColors.primary : AppColors.text3;
      return Expanded(
        child: InkWell(
          onTap: () => onTap(i),
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(icon, color: color, size: 24),
            const SizedBox(height: 4),
            Text(label, style: AppText.caption.copyWith(color: color, fontWeight: on ? FontWeight.w700 : FontWeight.w500)),
          ]),
        ),
      );
    }

    return Container(
      decoration: const BoxDecoration(color: AppColors.surface, border: Border(top: BorderSide(color: AppColors.line))),
      child: SafeArea(
        top: false,
        child: SizedBox(
          height: 60,
          child: Row(children: [tab(0, Icons.calendar_month_outlined, 'Planner'), tab(1, Icons.map_outlined, 'Map')]),
        ),
      ),
    );
  }
}
