import 'dart:async';
import 'dart:math' as math;

import 'package:clock/clock.dart';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import '../../core/api/api_client.dart';
import '../../core/api/guryong_api.dart';
import '../../core/app_session.dart';
import '../../core/format.dart' show dateOnly;
import '../../core/models/account.dart';
import '../../core/models/plan.dart';
import '../../core/models/safety.dart';
import '../chat/chat_controller.dart';
import '../planner/plan_controller.dart';
import 'location.dart';

/// 폴링 주기 (events.md §5). 테스트가 줄일 수 있게 묶어 둔다.
class TripTiming {
  const TripTiming({
    this.poll = const Duration(seconds: 5),
    this.pollPushed = const Duration(seconds: 10),
    this.pollWaiting = const Duration(seconds: 2),
    this.waitingFor = const Duration(seconds: 60),
    this.state = const Duration(seconds: 15),
    this.stateDemo = const Duration(seconds: 5),
    this.stateDemoTrip = const Duration(seconds: 1),
    this.planCheck = const Duration(seconds: 20),
    this.planCheckDemo = const Duration(seconds: 5),
    this.markerGlide = const Duration(seconds: 1),
    this.zones = const Duration(seconds: 60),
    this.toast = const Duration(seconds: 4),
    this.previewCollapse = const Duration(seconds: 8),
    this.arrivalCollapse = const Duration(minutes: 2),
  });
  final Duration poll;
  final Duration pollPushed;
  final Duration pollWaiting;
  final Duration waitingFor;
  final Duration state;

  /// 데모 위치 모드: 서버에 들어온 위치(재생기)를 그리므로 state 를 더 자주 읽는다.
  final Duration stateDemo;

  /// 데모 위치 모드 + 여행 중: 재생기·관제 화면이 약 1초마다 보내는 위치를 끊기지 않게 따라 그린다.
  final Duration stateDemoTrip;

  /// 동행 중 계획 version 가벼운 확인 (`GET /planner/plans` 목록의 version·progress_version) 주기.
  /// 다른 클라이언트(진행 스크립트·대시보드)가 제안을 승인하면 다음 도착·출발 전에도 일정을 다시 읽는다 (audit-2026-10-12 F2).
  final Duration planCheck;

  /// 데모 위치 모드: 발표 중 다른 화면에서 승인한 일정이 곧 보이게 더 자주.
  final Duration planCheckDemo;

  /// 실제 GPS 모드에서 사용자 마커가 새 위치로 옮겨 가는 시간 (위치는 30m·45초마다 들어와 주기가 일정하지 않다).
  final Duration markerGlide;
  final Duration zones;
  final Duration toast;
  final Duration previewCollapse;
  final Duration arrivalCollapse;
}

/// 동행 모드 상태 (계획 하나). 셸이 만들고 Today · 지도 · AI 시트 · 전역 오버레이가 함께 본다.
///
/// - 모드는 `GET /trip/plans/{id}/state` 의 active 로 정한다 (README §3.1).
/// - 이벤트는 위치 전송 응답과 폴링(`GET …/events?after=`) 두 경로로 받고, 같은 id 는 한 번만 처리한다 (events.md §4).
/// - 화면에는 동시에 하나씩: 위험 구역 경고 1 + (도착 카드 또는 선제 미리보기) 1 + 토스트 1. 우선순위는 안전 > 일정 영향 > 안내 (events.md §2).
typedef TodayChrome = ({double barBottom, Rect? fab});

class TripController extends ChangeNotifier with WidgetsBindingObserver {
  TripController({
    required this.api,
    required this.plan,
    required this.session,
    LocationSource? source,
    this.timing = const TripTiming(),
  }) : source = source ?? (kIsWeb ? const NoDeviceLocation() : GeolocatorSource());

  final GuryongApi api;
  final PlanController plan;
  final AppSession session;
  final LocationSource source;
  final TripTiming timing;
  ChatController? _chat;

  int get planId => plan.planId;

  // ---------- 상태 ----------

  TripState? state;
  Object? stateError;
  bool loaded = false;
  bool busy = false; // 시작·종료 요청 중
  Duration _offset = Duration.zero; // 서버 벽시계 - 기기 시계
  bool offline = false;
  LocationAccess? access;
  List<AvoidZone> zones = const [];
  DateTime? _zonesAt;

  /// 위험 구역 "지금 기준" 시각 (avoid 응답의 now). Today·Map 이 같은 구역을 그린다.
  DateTime? zonesNow;

  /// 손으로 고른 길 안내 장소 (다음 일정 🧭 · 지도 작은 창 [길 안내] · Map 우회 [이 길로 안내]). null 이면 자동 (이동 중 → 다음 장소).
  /// 도착·출발로 지금 상태가 바뀌면 지운다 (redesign §2.1).
  int? guideTo;

  bool get active => state?.active == true;
  bool get demoMode => session.demoLocationMode;

  /// 위치가 꺼져 있다 (14-S1 배너). 데모 위치 모드는 GPS 를 쓰지 않으므로 해당 없음.
  bool get locationOff => active && !demoMode && access != null && access != LocationAccess.granted;

  /// 서버의 지금 (가상 시각이면 그 시각) — 마지막 state 의 now 에 그 뒤 흐른 시간을 더한다.
  DateTime serverNow() => clock.now().add(_offset);

  // ---------- 이벤트 ----------

  final _events = <int, TripEvent>{};
  int _lastId = 0;
  final _seenSent = <int>{};
  DateTime? _waitSince;

  /// 오늘 이벤트 (오래된 순). 여러 날 여행에서 지난 여행일 이벤트는 뺀다 (audit-2026-10-12 F7).
  List<TripEvent> get events => (_events.values.where(isToday).toList()..sort((a, b) => a.id.compareTo(b.id)));

  /// 이 이벤트가 지금 여행일의 것인지 (여행일·이벤트 day 를 모르면 그렇다고 본다).
  bool isToday(TripEvent e) {
    final d = state?.day;
    return d == null || e.day == null || e.day == d;
  }

  /// 알림 목록 배지: seen 안 된 level ≥ 1 (24). 오늘 것만.
  int get unseenCount => _events.values.where((e) => isToday(e) && e.level >= 1 && !e.suppressed && e.seenAt == null && !_seenSent.contains(e.id)).length;

  bool get waitingAgent => _events.values.any((e) => isToday(e) && e.waitingAgent);

  // ---------- 화면에 떠 있는 것 ----------

  /// 23 위험 구역 경고 (최상단). collapsed 면 32 높이 띠.
  TripEvent? hazard;
  bool hazardCollapsed = false;

  /// 16 도착 해설 카드. collapsed 면 작은 "해설 듣기" 칩.
  TripEvent? arrival;
  bool arrivalCollapsed = false;

  /// 14-S3 선제 미리보기 (대화 밖). collapsed 면 FAB 점만.
  TripEvent? preview;
  bool previewCollapsed = false;
  final _previewQueue = <TripEvent>[];

  /// 출발·안전 완화 토스트 (4초).
  TripEvent? toast;
  final _toastQueue = <TripEvent>[];

  /// FAB Has Suggestion: 아직 보지 않은 대기 제안이 있는 선제 이벤트 id.
  final suggestions = <int>{};

  /// 가이드 노트를 연 장소 → 고른 길이 (30 "들은 해설", 24 "해설 들음 · 짧게"). 로컬 기록.
  final heard = <String, String>{};

  /// 17 Segment 선택 (다음 장소 기본값, 로컬).
  String? preferredLength;

  bool get hasSuggestion => suggestions.isNotEmpty;

  // ---------- 셸이 붙이는 것 ----------

  /// push 화면(가이드 노트·알림 등) 깊이. 0 보다 크면 FAB 가 없으므로 미리보기를 상단 배너로, 폴링은 10초.
  int pushDepth = 0;
  bool sheetOpen = false;

  /// Today 가 지도 위에 둔 것 (Today·오버레이 공통 좌표): 위 막대 아래 y · AI·현재 위치 버튼 칸(숨으면 null).
  /// 버튼이 패널을 따라 움직이므로 도착 카드·미리보기·토스트가 이것을 보고 버튼과 겹치지 않게 자리를 잡는다.
  final todayChrome = ValueNotifier<TodayChrome?>(null);

  void Function({int? messageId, String? prefill, bool send, String? placeTitle})? onOpenAi;
  void Function(String placeId, {String? length, TripEvent? event})? onOpenGuide;
  void Function(TripEvent event)? onShowDetour;
  void Function()? onOpenInbox;
  void Function(TripState ended)? onEnded;

  Timer? _pollTimer;
  Timer? _stateTimer;
  Timer? _toastTimer;
  Timer? _previewTimer;
  Timer? _arrivalTimer;
  LocationSender? sender;
  bool _companion = false;
  bool _observing = false;
  bool _disposed = false;
  bool _polling = false;

  /// 셸의 대화 (현장 가이드 17 이 같은 세션으로 묻는다).
  ChatController? get chat => _chat;

  // ---------- 가이드 노트 (Today 한 줄 · 열린 17 갱신) ----------

  /// 장소 → 노트 요약 (`GET /docent/plans/{id}/notes`). null 이면 아직 읽지 않음.
  Map<String, GuideNoteSummary>? guideNotes;
  DateTime? _notesAt;
  Future<void>? _notesLoading;

  bool get guidePreparing => guideNotes?.values.any((n) => n.status == 'pending' || n.status == 'preparing') ?? false;

  /// 노트 목록을 다시 읽는다 (가볍게: 동행 시작·도착 이벤트·패널을 펼칠 때·준비 중이면 폴링 때). [minGap] 안에 읽었으면 건너뛴다.
  Future<void> loadGuideNotes({Duration minGap = Duration.zero}) {
    final at = _notesAt;
    if (at != null && clock.now().difference(at) < minGap) return Future.value();
    return _notesLoading ??= _loadGuideNotes().whenComplete(() => _notesLoading = null);
  }

  Future<void> _loadGuideNotes() async {
    try {
      final list = await api.guideNotes(planId);
      _notesAt = clock.now();
      guideNotes = {for (final n in list) n.placeId: n};
      _notify();
    } catch (_) {}
  }

  void attachChat(ChatController chat) {
    _chat?.removeListener(_onChat);
    _chat = chat..addListener(_onChat);
  }

  @override
  void dispose() {
    _disposed = true;
    _stopCompanion();
    if (_observing) WidgetsBinding.instance.removeObserver(this);
    _observing = false;
    _chat?.removeListener(_onChat);
    session.removeListener(_onSession);
    todayChrome.dispose();
    super.dispose();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  // ---------- 시작 · 종료 ----------

  /// 계획 화면 진입: state 를 읽고 active 면 동행 모드를 켠다.
  /// 아니어도 여행 기간 중이면 state 를 주기적으로 (그리고 앱이 다시 보일 때) 읽어, 다른 클라이언트(진행 스크립트·대시보드)가
  /// 여행을 시작하면 동행 모드로 바뀐다 (audit-2026-10-12 F1).
  Future<void> load() async {
    session.addListener(_onSession);
    _observe();
    try {
      _setState(await api.tripState(planId));
      stateError = null;
    } catch (e) {
      stateError = e;
    }
    loaded = true;
    _notify();
    if (active) {
      await _startCompanion();
    } else {
      _scheduleState();
    }
  }

  void _observe() {
    if (_observing || _disposed) return;
    _observing = true;
    WidgetsBinding.instance.addObserver(this);
  }

  /// [여행 시작] (11). 409 not_trip_day 는 그대로 던진다 → 12.
  Future<TripState> start({String? sessionId}) async {
    busy = true;
    _notify();
    try {
      final s = await api.tripStart(planId, sessionId: sessionId);
      _setState(s);
      if (active) await _startCompanion();
      return s;
    } finally {
      busy = false;
      _notify();
    }
  }

  /// [여행 종료] (30). 진행 기록 외에는 바뀌는 것이 없다.
  Future<TripState> end() async {
    busy = true;
    _notify();
    try {
      final s = await api.tripEnd(planId);
      _stopCompanion();
      _setState(s);
      _clearOverlays();
      return s;
    } finally {
      busy = false;
      _notify();
    }
  }

  Future<void> _startCompanion() async {
    if (_companion || _disposed) return;
    _companion = true;
    _observe();
    _scheduleState();
    unawaited(loadZones());
    unawaited(loadGuideNotes());
    await poll(backlog: true);
    await _startLocation();
  }

  void _stopCompanion() {
    _companion = false;
    _pollTimer?.cancel();
    _stateTimer?.cancel();
    _stateTimer = null;
    _toastTimer?.cancel();
    _previewTimer?.cancel();
    _arrivalTimer?.cancel();
    sender?.stop();
  }

  void _clearOverlays() {
    hazard = arrival = preview = toast = null;
    _previewQueue.clear();
    _toastQueue.clear();
    suggestions.clear();
  }

  // ---------- 위치 ----------

  /// 위치 권한 확인 (11 → 13). 데모 위치 모드면 묻지 않는다.
  Future<LocationAccess> checkAccess() async {
    access = await source.check();
    _notify();
    return access!;
  }

  /// 13 [위치 허용] · 14-S1 [켜기]. 영구 거부·서비스 꺼짐이면 OS 설정.
  Future<LocationAccess> requestAccess() async {
    final cur = access ?? await source.check();
    if (cur == LocationAccess.deniedForever || cur == LocationAccess.serviceOff) {
      await source.openSettings(cur);
      access = await source.check();
    } else {
      access = await source.request();
    }
    _notify();
    if (access == LocationAccess.granted && active) await _startLocation();
    return access!;
  }

  Future<void> _startLocation() async {
    sender?.stop();
    if (!active || demoMode || _disposed) return;
    access ??= await source.check();
    if (access != LocationAccess.granted) {
      _notify();
      return;
    }
    sender = LocationSender(
      send: (points) => api.sendLocation(planId, points),
      serverNow: serverNow,
      onResult: (r) {
        _setState(r.state);
        ingest(r.events);
      },
      onOffline: (off) {
        if (offline != off) {
          offline = off;
          _notify();
        }
      },
      onInactive: refreshState,
    )..start(source.positions());
  }

  void _onSession() {
    // 데모 위치 모드를 켜면 GPS 전송을 멈추고, 끄면 다시 (33)
    if (!active) {
      if (loaded) _scheduleState(); // 여행 전 state 확인 주기 (데모면 더 짧게)
      return;
    }
    if (demoMode) {
      sender?.stop();
    } else if (sender?.running != true) {
      _startLocation();
    }
    _scheduleState();
    _notify();
  }

  // ---------- 상태 ----------

  /// state 주기 읽기. 동행 중이면 늘, 아니면 여행 기간 중일 때만 (다른 클라이언트가 시작한 여행을 알아차리게, F1).
  /// 주기: 동행 15초 (데모 위치 모드 1초), 여행 전 15초 (데모 위치 모드 5초). 같은 주기면 타이머를 다시 만들지 않는다.
  void _scheduleState() {
    final every = _disposed || !(_companion || _watchStart) ? null : _stateEvery;
    if (every != null && every == _stateTimerEvery && _stateTimer?.isActive == true) return;
    _stateTimer?.cancel();
    _stateTimer = null;
    _stateTimerEvery = every;
    if (every == null) return;
    _stateTimer = Timer.periodic(every, (_) {
      if (!_stateLoading) refreshState(); // 1초 주기에서 응답이 늦으면 쌓지 않는다
    });
  }

  Duration? _stateTimerEvery;

  /// 여행 전에도 state 를 지켜볼지: 오늘(서버 시각)이 계획 기간 안.
  bool get _watchStart {
    final p = plan.plan;
    if (!loaded || active || p == null) return false;
    final d = dateOnly(serverNow());
    return !d.isBefore(dateOnly(p.startDate)) && !d.isAfter(dateOnly(p.endDate));
  }

  Duration get _stateEvery => demoMode ? (active ? timing.stateDemoTrip : timing.stateDemo) : timing.state;

  /// 사용자 마커가 이전 위치에서 새 위치로 옮겨 가는 시간 (≈ 위치 갱신 주기).
  Duration get markerGlide => demoMode ? timing.stateDemoTrip : timing.markerGlide;

  bool _stateLoading = false;

  Future<void> refreshState() async {
    _stateLoading = true;
    var started = false;
    try {
      final was = active;
      _setState(await api.tripState(planId));
      if (offline) offline = false;
      // 다른 클라이언트가 여행을 시작함 → 동행 모드 (F1)
      started = !was && active && loaded && !_companion;
    } on ApiException catch (e) {
      if (e.isNetwork) offline = true;
    } catch (_) {
    } finally {
      _stateLoading = false;
    }
    _notify();
    if (started) {
      await _startCompanion();
    } else if (_companion) {
      unawaited(_checkPlanVersion());
    }
  }

  void _setState(TripState s) {
    final wasActive = active;
    final prev = state;
    state = s;
    if (s.now != null) _offset = s.now!.difference(clock.now());
    if (wasActive && !s.active) {
      _stopCompanion();
      _clearOverlays();
      onEnded?.call(s);
      _scheduleState(); // 여행 기간이 남았으면 다시 시작을 지켜본다
    }
    if (prev != null && (prev.currentItemId != s.currentItemId || prev.currentState != s.currentState)) {
      plan.load(silent: true).catchError((_) {}); // 진행 상태 (progress_version)
      guideTo = null;
    } else if (prev != null && prev.active && s.active && _planSign(prev) != _planSign(s)) {
      // 다음 일정·계획 시작·남은 항목이 바뀜 = 다른 곳에서 일정이 바뀜 (승인 등, F2)
      plan.load(silent: true).catchError((_) {});
    }
    _checkHazardLeft();
    _notify();
  }

  /// state 에서 계획이 바뀌었는지 볼 수 있는 값: 다음 일정·그 계획 시작(예상 아님)·남은 항목 목록 (expected 가 둘 다 있을 때만).
  static String _planSign(TripState s) => '${s.nextItemId}|${s.nextPlannedStart}|${s.expected == null ? '' : (s.expected!.items.keys.toList()..sort()).join(',')}';

  DateTime? _planCheckedAt;
  bool _planChecking = false;

  /// 동행 중 계획 version 가벼운 확인 (F2): state 에 version 이 없어 `GET /planner/plans` 목록에서 이 계획의
  /// version·progress_version 을 보고, 지금 일정과 다르면 다시 읽는다. 주기 [TripTiming.planCheck] (데모 위치 모드 planCheckDemo).
  Future<void> _checkPlanVersion() async {
    final cur = plan.plan, at = _planCheckedAt;
    if (cur == null || _planChecking || _disposed) return;
    if (at != null && clock.now().difference(at) < (demoMode ? timing.planCheckDemo : timing.planCheck)) return;
    _planChecking = true;
    _planCheckedAt = clock.now();
    try {
      final row = (await api.plans()).where((p) => p.id == planId).firstOrNull;
      final now = plan.plan;
      if (row != null && now != null && (row.version != now.version || row.progressVersion != now.progressVersion)) {
        await plan.load(silent: true);
      }
    } catch (_) {
    } finally {
      _planChecking = false;
    }
  }

  // ---------- 위험 구역 ----------

  Future<void> loadZones({bool force = false}) async {
    final at = _zonesAt;
    if (!force && at != null && clock.now().difference(at) < timing.zones) return;
    _zonesAt = clock.now();
    try {
      final r = await api.avoid();
      zones = r.$1;
      zonesNow = r.$2 ?? clock.now();
      _checkHazardLeft();
      _notify();
    } catch (_) {}
  }

  /// 접힌 경고 띠는 구역을 벗어나면 사라진다 (23 확인).
  void _checkHazardLeft() {
    final h = hazard, loc = state?.location;
    if (h == null || !hazardCollapsed || loc == null || h.now == null) return;
    if (loc.recordedAt != null && !loc.recordedAt!.isAfter(h.now!)) return;
    final ids = h.zones.map((z) => z.id).toSet();
    final mine = zones.where((z) => ids.contains(z.id)).toList();
    if (mine.isEmpty) return;
    final near = mine.any((z) => zoneDistanceM(z, loc.lat, loc.lng) <= 60);
    if (!near) hazard = null;
  }

  // ---------- 길 안내 ----------

  /// [itemId] 로 길 안내 (null 이면 자동으로 돌아감).
  void setGuide(int? itemId) {
    guideTo = itemId;
    _notify();
  }

  // ---------- 폴링 ----------

  Duration get pollInterval {
    if (waitingAgent) {
      _waitSince ??= clock.now();
      if (clock.now().difference(_waitSince!) < timing.waitingFor) return timing.pollWaiting;
    } else {
      _waitSince = null;
    }
    return pushDepth > 0 ? timing.pollPushed : timing.poll;
  }

  void _schedulePoll() {
    _pollTimer?.cancel();
    if (!_companion || _disposed) return;
    _pollTimer = Timer(pollInterval, poll);
  }

  /// 한 번에 읽는 이벤트 수. 이만큼 오면 다음 쪽을 이어 읽는다 (서버 최대 200).
  static const eventPage = 50;

  /// `GET …/events?after=`. 선제 턴을 기다리는 이벤트가 있으면 그 앞부터 다시 읽어 agent 가 채워졌는지 본다.
  /// 한 쪽([eventPage])이 가득 차면 끝까지 이어 읽는다 — 앱을 연 직후 쌓인 것이 많아도 모두 backlog 로 (옛 이벤트를 새것처럼 알리지 않게, F6).
  Future<void> poll({bool backlog = false}) async {
    if (_polling) return;
    _polling = true;
    final waiting = _events.values.where((e) => isToday(e) && e.waitingAgent).map((e) => e.id);
    var after = waiting.isEmpty ? _lastId : waiting.reduce(math.min) - 1;
    try {
      final list = <TripEvent>[];
      for (var page = 0; page < 40; page++) {
        final got = await api.tripEvents(planId, after: after, limit: eventPage);
        list.addAll(got);
        if (got.length < eventPage) break;
        after = got.map((e) => e.id).reduce(math.max);
      }
      if (offline) offline = false;
      ingest(list, backlog: backlog);
      // 준비 중인 노트가 있으면 폴링 때 같이 (10초에 한 번) — Today 한 줄 · 열린 17 이 새 노트로
      if (guidePreparing) unawaited(loadGuideNotes(minGap: const Duration(seconds: 10)));
    } on ApiException catch (e) {
      if (e.isNetwork) {
        offline = true;
        _notify();
      }
    } catch (_) {
    } finally {
      _polling = false;
      _schedulePoll();
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (!_companion) {
      // 여행 전: 다시 보이면 state 를 한 번 (그사이 다른 곳에서 여행을 시작했을 수 있다, F1)
      if (state == AppLifecycleState.resumed && loaded && !_disposed && !_stateLoading) refreshState();
      return;
    }
    if (state == AppLifecycleState.resumed) {
      // 포그라운드로 돌아옴: 즉시 1회 + state, 위치 다시
      refreshState();
      poll(backlog: true);
      if (sender != null && !sender!.running) _startLocation();
    } else if (state == AppLifecycleState.paused || state == AppLifecycleState.hidden) {
      sender?.stop(); // 화면이 켜진 동안만 (백그라운드 위치 없음)
    }
  }

  /// push 화면에 들어가고 나올 때 (폴링 10초 ↔ 5초, 미리보기 위치).
  /// 화면의 initState·dispose 에서 불리므로 알림은 다음 마이크로태스크로.
  void pushed(bool on) {
    pushDepth = math.max(0, pushDepth + (on ? 1 : -1));
    scheduleMicrotask(_notify);
  }

  // ---------- 이벤트 처리 ----------

  /// 위치 응답·폴링에서 받은 이벤트. [backlog] = 앱을 연 직후 쌓여 있던 것 (지난 0~1단계는 목록에만, events.md §2).
  void ingest(List<TripEvent> list, {bool backlog = false}) {
    final sorted = [...list]..sort((a, b) => a.id.compareTo(b.id));
    for (final e in sorted) {
      final old = _events[e.id];
      _events[e.id] = e;
      if (e.id > _lastId) _lastId = e.id;
      if (!isToday(e)) continue; // 지난 여행일 것은 기억만 하고 알리지 않는다 (F7)
      if (old == null) {
        _handleNew(e, backlog: backlog);
      } else if (old.waitingAgent && e.agentReady) {
        _proactiveReady(e, backlog: false);
      }
    }
    _notify();
  }

  void _handleNew(TripEvent e, {required bool backlog}) {
    if (e.suppressed) return;
    switch (e.type) {
      case 'day_ended':
        refreshState();
      case 'arrived':
        plan.load(silent: true).catchError((_) {});
        if (!backlog) unawaited(loadGuideNotes()); // 도착하면 서버가 그 장소 노트를 자동 준비한다 (docent §3.2)
        final cur = state;
        final stillHere = cur != null && cur.visiting && cur.currentItemId == (e.card?.itemId ?? e.payload['item_id']);
        if ((!backlog || (stillHere && e.seenAt == null)) && e.card != null) _showArrival(e);
      case 'departed':
        plan.load(silent: true).catchError((_) {});
        if (arrival != null && arrival!.card?.itemId == e.payload['item_id']) arrival = null; // 떠나면 카드 사라짐
        if (!backlog) _showToast(e);
      case 'safety_eased':
        plan.loadSafety();
        loadZones(force: true);
        if (!backlog) _showToast(e);
      case 'near_hazard':
        loadZones(force: true);
        if (!backlog) _showHazard(e);
      case 'delay' || 'safety_raised':
        if (e.type == 'safety_raised') {
          plan.loadSafety();
          loadZones(force: true);
        }
        if (e.agentReady) _proactiveReady(e, backlog: backlog);
    }
  }

  void _showHazard(TripEvent e) {
    hazard = e;
    hazardCollapsed = false;
    markSeen(e);
    HapticFeedback.heavyImpact();
    SystemSound.play(SystemSoundType.alert);
  }

  void _showArrival(TripEvent e) {
    arrival = e;
    arrivalCollapsed = preview != null && !previewCollapsed; // 3단계가 떠 있으면 칩으로
    if (sheetOpen) _chat?.showArrivalInChat(e);
    _arrivalTimer?.cancel();
    _arrivalTimer = Timer(timing.arrivalCollapse, () {
      if (arrival?.id == e.id) {
        arrivalCollapsed = true;
        _notify();
      }
    });
    if (!sheetOpen) markSeen(e);
  }

  void _showToast(TripEvent e) {
    final cur = toast;
    if (cur != null && cur.type == e.type) {
      toast = e; // 같은 종류는 마지막 것만
      _startToastTimer();
    } else if (cur != null) {
      _toastQueue
        ..removeWhere((x) => x.type == e.type)
        ..add(e);
      return;
    } else {
      toast = e;
      _startToastTimer();
    }
    markSeen(e);
  }

  void _startToastTimer() {
    _toastTimer?.cancel();
    _toastTimer = Timer(timing.toast, () {
      toast = _toastQueue.isEmpty ? null : _toastQueue.removeAt(0);
      if (toast != null) {
        markSeen(toast!);
        _startToastTimer();
      }
      _notify();
    });
  }

  void dismissToast() {
    _toastTimer?.cancel();
    toast = _toastQueue.isEmpty ? null : _toastQueue.removeAt(0);
    if (toast != null) _startToastTimer();
    _notify();
  }

  static int _rank(TripEvent e) => e.type == 'safety_raised' ? 2 : 1;

  /// 선제 턴이 끝남: 대화에 표시 줄을 붙이고, 시트가 열려 있으면 그 메시지로, 닫혀 있으면 FAB 미리보기.
  Future<void> _proactiveReady(TripEvent e, {required bool backlog}) async {
    final mid = e.agent?.messageId;
    final chat = _chat;
    if (mid != null) chat?.proactive[mid] = e;
    if (backlog && e.agent?.proposalId != null) {
      // 앱을 다시 열었을 때는 아직 처리되지 않은(대기) 제안만 알린다
      try {
        final p = await api.proposal(planId, e.agent!.proposalId!);
        if (!p.pending) {
          _notify();
          return;
        }
      } catch (_) {}
    }
    if (e.agent?.proposalId != null || mid != null) suggestions.add(e.id);
    if (chat != null) {
      if (chat.sessionId != null && chat.sessionId == e.agent?.sessionId) {
        await chat.reloadSession(force: true);
      } else if (e.agent?.sessionId != null && !chat.busy) {
        await chat.switchSession(e.agent!.sessionId!);
      }
    }
    if (sheetOpen) {
      chat?.focus(mid);
      suggestions.remove(e.id);
      markSeen(e);
    } else {
      _queuePreview(e);
    }
    _notify();
  }

  void _queuePreview(TripEvent e) {
    final cur = preview;
    if (cur == null || previewCollapsed || cur.id == e.id || _rank(e) > _rank(cur)) {
      if (cur != null && cur.id != e.id && !previewCollapsed) _previewQueue.insert(0, cur); // 낮은 것은 뒤에 줄 선다
      _setPreview(e);
    } else {
      _previewQueue.add(e);
      _previewQueue.sort((a, b) => _rank(b).compareTo(_rank(a)));
    }
  }

  void _setPreview(TripEvent e) {
    preview = e;
    previewCollapsed = false;
    if (arrival != null) arrivalCollapsed = true; // 도착 카드는 "해설 듣기" 칩으로 접는다
    markSeen(e);
    _previewTimer?.cancel();
    if (e.type != 'safety_raised') {
      // 안전은 사용자가 닫을 때까지 남는다
      _previewTimer = Timer(timing.previewCollapse, () {
        if (preview?.id == e.id) {
          previewCollapsed = true;
          _nextPreview();
          if (previewCollapsed && arrival != null) arrivalCollapsed = false;
          _notify();
        }
      });
    }
  }

  void _nextPreview() {
    if (_previewQueue.isEmpty) return;
    _setPreview(_previewQueue.removeAt(0));
  }

  /// 미리보기 × (점은 제안이 대기인 동안 남는다).
  void dismissPreview() {
    previewCollapsed = true;
    _previewTimer?.cancel();
    _nextPreview();
    if (previewCollapsed && arrival != null) arrivalCollapsed = false; // 높은 것이 닫히면 이어서 보인다
    _notify();
  }

  /// 미리보기·FAB·알림 행 탭 → AI 시트 Full + 그 메시지로.
  void openProactive(TripEvent e) {
    suggestions.remove(e.id);
    if (preview?.id == e.id) {
      preview = null;
      _previewTimer?.cancel();
      _nextPreview();
    }
    _notify();
    onOpenAi?.call(messageId: e.agent?.messageId);
  }

  /// FAB: 대기 중인 선제 제안이 있으면 그 메시지로, 없으면 그냥 시트.
  void openAiFromFab() {
    final ids = suggestions.toList()..sort();
    final e = ids.isEmpty ? null : _events[ids.last];
    if (e != null) {
      openProactive(e);
    } else {
      onOpenAi?.call();
    }
  }

  void collapseHazard() {
    hazardCollapsed = true;
    _checkHazardLeft();
    _notify();
  }

  void expandHazard() {
    hazardCollapsed = false;
    _notify();
  }

  void dismissArrival() {
    arrival = null;
    _arrivalTimer?.cancel();
    _notify();
  }

  void expandArrival() {
    arrivalCollapsed = false;
    _notify();
  }

  /// 16 도착 알림 누르기 → 17 (length 가 없으면 17 기본값 = 고른 길이 · 짧게).
  void openArrivalGuide(TripEvent e, String? length) {
    final placeId = e.card?.placeId ?? e.payload['place_id'] as String?;
    arrival = null;
    _chat?.showArrivalInChat(null);
    markSeen(e);
    _notify();
    if (placeId != null) onOpenGuide?.call(placeId, length: length, event: e);
  }

  /// 시트가 열리고 닫힐 때 (도착 카드는 시트 안의 로컬 카드로 옮긴다, 16 진입).
  void setSheetOpen(bool open) {
    sheetOpen = open;
    if (open && arrival != null) _chat?.showArrivalInChat(arrival);
    if (!open) _chat?.showArrivalInChat(null);
    _notify();
  }

  /// 이벤트가 화면에 보였으면 확인 표시 (events.md §5).
  void markSeen(TripEvent e) {
    if (e.seenAt != null || !_seenSent.add(e.id)) return;
    api.eventSeen(planId, e.id).catchError((_) {});
  }

  /// 대화에서 제안이 처리되면(승인·거절·대체) 점·미리보기를 거둔다.
  void _onChat() {
    final chat = _chat;
    if (chat == null || suggestions.isEmpty) return;
    var changed = false;
    for (final id in suggestions.toList()) {
      final pid = _events[id]?.agent?.proposalId;
      if (pid == null) continue;
      final entry = chat.proposalEntry(pid);
      if (entry != null && entry.state != ProposalState.pending && entry.state != ProposalState.applying) {
        suggestions.remove(id);
        if (preview?.id == id) preview = null;
        changed = true;
      }
    }
    if (changed) _notify();
  }

  // ---------- 화면용 계산 ----------

  /// 오늘 (여행일) 의 Day. 동행 중이면 그 여행일, 아니면 서버 시각의 오늘이 계획 기간 안일 때 그날
  /// (지난 여행일 state 의 day 가 아니라 — 2일차 시작 시트가 1일차를 보이지 않게, audit-2026-10-12 F5).
  PlanDay? get today => plan.plan?.dayOf(todayDay);

  int get todayDay {
    final s = state, p = plan.plan;
    if (s?.active == true && s?.day != null) return s!.day!;
    if (p != null && s?.now != null) {
      final d = dateOnly(serverNow()), start = dateOnly(p.startDate);
      final n = (DateTime.utc(d.year, d.month, d.day).difference(DateTime.utc(start.year, start.month, start.day)).inDays) + 1;
      if (n >= 1 && n <= p.numDays) return n;
    }
    return s?.day ?? plan.day;
  }

  PlanItem? item(int? id) => id == null ? null : plan.plan?.item(id);

  /// 대기 중인 지연 선제 제안 (ETA 칩 Red 판단). 알림이 가리킨 다음 일정이 지금 다음 일정일 때만 —
  /// 그 일정을 지나면(도착·출발) 지난 알림이라 칩은 지금 지연으로 그린다 (제안·점은 대화에 그대로 남는다, audit-2026-10-12 B2).
  TripEvent? get pendingDelay => events
      .where((e) =>
          e.type == 'delay' &&
          e.agentReady &&
          (e.payload['next'] as Map?)?['item_id'] == state?.nextItemId &&
          (suggestions.contains(e.id) || _chat?.proposalEntry(e.agent?.proposalId ?? -1)?.state == ProposalState.pending))
      .lastOrNull;

  TripEvent? event(int id) => _events[id];
}

/// 점에서 구역 다각형까지의 대략 거리 (안이면 0). 꼭짓점·변 중점 중 가장 가까운 것.
double zoneDistanceM(AvoidZone z, double lat, double lng) {
  final pts = z.polygon;
  if (pts.isEmpty) return double.infinity;
  var inside = false;
  for (var i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    final a = pts[i], b = pts[j];
    if ((a.latitude > lat) != (b.latitude > lat) && lng < (b.longitude - a.longitude) * (lat - a.latitude) / (b.latitude - a.latitude) + a.longitude) {
      inside = !inside;
    }
  }
  if (inside) return 0;
  var best = double.infinity;
  for (var i = 0; i < pts.length; i++) {
    final a = pts[i], b = pts[(i + 1) % pts.length];
    for (final t in const [0.0, 0.25, 0.5, 0.75]) {
      final d = distanceM(lat, lng, a.latitude + (b.latitude - a.latitude) * t, a.longitude + (b.longitude - a.longitude) * t);
      if (d < best) best = d;
    }
  }
  return best;
}
