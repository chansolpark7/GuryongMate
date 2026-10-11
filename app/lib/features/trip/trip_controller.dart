import 'dart:async';
import 'dart:math' as math;

import 'package:clock/clock.dart';
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import '../../core/api/api_client.dart';
import '../../core/api/guryong_api.dart';
import '../../core/app_session.dart';
import '../../core/models/plan.dart';
import '../../core/models/safety.dart';
import '../../core/models/trip.dart';
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
class TripController extends ChangeNotifier with WidgetsBindingObserver {
  TripController({
    required this.api,
    required this.plan,
    required this.session,
    LocationSource? source,
    this.timing = const TripTiming(),
  }) : source = source ?? GeolocatorSource();

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

  /// 오늘 이벤트 (오래된 순).
  List<TripEvent> get events => (_events.values.toList()..sort((a, b) => a.id.compareTo(b.id)));

  /// 알림 목록 배지: seen 안 된 level ≥ 1 (24).
  int get unseenCount => _events.values.where((e) => e.level >= 1 && !e.suppressed && e.seenAt == null && !_seenSent.contains(e.id)).length;

  bool get waitingAgent => _events.values.any((e) => e.waitingAgent);

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
  bool _disposed = false;
  bool _polling = false;

  void attachChat(ChatController chat) {
    _chat?.removeListener(_onChat);
    _chat = chat..addListener(_onChat);
  }

  @override
  void dispose() {
    _disposed = true;
    _stopCompanion();
    _chat?.removeListener(_onChat);
    session.removeListener(_onSession);
    super.dispose();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  // ---------- 시작 · 종료 ----------

  /// 계획 화면 진입: state 를 읽고 active 면 동행 모드를 켠다.
  Future<void> load() async {
    session.addListener(_onSession);
    try {
      _setState(await api.tripState(planId));
      stateError = null;
    } catch (e) {
      stateError = e;
    }
    loaded = true;
    _notify();
    if (active) await _startCompanion();
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
    WidgetsBinding.instance.addObserver(this);
    _scheduleState();
    unawaited(loadZones());
    await poll(backlog: true);
    await _startLocation();
  }

  void _stopCompanion() {
    if (_companion) WidgetsBinding.instance.removeObserver(this);
    _companion = false;
    _pollTimer?.cancel();
    _stateTimer?.cancel();
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
    if (!active) return;
    if (demoMode) {
      sender?.stop();
    } else if (sender?.running != true) {
      _startLocation();
    }
    _scheduleState();
    _notify();
  }

  // ---------- 상태 ----------

  void _scheduleState() {
    _stateTimer?.cancel();
    if (!_companion) return;
    _stateTimer = Timer.periodic(_stateEvery, (_) {
      if (!_stateLoading) refreshState(); // 1초 주기에서 응답이 늦으면 쌓지 않는다
    });
  }

  Duration get _stateEvery => demoMode ? (active ? timing.stateDemoTrip : timing.stateDemo) : timing.state;

  /// 사용자 마커가 이전 위치에서 새 위치로 옮겨 가는 시간 (≈ 위치 갱신 주기).
  Duration get markerGlide => demoMode ? timing.stateDemoTrip : timing.markerGlide;

  bool _stateLoading = false;

  Future<void> refreshState() async {
    _stateLoading = true;
    try {
      _setState(await api.tripState(planId));
      if (offline) offline = false;
    } on ApiException catch (e) {
      if (e.isNetwork) offline = true;
    } catch (_) {
    } finally {
      _stateLoading = false;
    }
    _notify();
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
    }
    if (prev != null && (prev.currentItemId != s.currentItemId || prev.currentState != s.currentState)) {
      plan.load(silent: true).catchError((_) {}); // 진행 상태 (progress_version)
      guideTo = null;
    }
    _checkHazardLeft();
    _notify();
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

  /// `GET …/events?after=`. 선제 턴을 기다리는 이벤트가 있으면 그 앞부터 다시 읽어 agent 가 채워졌는지 본다.
  Future<void> poll({bool backlog = false}) async {
    if (_polling) return;
    _polling = true;
    final waiting = _events.values.where((e) => e.waitingAgent).map((e) => e.id);
    final after = waiting.isEmpty ? _lastId : waiting.reduce(math.min) - 1;
    try {
      final list = await api.tripEvents(planId, after: after, limit: 50);
      if (offline) offline = false;
      ingest(list, backlog: backlog);
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
    if (!_companion) return;
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

  /// 오늘 (여행일) 의 Day.
  PlanDay? get today => plan.plan?.dayOf(state?.day ?? plan.day);

  PlanItem? item(int? id) => id == null ? null : plan.plan?.item(id);

  /// 대기 중인 지연 선제 제안 (ETA 칩 Red 판단).
  TripEvent? get pendingDelay =>
      events.where((e) => e.type == 'delay' && e.agentReady && (suggestions.contains(e.id) || _chat?.proposalEntry(e.agent?.proposalId ?? -1)?.state == ProposalState.pending)).lastOrNull;

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
