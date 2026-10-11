import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../core/app_session.dart';
import '../../core/format.dart';
import '../../core/models/trip.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../chat/ai_sheet.dart';
import '../chat/chat_controller.dart';
import '../map/map_screen.dart';
import '../trip/detour_sheet.dart';
import '../trip/event_inbox_screen.dart';
import '../trip/guide_note_screen.dart';
import '../trip/location.dart';
import '../trip/today_screen.dart';
import '../trip/trip_controller.dart';
import '../trip/trip_end_screen.dart';
import '../trip/trip_overlay.dart';
import '../trip/trip_start.dart';
import 'plan_controller.dart';
import 'planner_screen.dart';

/// 계획 하나의 화면 묶음. 계획 모드: Planner · Map / 동행 모드: Today · Planner · Map (Today 기본, README §3.3).
/// 세 탭은 같은 PlanController 를, 동행 화면·전역 오버레이는 같은 TripController 를 쓴다.
/// 모드는 스위치가 아니라 `GET /trip/plans/{id}/state` 의 active 다 (README §3.1).
class PlannerShell extends StatefulWidget {
  const PlannerShell({super.key, required this.planId, this.initialAiPrompt, this.startTrip = false, this.locationSource, this.tripTiming});
  final int planId;
  final String? initialAiPrompt;

  /// 29 [오늘 여행 시작] 으로 들어옴 → 11 을 바로 연다.
  final bool startTrip;

  /// 테스트에서 가짜 위치를 끼운다 (기본 geolocator).
  final LocationSource? locationSource;
  final TripTiming? tripTiming;

  @override
  State<PlannerShell> createState() => _PlannerShellState();
}

class _PlannerShellState extends State<PlannerShell> {
  late final PlanController plan;
  late final ChatController chat;
  late final TripController trip;
  int? _shownAppliedId;
  bool _sheetOpen = false;
  bool _wasActive = false;
  bool _moveDateRequested = false; // 12 [오늘로 옮기기] 뒤 날짜가 바뀌면 11 다시
  ModalRoute<dynamic>? _route;

  @override
  void initState() {
    super.initState();
    final session = context.read<AppSession>();
    final api = session.api;
    plan = PlanController(api, widget.planId)
      ..openAiHandler = _openAiFromPlan
      ..onStartTrip = _startFlow
      ..onEndTrip = _endFlow;
    chat = ChatController(api, plan);
    trip = TripController(api: api, plan: plan, session: session, source: widget.locationSource, timing: widget.tripTiming ?? const TripTiming())
      ..attachChat(chat)
      ..onOpenAi = _openAi
      ..onOpenGuide = _openGuide
      ..onShowDetour = _showDetour
      ..onOpenInbox = _openInbox
      ..onEnded = _onAutoEnded;
    tripHub.attach(trip);
    plan.addListener(_onPlanChanged);
    trip.addListener(_onTripChanged);
    plan.load().then((_) async {
      if (!mounted) return;
      chat.init();
      await trip.load();
      if (!mounted) return;
      final prompt = widget.initialAiPrompt;
      if (prompt != null && plan.plan != null) _openAi(prefill: prompt, send: true);
      await _afterTripLoad();
    });
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _route ??= ModalRoute.of(context);
  }

  @override
  void dispose() {
    tripHub.detach(trip);
    plan.removeListener(_onPlanChanged);
    trip.removeListener(_onTripChanged);
    trip.dispose();
    chat.dispose();
    plan.dispose();
    super.dispose();
  }

  // ---------- 모드 전환 ----------

  /// 동행 모드가 켜지면 Today 탭 + 헤더 "계획명 · 동행 중", 꺼지면 Planner 로.
  void _onTripChanged() {
    final active = trip.active;
    if (active == _wasActive) return;
    _wasActive = active;
    plan.tripActive = active;
    if (active) {
      final d = trip.state?.day;
      if (d != null) plan.setDay(d);
      plan.setTab(2);
      chat.contextLabel = '${plan.plan?.title ?? ''} · 동행 중';
    } else {
      if (plan.tab == 2) plan.setTab(0);
      chat.contextLabel = null;
    }
  }

  DateTime get _today => dateOnly(trip.state?.now ?? DateTime.now());

  Future<void> _afterTripLoad() async {
    _onTripChanged();
    final s = trip.state;
    final p = plan.plan;
    if (s == null || p == null || s.active) return;
    final prefs = await SharedPreferences.getInstance();
    final day = isoDate(_today);
    // 자동 종료(ended_reason: auto) 뒤 처음 열면 하루 요약을 한 번 (F9)
    if (s.endedReason == 'auto' && s.date != null && isoDate(s.date!) == day && !(prefs.getBool('trip_summary_${p.id}_$day') ?? false)) {
      await prefs.setBool('trip_summary_${p.id}_$day', true);
      if (mounted) await _showSummary(s);
      return;
    }
    if (widget.startTrip) {
      await _startFlow();
      return;
    }
    // 오늘이 여행일이고 첫 일정 1시간 전 이후면 그날 한 번 "오늘 여행 시작할까요?" (자동 시작 안 함, 11)
    final today = _today;
    if (today.isBefore(p.startDate) || today.isAfter(p.endDate)) return;
    if (s.endedAt != null && s.date != null && isoDate(s.date!) == day) return; // 오늘 이미 끝낸 여행
    final key = 'trip_prompt_${p.id}_$day';
    if (prefs.getBool(key) ?? false) return;
    final dayItems = p.dayOf(today.difference(p.startDate).inDays + 1)?.items.where((i) => !i.skipped).toList() ?? const [];
    final first = toMinutes(dayItems.firstOrNull?.startAt);
    final now = s.now ?? DateTime.now();
    if (first != null && now.hour * 60 + now.minute < first - 60) return;
    await prefs.setBool(key, true);
    if (mounted) await _startFlow();
  }

  /// 11 → (13) → start → 14. 409 not_trip_day → 12.
  Future<void> _startFlow() async {
    if (!mounted) return;
    final r = await showTripStartSheet(context, trip);
    if (!mounted || r == null) return;
    if (r.kind == 'started') {
      plan.setTab(2);
      return;
    }
    if (r.kind == 'not_trip_day') {
      final move = await showNotTripDayDialog(context, title: plan.plan?.title ?? '', detail: r.detail, today: _today);
      if (!move || !mounted) return;
      _moveDateRequested = true;
      final t = _today;
      await _openAi(prefill: '일정을 오늘(${t.month}/${two(t.day)})로 옮겨줘', send: true);
    }
  }

  /// [여행 종료] → 확인 → end → 30.
  Future<void> _endFlow() async {
    if (!await confirmEndTrip(context) || !mounted) return;
    try {
      final s = await trip.end();
      if (mounted) await _showSummary(s);
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  /// 동행 중에 자동 종료됨 (state 가 inactive 로 바뀜).
  void _onAutoEnded(TripState s) {
    if (s.endedReason != 'auto') return;
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool('trip_summary_${plan.planId}_${isoDate(_today)}', true);
      if (mounted) await _showSummary(s);
    });
  }

  Future<void> _showSummary(TripState s) => Navigator.of(context).push(MaterialPageRoute<void>(
        fullscreenDialog: true,
        builder: (_) => TripEndScreen(api: plan.api, planId: plan.planId, state: s, heard: Map.of(trip.heard), onContinueChat: () => _openAi()),
      ));

  // ---------- 계획 변경 ----------

  /// 제안 적용 직후: 시트를 내리고 "AI가 일정 N개를 변경했어요 · 실행 취소" 스낵바 (4초).
  void _onPlanChanged() {
    final p = plan.plan;
    if (_moveDateRequested && p != null && !trip.active && dateOnly(p.startDate) == _today) {
      // 12 → 날짜 변경 제안 승인 → 11 다시
      _moveDateRequested = false;
      WidgetsBinding.instance.addPostFrameCallback((_) async {
        if (!mounted) return;
        await Future<void>.delayed(const Duration(milliseconds: 800));
        if (mounted && _sheetOpen) Navigator.of(context).pop();
        if (mounted) _startFlow();
      });
    }
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
      // 동행 중: 승인으로 남은 일정·위험이 바뀌었으니 state·위험 구역도 다시 (events.md §6)
      if (trip.active) {
        trip.refreshState();
        trip.loadZones(force: true);
      }
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

  // ---------- AI 시트 · push 화면 ----------

  /// Planner·Map FAB: 동행 중 대기 선제 제안이 있으면 그 메시지로.
  void _openAiFromPlan({String? prefill, bool send = false}) {
    if (prefill == null && trip.hasSuggestion) {
      trip.openAiFromFab();
    } else {
      _openAi(prefill: prefill, send: send);
    }
  }

  /// 셸 위에 쌓인 push 화면(가이드 노트·알림 등)을 걷어 낸다 (상단 미리보기 배너 → Shell 로 돌아가 시트 열기).
  void _popToShell() {
    final r = _route;
    if (r != null && !r.isCurrent && !_sheetOpen) Navigator.of(context).popUntil((x) => x == r);
  }

  Future<void> _openAi({int? messageId, String? prefill, bool send = false, String? placeTitle}) async {
    if (!mounted) return;
    if (_sheetOpen) {
      if (messageId != null) chat.focus(messageId);
      if (send && prefill != null) chat.send(prefill);
      return;
    }
    _popToShell();
    if (trip.active) {
      // 헤더 컨텍스트: 장소 질문이면 "장소 · 현장 해설", 아니면 "계획 · 동행 중" (18)
      chat.contextLabel = placeTitle != null ? '$placeTitle · 현장 해설' : '${plan.plan?.title ?? ''} · 동행 중';
    }
    _sheetOpen = true;
    trip.setSheetOpen(true);
    if (messageId != null) chat.focus(messageId);
    await showAiSheet(context, plan: plan, chat: chat, prefill: send ? null : prefill, sendNow: send ? prefill : null, full: messageId != null);
    _sheetOpen = false;
    trip.setSheetOpen(false);
    if (trip.active) chat.contextLabel = '${plan.plan?.title ?? ''} · 동행 중';
  }

  Future<void> _openGuide(String placeId, {String? length, TripEvent? event}) async {
    if (!mounted) return;
    _popToShell();
    await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => GuideNoteScreen(t: trip, placeId: placeId, initialLength: length)));
  }

  Future<void> _openInbox() => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => EventInboxScreen(t: trip)));

  Future<void> _showDetour(TripEvent e) async {
    if (!mounted) return;
    _popToShell();
    await showDetourSheet(context, trip, e);
  }

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: plan),
        ChangeNotifierProvider.value(value: chat),
        ChangeNotifierProvider.value(value: trip),
      ],
      child: Consumer2<PlanController, TripController>(
        builder: (context, c, t, _) {
          final active = t.active;
          final tab = !active && c.tab == 2 ? 0 : c.tab;
          return Scaffold(
            body: IndexedStack(index: tab, children: [
              const PlannerScreen(),
              const MapScreen(),
              if (active) TodayScreen(onEndTrip: _endFlow) else const SizedBox.shrink(),
            ]),
            floatingActionButton: tab == 0 && c.plan != null ? PlannerFabs(hasSuggestion: t.hasSuggestion) : null,
            // 탭을 바꿀 때 버튼이 화면과 함께 바로 나타나고 사라지게 (기본은 회전·크기 애니메이션이라 화면보다 늦게 따라온다).
            // 셸 Scaffold 에 둔 이유(스낵바가 뜨면 함께 위로 비켜남)는 그대로다.
            floatingActionButtonAnimator: FloatingActionButtonAnimator.noAnimation,
            bottomNavigationBar: _BottomNav(
              index: tab,
              companion: active,
              onTap: (i) {
                if (i != c.tab) ScaffoldMessenger.of(context).hideCurrentSnackBar();
                c.setTab(i);
              },
            ),
          );
        },
      ),
    );
  }
}

/// 계획 모드 Planner · Map, 동행 모드 Today · Planner · Map (Components_Companion › Bottom Navigation 3 tabs).
/// index 는 PlanController.tab (0 Planner · 1 Map · 2 Today).
class _BottomNav extends StatelessWidget {
  const _BottomNav({required this.index, required this.companion, required this.onTap});
  final int index;
  final bool companion;
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
          child: Row(children: [
            if (companion) tab(2, Icons.near_me_outlined, 'Today'),
            tab(0, Icons.calendar_month_outlined, 'Planner'),
            tab(1, Icons.map_outlined, 'Map'),
          ]),
        ),
      ),
    );
  }
}
