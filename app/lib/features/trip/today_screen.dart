import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';
import 'package:provider/provider.dart';

import '../../core/format.dart';
import '../../core/models/plan.dart';
import '../../core/models/safety.dart';
import '../../core/models/account.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../../core/widgets/safety_widgets.dart';
import '../guide/guide_prep_screen.dart';
import '../map/map_layers.dart';
import '../planner/plan_controller.dart';
import '../safety/safety_detail_sheet.dart';
import 'location.dart';
import 'trip_controller.dart';

/// 14 Today (동행 홈) · 15 이동 중 · 14-S1 위치 꺼짐/오프라인 · 14-S2 첫 장소 전 + 데모 위치 칩 (Figma 73:2042, 73:2240, 73:7222, 73:7401).
/// 지도(현재 위치 · 오늘 남은 경로 · 다녀온 구간 · 위험 구역) + 패널(지금 · 다음 · 남은 일정).
/// Today 는 "지금 여행" — 핀·일정 행을 눌러도 탭을 옮기지 않고 그 자리 작은 창, Map 은 둘러보기·자세히 (docs/app/today-map-redesign.md).
class TodayScreen extends StatefulWidget {
  const TodayScreen({super.key, required this.onEndTrip});

  /// 패널 맨 아래 "여행 종료" / 모두 마침 [여행 마치기] → 셸이 확인 다이얼로그 → 30.
  final VoidCallback onEndTrip;

  @override
  State<TodayScreen> createState() => _TodayScreenState();
}

/// 경로 [line] 에서 [loc] 에 가장 가까운 점의 인덱스 (loc 이 없으면 0). 안내 카드의 현재 step 과 지나온 구간을 같은 k 로.
int nearestIndex(List<LatLng> line, LatLng? loc) {
  if (loc == null) return 0;
  var k = 0;
  var best = double.infinity;
  for (final (i, p) in line.indexed) {
    final d = distanceM(loc.latitude, loc.longitude, p.latitude, p.longitude);
    if (d < best) {
      best = d;
      k = i;
    }
  }
  return k;
}

/// 15 길 안내선을 k 에서 나눈다: 지나온 부분(회색, 다녀온 구간과 같은 색) · 남은 부분(Primary). 점이 2개 안 되는 쪽은 빈 목록.
@visibleForTesting
({List<LatLng> passed, List<LatLng> ahead}) splitGuide(List<LatLng> line, LatLng? loc) {
  final k = nearestIndex(line, loc);
  return (passed: k > 0 ? line.sublist(0, k + 1) : const <LatLng>[], ahead: k < line.length - 1 ? line.sublist(k) : const <LatLng>[]);
}

class _TodayScreenState extends State<TodayScreen> {
  final _map = MapController();
  bool _ready = false;
  String? _fitted;
  Timer? _tick;

  /// 구간 경로선 "a>b:mode" → (점, 직선 근사)
  final _legs = <String, (List<LatLng>, bool)>{};
  final _legsLoading = <String>{};

  /// 15 길 안내: 현재 위치 → 다음 장소 (hazards avoid, steps). 다음 장소가 바뀌거나 경로에서 50m 벗어나면 다시.
  RouteResult? _guide;
  int? _guideFor;
  LatLng? _guideFrom;
  bool _guideLoading = false;

  /// 안내 카드 [×] 로 숨긴 구간 ("상태:지금 항목:다음 항목"). 다음 구간이 되면 키가 바뀌어 다시 자동으로 켜진다.
  String? _hiddenLeg;

  /// 안내를 시작할 때 카메라를 내 위치 + 목적지로 한 번 맞춘 대상 (그 뒤 사용자가 움직이면 따라가지 않는다).
  int? _guideFitted;

  /// 지도 작은 창을 띄운 항목 (핀 · 남은 일정 행).
  int? _popup;

  /// 위 막대(Pill·데모 칩·안내 카드) — 작은 창이 그 아래로만 뜨게 높이를 잰다.
  final _topBar = GlobalKey();

  /// 패널 높이 (화면 비율, 시트 알림으로 갱신). 바뀌면 버튼·작은 창만 다시 그린다.
  final _extent = ValueNotifier<double>(_half);
  Size _size = Size.zero;

  static const _half = 0.49;

  /// 패널을 이보다 펼치면(0.86 쪽) 버튼을 숨긴다 — 접힘 0.19 · 반 0.49 에서는 보인다.
  static const _fabHideAt = 0.6;

  /// 오른쪽 버튼 칸: 현재 위치(44) · 10 · AI(56), 패널 위 12 (Map 과 같은 순서·간격).
  static const _fabW = 56.0, _fabH = 44.0 + 10 + 56, _fabGap = 12.0;

  /// 14-S2: 지금 위치 → 첫 장소 도보 시간.
  int? _firstWalk;
  int? _firstWalkFor;

  @override
  void initState() {
    super.initState();
    // Pill 의 시각(서버 now + 흐른 시간)과 "n분 남음"을 갱신
    _tick = Timer.periodic(const Duration(seconds: 20), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _tick?.cancel();
    _extent.dispose();
    super.dispose();
  }

  LatLng? _loc(TripController t) => t.state?.location == null ? null : LatLng(t.state!.location!.lat, t.state!.location!.lng);

  List<PlanItem> _items(TripController t) => (t.today?.items ?? const <PlanItem>[]).where((i) => !i.skipped && i.place != null).toList();

  static LatLng _pt(PlanItem i) => LatLng(i.place!.lat, i.place!.lng);

  void _fit(TripController t, List<PlanItem> items) {
    if (!_ready) return;
    final loc = _loc(t);
    final key = '${items.map((i) => i.id).join(',')}:${loc != null}';
    if (key == _fitted) return;
    _fitted = key;
    final pts = [for (final i in items) _pt(i), ?loc];
    if (pts.length < 2) {
      _map.move(pts.isEmpty ? guryongpo : pts.first, 16);
      return;
    }
    _map.fitCamera(CameraFit.coordinates(coordinates: pts, padding: const EdgeInsets.fromLTRB(40, 130, 90, 40), maxZoom: 16.5));
  }

  void _ensureLegs(PlanController c, List<PlanItem> items) {
    for (var i = 1; i < items.length; i++) {
      final a = items[i - 1], b = items[i];
      final mode = b.legMode ?? c.plan?.travelMode ?? 'walk';
      final key = '${a.id}>${b.id}:$mode';
      if (_legs.containsKey(key) || !_legsLoading.add(key)) continue;
      c.routes.leg(mode, _pt(a), _pt(b), hazards: 'report').then((r) {
        _legsLoading.remove(key);
        if (mounted) setState(() => _legs[key] = r == null || r.points.length < 2 ? ([_pt(a), _pt(b)], true) : (r.points, r.estimated));
      });
    }
  }

  /// 안내 대상: 손으로 고른 장소(t.guideTo) 또는 자동 — 이동 중이거나 첫 장소 전에 위치가 있으면 다음 장소 (redesign §2.1).
  /// [×] 로 숨긴 구간이면 자동 대상은 없다.
  PlanItem? _guideTarget(TripController t, List<PlanItem> items, PlanItem? next, bool beforeFirst) {
    final manual = t.guideTo == null ? null : items.where((i) => i.id == t.guideTo && i.status != 'done').firstOrNull;
    if (manual != null) return manual;
    final auto = t.state?.moving == true || (beforeFirst && _loc(t) != null);
    return auto && _hiddenLeg != _legOf(t, next) ? next : null;
  }

  static String _legOf(TripController t, PlanItem? next) => '${t.state?.currentState}:${t.state?.currentItemId}:${next?.id}';

  void _closeGuide(TripController t, PlanItem? next) {
    setState(() => _hiddenLeg = _legOf(t, next));
    if (t.guideTo != null) t.setGuide(null);
  }

  void _startGuide(TripController t, PlanItem item) {
    setState(() {
      _hiddenLeg = null;
      _popup = null;
    });
    t.setGuide(item.id);
  }

  /// 안내선이 처음 그려지면 내 위치 + 목적지가 들어오게 한 번.
  void _fitGuide(PlanItem target, LatLng? loc) {
    if (!_ready || loc == null || _guideFitted == target.id || _guide == null || _guideFor != target.id) return;
    _guideFitted = target.id;
    // 아래 패널(기본 49%)에 가리지 않게 지도 높이의 절반 위에 둘 다
    final h = _map.camera.size.height;
    _map.fitCamera(CameraFit.coordinates(coordinates: [loc, _pt(target)], padding: EdgeInsets.fromLTRB(48, 200, 80, h * 0.52), maxZoom: 17));
  }

  /// 위 막대 아래 y (지난 프레임 크기, 아직 없으면 Pill 높이만).
  double _topBarBottom() {
    final top = MediaQuery.paddingOf(context).top;
    final box = _topBar.currentContext?.findRenderObject() as RenderBox?;
    return top + 11 + (box != null && box.hasSize ? box.size.height : 48) + 8;
  }

  /// 오른쪽 현재 위치(44) + AI(56) 버튼 칸 — 패널 위 가장자리에 붙어 함께 움직인다 (화면 = 지도 크기 기준).
  /// 패널을 크게 펼치거나 위 막대(Pill·안내 카드)에 닿을 만큼 올라가면 숨긴다 (null).
  Rect? _fabBox(Size size, double extent) {
    if (size.isEmpty || extent > _fabHideAt) return null;
    final r = _fabAt(size, extent);
    return r.top < _topBarBottom() ? null : r;
  }

  /// 패널 위 가장자리에 붙인 버튼 칸 (보이든 숨든).
  static Rect _fabAt(Size size, double extent) => Rect.fromLTWH(size.width - 16 - _fabW, size.height * (1 - extent) - _fabGap - _fabH, _fabW, _fabH);

  /// 작은 창이 피할 칸 (버튼이 숨어 있으면 없음).
  List<Rect> _fabRect(Size map) => [?_fabBox(map, _extent.value)];

  /// 오버레이(도착 카드·미리보기·토스트)가 버튼을 피하게 지금 배치를 알린다. 프레임 뒤에만 (오버레이가 다시 그려진다).
  void _publishChrome(TripController t) {
    if (!mounted) return;
    final v = (barBottom: _topBarBottom(), fab: _fabBox(_size, _extent.value));
    if (t.todayChrome.value != v) t.todayChrome.value = v;
  }

  /// 핀·남은 일정 행 → 지도에서 그 핀 + 작은 창 (탭 이동 없음, redesign §2.2).
  void _focus(PlanItem i) {
    setState(() => _popup = i.id);
    // 핀이 아래 패널 위쪽에 오게 (작은 창은 핀 위로 뜬다)
    if (_ready) _map.move(_pt(i), _map.camera.zoom < 15.5 ? 15.5 : _map.camera.zoom, offset: Offset(0, -_map.camera.size.height * 0.12));
  }

  void _ensureGuide(TripController t, PlanItem? next) {
    final loc = _loc(t);
    if (next == null || loc == null || _guideLoading) return;
    final off = _guideFrom == null || _guide == null || _offRoute(loc, _guide!.points) > 50;
    if (_guideFor == next.id && !off) return;
    _guideLoading = true;
    _guideFor = next.id;
    _guideFrom = loc;
    final mode = next.legMode ?? t.plan.plan?.travelMode ?? 'walk';
    t.api.route(mode, [(loc.latitude, loc.longitude), (next.place!.lat, next.place!.lng)], hazards: 'avoid', steps: true).then((r) {
      if (mounted) setState(() => _guide = r);
    }).catchError((_) {
      if (mounted) setState(() => _guide = null);
    }).whenComplete(() => _guideLoading = false);
  }

  static double _offRoute(LatLng p, List<LatLng> line) {
    var best = double.infinity;
    for (final q in line) {
      final d = distanceM(p.latitude, p.longitude, q.latitude, q.longitude);
      if (d < best) best = d;
    }
    return best;
  }

  void _ensureFirstWalk(TripController t, PlanItem first) {
    final loc = _loc(t);
    if (loc == null || _firstWalkFor == first.id) return;
    _firstWalkFor = first.id;
    t.plan.routes.leg('walk', loc, _pt(first)).then((r) {
      if (mounted && r != null) setState(() => _firstWalk = r.minutes);
    });
  }

  @override
  Widget build(BuildContext context) {
    final c = context.watch<PlanController>();
    final t = context.watch<TripController>();
    final plan = c.plan;
    if (plan == null) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    final s = t.state;
    final items = _items(t);
    final done = items.where((i) => i.status == 'done').toList();
    final current = s != null && s.visiting ? t.item(s.currentItemId) : null;
    final next = t.item(s?.nextItemId) ?? items.where((i) => i.status == 'planned').firstOrNull;
    final remaining = items.where((i) => i.status == 'planned').toList();
    final moving = s?.moving == true;
    final started = items.any((i) => i.status != 'planned');
    final allDone = s != null && s.idle && remaining.isEmpty && items.isNotEmpty;
    final beforeFirst = s != null && s.idle && !started && next != null;
    if (_ready) afterFrame(() => mounted ? _fit(t, items) : null);
    _ensureLegs(c, items.where((i) => i.status != 'done' || i == done.lastOrNull).toList());
    final target = _guideTarget(t, items, next, beforeFirst);
    final showGuide = target != null;
    final loc = t.locationOff ? null : _loc(t);
    if (target != null) {
      _ensureGuide(t, target);
      afterFrame(() => mounted ? _fitGuide(target, loc) : null);
    } else if (_guideFitted != null) {
      _guideFitted = null;
      _fitted = null; // 안내가 끝나면(도착·[×]) 오늘 경로 전체로 한 번 되돌린다
    }
    if (beforeFirst) _ensureFirstWalk(t, next);
    final popup = _popup == null ? null : items.where((i) => i.id == _popup).firstOrNull;
    final top = MediaQuery.paddingOf(context).top;
    afterFrame(() => _publishChrome(t));

    return Scaffold(
      body: LayoutBuilder(builder: (context, box) {
        _size = box.biggest;
        return Stack(children: [
        Positioned.fill(child: _buildMap(c, t, items, done, current, target, popup)),
        Positioned(
          left: 16,
          right: 16,
          top: top + 11,
          child: Column(key: _topBar, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Expanded(child: TripPill(title: plan.title, time: s?.now == null ? null : clockText(t.serverNow()))),
              const SizedBox(width: 12),
              _Bell(count: t.unseenCount, onTap: () => t.onOpenInbox?.call()),
            ]),
            if (t.demoMode) ...[const SizedBox(height: 8), const _DemoChip()],
            if (t.locationOff) ...[const SizedBox(height: 10), _LocationOffBanner(onEnable: t.requestAccess)],
            if (t.offline) ...[const SizedBox(height: 10), const _OfflineStrip()],
            if (showGuide && !t.offline) ...[
              const SizedBox(height: 10),
              _GuideCard(
                route: _guideFor == target.id ? _guide : null,
                next: target,
                eta: target.id == s?.nextItemId ? s?.nextEta : null,
                loc: loc,
                loading: _guideLoading,
                onClose: () => _closeGuide(t, next),
              ),
            ],
          ]),
        ),
        // 현재 위치 · AI 버튼: 패널 위 가장자리에 붙어 함께 움직이고, 크게 펼치면 투명 + 눌리지 않음 (Map 과 같은 방식, 2026-10-12)
        ValueListenableBuilder<double>(
          valueListenable: _extent,
          builder: (context, extent, _) {
            final fab = _fabBox(box.biggest, extent);
            final at = fab ?? _fabAt(box.biggest, extent.clamp(0, _fabHideAt)); // 숨을 때는 사라지기 시작한 자리에서 흐려진다
            return Positioned(
              left: at.left,
              top: at.top,
              width: _fabW,
              child: IgnorePointer(
                ignoring: fab == null,
                child: AnimatedOpacity(
                  opacity: fab == null ? 0 : 1,
                  duration: const Duration(milliseconds: 150),
                  child: Column(key: const ValueKey('today-fab'), children: [
                    _RoundButton(
                      icon: Icons.my_location,
                      tooltip: '현재 위치',
                      onTap: () {
                        final l = _loc(t);
                        if (l != null && _ready) _map.move(l, 16.5);
                      },
                    ),
                    const SizedBox(height: 10),
                    AiFab(onTap: t.openAiFromFab, hasSuggestion: t.hasSuggestion),
                  ]),
                ),
              ),
            );
          },
        ),
        NotificationListener<DraggableScrollableNotification>(
          onNotification: (n) {
            _extent.value = n.extent;
            afterFrame(() => _publishChrome(t));
            // 패널을 펼치면 노트 목록을 가볍게 다시 읽는다 (10초 안에 읽었으면 건너뜀)
            if (n.extent > 0.6) t.loadGuideNotes(minGap: const Duration(seconds: 10));
            return false;
          },
          child: DraggableScrollableSheet(
            initialChildSize: _half,
            minChildSize: 0.19,
            maxChildSize: 0.86,
            snap: true,
            snapSizes: const [0.19, _half, 0.86],
            builder: (context, scroll) => Container(
              decoration: const BoxDecoration(
                color: AppColors.surface,
                borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
                boxShadow: [BoxShadow(color: Color(0x14111827), blurRadius: 16, offset: Offset(0, -2))],
              ),
              child: ListView(
                controller: scroll,
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
                children: [
                  const Grabber(),
                  if (s == null && t.stateError != null)
                    _StateError(onRetry: t.refreshState)
                  else if (s == null)
                    const _PanelSkeleton()
                  else if (allDone)
                    _AllDone(onFinish: widget.onEndTrip)
                  else if (beforeFirst)
                    _BeforeFirst(
                      first: next,
                      now: t.serverNow(),
                      walk: _firstWalk,
                      onPreview: () => t.onOpenGuide?.call(next.placeId),
                    )
                  else if (moving && next != null)
                    _MovingSection(trip: t, from: t.item(s.currentItemId), next: next, onArrived: null)
                  else if (current != null)
                    _NowSection(
                      trip: t,
                      item: current,
                      safety: c.safety?.visit(current.id),
                      stale: t.locationOff,
                      onListen: () => t.onOpenGuide?.call(current.placeId),
                      onBadge: () => showSafetyDetailSheet(context, c, current),
                    ),
                  if (s != null && !allDone && !beforeFirst && !moving && next != null) ...[
                    const Divider(height: 28, color: AppColors.line),
                    _NextSection(trip: t, next: next, number: items.indexOf(next) + 1, guiding: target?.id == next.id, onGuide: () => _startGuide(t, next)),
                  ],
                  if (s != null && !allDone) ...[
                    const Divider(height: 28, color: AppColors.line),
                    // 여행 시작~첫 도착 전에도 목록을 보인다 (한 줄 요약은 머리에, redesign §2.3 — Figma 14-S2 와 다름)
                    _RemainingSection(
                      items: beforeFirst ? items : remaining.where((i) => i.id != next?.id || moving).toList(),
                      total: beforeFirst,
                      end: (t.today?.endAt ?? items.lastOrNull?.endAt),
                      safety: c.safety,
                      selected: popup?.id,
                      onTap: _focus,
                    ),
                    // 가이드 노트 n/m 준비됨 → 32 (여행 중에도 다시 준비할 수 있게, 2026-10-11)
                    if (t.guideNotes != null && items.isNotEmpty) ...[
                      const SizedBox(height: 6),
                      _GuideNotesLine(notes: t.guideNotes!, placeIds: {for (final i in items) i.placeId}.toList(), onTap: () => _openGuidePrep(t)),
                    ],
                  ],
                  const SizedBox(height: 12),
                  Center(
                    child: TextButton(
                      onPressed: t.busy ? null : widget.onEndTrip,
                      child: Text('여행 종료', style: AppText.meta.copyWith(color: AppColors.text3, decoration: TextDecoration.underline)),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ]);
      }),
    );
  }

  Future<void> _openGuidePrep(TripController t) async {
    t.pushed(true);
    await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => GuidePrepScreen(plan: t.plan)));
    t.pushed(false);
    unawaited(t.loadGuideNotes());
  }

  Widget _buildMap(PlanController c, TripController t, List<PlanItem> items, List<PlanItem> done, PlanItem? current, PlanItem? target, PlanItem? popup) {
    final loc = _loc(t), acc = t.state?.location?.accuracy;
    final lines = <Polyline>[];
    final guide = target != null && _guideFor == target.id && _guide != null && _guide!.points.length > 1 ? _guide : null;
    for (var i = 1; i < items.length; i++) {
      final a = items[i - 1], b = items[i];
      // 안내 중에는 안내 대상으로 가는 구간선 대신 안내선만 (같은 길에 두 줄이 겹치지 않게)
      if (guide != null && b.id == target?.id) continue;
      final mode = b.legMode ?? c.plan?.travelMode ?? 'walk';
      final leg = _legs['${a.id}>${b.id}:$mode'];
      final walked = b.status != 'planned';
      final pts = leg?.$1 ?? [_pt(a), _pt(b)];
      final dotted = mode == 'car' || (leg?.$2 ?? true);
      // 안내 중이면 나머지 구간은 옅게 — 안내선이 눈에 띄게
      lines.add(Polyline(
        points: pts,
        strokeWidth: walked ? 4 : 5,
        color: walked ? const Color(0xFFAEB6C1) : (guide != null ? AppColors.primary.withValues(alpha: 0.3) : AppColors.primary),
        borderColor: Colors.white,
        borderStrokeWidth: walked || guide != null ? 0 : 2,
        pattern: dotted && !walked ? StrokePattern.dotted(spacingFactor: 2) : const StrokePattern.solid(),
      ));
    }
    if (guide != null) {
      final (:passed, :ahead) = splitGuide(guide.points, loc);
      if (passed.length > 1) lines.add(Polyline(points: passed, strokeWidth: 4, color: const Color(0xFFAEB6C1)));
      if (ahead.length > 1) lines.add(Polyline(points: ahead, strokeWidth: 7, color: AppColors.primary, borderColor: Colors.white, borderStrokeWidth: 3));
    }
    return FlutterMap(
      mapController: _map,
      options: MapOptions(
        initialCenter: loc ?? (items.isEmpty ? guryongpo : _pt(items.first)),
        initialZoom: 15.5,
        onMapReady: () {
          _ready = true;
          deferOnWeb(() => mounted ? _fit(t, items) : null);
        },
        onTap: (_, _) {
          if (_popup != null) setState(() => _popup = null);
        },
      ),
      children: [
        osmTiles(),
        // 위험 구역: 라벨은 지금 경고 중인 구역만 (23 지도 — 그 구역 Red 강조)
        ZoneLayer(zones: t.zones, labels: false),
        if (t.hazard != null) ZoneLayer(zones: t.zones.where((z) => t.hazard!.zones.any((h) => h.id == z.id)).toList()),
        PolylineLayer(polylines: lines),
        MarkerLayer(markers: [
          for (final (n, i) in items.indexed)
            Marker(
              point: _pt(i),
              width: i.id == current?.id ? 40 : 32,
              height: i.id == current?.id ? 40 : 32,
              child: GestureDetector(
                key: ValueKey('today-pin-${i.id}'),
                onTap: () => _focus(i),
                child: i.status == 'done'
                    ? const MapDot(color: Color(0xFFAEB6C1), icon: Icons.check, size: 28)
                    : MapDot(color: AppColors.primary, label: '${n + 1}', size: i.id == current?.id || i.id == popup?.id ? 36 : 28),
              ),
            ),
        ]),
        // 사용자 위치는 장소 Pin 위에 (새 위치로 부드럽게 옮겨 간다)
        if (loc != null) UserLocationLayer(point: loc, accuracy: acc, duration: t.markerGlide, dotKey: const ValueKey('today-location')),
        if (popup != null)
          // 버튼이 패널을 따라 움직이므로 패널 높이가 바뀌면 자리를 다시 고른다
          ValueListenableBuilder<double>(
            valueListenable: _extent,
            builder: (context, _, child) => _PopupLayer(
              point: _pt(popup),
              // 위 막대(Pill·안내 카드) 아래 · 오른쪽 AI·현재 위치 버튼 밖으로 (2026-10-11)
              minTop: _topBarBottom(),
              avoid: _fabRect,
              child: child!,
            ),
            child: _PlacePopup(
              item: popup,
              number: items.indexOf(popup) + 1,
              onListen: () {
                setState(() => _popup = null);
                t.onOpenGuide?.call(popup.placeId);
              },
              onGuide: popup.status == 'done' ? null : () => _startGuide(t, popup),
              onDetail: () {
                setState(() => _popup = null);
                c.showDetailOnMap(popup.id);
              },
              onClose: () => setState(() => _popup = null),
            ),
          ),
      ],
    );
  }
}

/// "● 동행 중 · {계획명}  HH:MM" (Trip Pill). 시각은 서버의 지금 (가상 시각이면 그 시각, 33 에서만 "가상" 표시).
class TripPill extends StatelessWidget {
  const TripPill({super.key, required this.title, required this.time});
  final String title;
  final String? time;

  @override
  Widget build(BuildContext context) => Container(
        height: 48,
        padding: const EdgeInsets.symmetric(horizontal: 16),
        decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(24), boxShadow: AppShadow.floating),
        child: Row(children: [
          Container(width: 8, height: 8, decoration: const BoxDecoration(color: AppColors.success, shape: BoxShape.circle)),
          const SizedBox(width: 8),
          Text('동행 중', style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
          Expanded(child: Text(' · $title', style: AppText.label.copyWith(color: AppColors.text3), overflow: TextOverflow.ellipsis)),
          if (time != null) Text(time!, key: const ValueKey('trip-pill-time'), style: AppText.meta.copyWith(color: AppColors.text)),
        ]),
      );
}

class _Bell extends StatelessWidget {
  const _Bell({required this.count, required this.onTap});
  final int count;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Stack(clipBehavior: Clip.none, children: [
        _RoundButton(icon: Icons.notifications_none, tooltip: '알림', onTap: onTap),
        if (count > 0)
          Positioned(
            right: -2,
            top: -2,
            child: Container(
              key: const ValueKey('bell-badge'),
              constraints: const BoxConstraints(minWidth: 18),
              height: 18,
              padding: const EdgeInsets.symmetric(horizontal: 4),
              alignment: Alignment.center,
              decoration: BoxDecoration(color: AppColors.danger, borderRadius: BorderRadius.circular(9), border: Border.all(color: Colors.white, width: 1.5)),
              child: Text('$count', style: AppText.caption.copyWith(color: Colors.white, fontSize: 10, fontWeight: FontWeight.w700)),
            ),
          ),
      ]);
}

class _RoundButton extends StatelessWidget {
  const _RoundButton({required this.icon, required this.tooltip, required this.onTap});
  final IconData icon;
  final String tooltip;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Tooltip(
        message: tooltip,
        // Map 의 버튼과 같은 모양 (그림자 · 아이콘 20)
        child: Container(
          decoration: const BoxDecoration(shape: BoxShape.circle, boxShadow: AppShadow.floating),
          child: Material(
            color: AppColors.surface,
            shape: const CircleBorder(),
            child: InkWell(customBorder: const CircleBorder(), onTap: onTap, child: SizedBox(width: 44, height: 44, child: Icon(icon, size: 20, color: AppColors.text))),
          ),
        ),
      );
}

class _DemoChip extends StatelessWidget {
  const _DemoChip();

  @override
  Widget build(BuildContext context) => Container(
        key: const ValueKey('demo-location-chip'),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(color: AppColors.dark, borderRadius: BorderRadius.circular(8)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.my_location, size: 12, color: Colors.white),
          const SizedBox(width: 4),
          Text('데모 위치 · 서버 위치 표시', style: AppText.caption.copyWith(color: Colors.white, fontWeight: FontWeight.w500)),
        ]),
      );
}

class _LocationOffBanner extends StatelessWidget {
  const _LocationOffBanner({required this.onEnable});
  final Future<LocationAccess> Function() onEnable;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(14, 10, 6, 10),
        decoration: BoxDecoration(color: AppColors.warningSoft, borderRadius: BorderRadius.circular(14), border: Border.all(color: AppColors.warningLine)),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Padding(padding: EdgeInsets.only(top: 2), child: Icon(Icons.location_on_outlined, size: 18, color: AppColors.warning)),
          const SizedBox(width: 8),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('위치가 꺼져 있어요', style: AppText.label.copyWith(fontWeight: FontWeight.w700, color: AppColors.text)),
              Text('도착 해설·위험 구역 알림이 동작하지 않아요', style: AppText.caption.copyWith(color: AppColors.warning)),
            ]),
          ),
          TextButton(onPressed: onEnable, child: Text('켜기', style: AppText.label.copyWith(color: AppColors.primary, fontWeight: FontWeight.w700))),
        ]),
      );
}

class _OfflineStrip extends StatelessWidget {
  const _OfflineStrip();

  @override
  Widget build(BuildContext context) => Container(
        key: const ValueKey('offline-strip'),
        height: 40,
        padding: const EdgeInsets.symmetric(horizontal: 14),
        decoration: BoxDecoration(color: AppColors.dark, borderRadius: BorderRadius.circular(12)),
        child: Row(children: [
          const Icon(Icons.wifi_off, size: 16, color: Colors.white),
          const SizedBox(width: 8),
          Expanded(child: Text('오프라인 · 위치는 모아 뒀다가 연결되면 보낼게요', style: AppText.meta.copyWith(color: Colors.white), overflow: TextOverflow.ellipsis)),
        ]),
      );
}

/// 15 길 안내 카드: 현재 위치에서 가장 가까운 경로 점 k → begin_index ≤ k 인 마지막 안내의 다음 안내.
class _GuideCard extends StatelessWidget {
  const _GuideCard({required this.route, required this.next, required this.eta, required this.loc, required this.loading, required this.onClose});
  final RouteResult? route;
  final PlanItem next;
  final String? eta;
  final LatLng? loc;
  final bool loading;

  /// [×]: 이번 구간 동안만 숨김 (다음 구간에서 다시 자동).
  final VoidCallback onClose;

  static RouteStep? currentStep(RouteResult r, LatLng? loc) {
    if (r.steps.isEmpty) return null;
    final k = nearestIndex(r.points, loc);
    final passed = r.steps.lastIndexWhere((s) => s.beginIndex <= k);
    final idx = (passed + 1).clamp(0, r.steps.length - 1);
    return r.steps[idx];
  }

  static IconData turnIcon(String type) => switch (type) {
        'left' || 'sharp_left' => Icons.turn_left,
        'slight_left' || 'keep_left' => Icons.turn_slight_left,
        'right' || 'sharp_right' => Icons.turn_right,
        'slight_right' || 'keep_right' => Icons.turn_slight_right,
        'arrive' => Icons.flag_outlined,
        'uturn' => Icons.u_turn_left,
        _ => Icons.straight,
      };

  @override
  Widget build(BuildContext context) {
    final r = route;
    final mode = next.legMode == 'car' ? '차량' : '도보';
    final step = r == null ? null : currentStep(r, loc);
    final car = next.legMode == 'car';
    final title = loc == null
        ? '위치를 켜면 길을 안내해요'
        : r == null
        ? (loading ? '경로를 찾고 있어요' : '${next.title} 방향')
        : car
            ? '차로 ${r.minutes}분'
            : step?.instruction ?? '${next.title} 방향 · 약 ${r.minutes}분';
    final sub = '${next.title}까지 $mode ${r?.minutes ?? next.travelMinutes ?? '-'}분${eta == null ? '' : ' · $eta 도착 예상'}';
    final detour = r?.hazards?.avoided.isNotEmpty == true && (r?.hazards?.detourMinutes ?? 0) > 0 ? r!.hazards!.detourMinutes : null;
    return Container(
      key: const ValueKey('guide-card'),
      padding: const EdgeInsets.fromLTRB(12, 12, 6, 12),
      decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(16), boxShadow: AppShadow.floating),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(color: AppColors.primary, borderRadius: BorderRadius.circular(10)),
            child: Icon(loc == null ? Icons.location_disabled : car ? Icons.directions_car : turnIcon(step?.type ?? ''), color: Colors.white),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: AppText.label.copyWith(fontSize: 15, fontWeight: FontWeight.w700), maxLines: 1, overflow: TextOverflow.ellipsis),
              Text(sub, style: AppText.meta, maxLines: 1, overflow: TextOverflow.ellipsis),
            ]),
          ),
          SizedBox(
            width: 32,
            height: 32,
            child: IconButton(
              key: const ValueKey('guide-close'),
              tooltip: '안내 닫기',
              padding: EdgeInsets.zero,
              iconSize: 18,
              onPressed: onClose,
              icon: const Icon(Icons.close, color: AppColors.text3),
            ),
          ),
        ]),
        if (detour != null) ...[
          const SizedBox(height: 6),
          Text('위험 구역을 피해 $detour분 더 걸려요', style: AppText.caption.copyWith(color: AppColors.warning, fontWeight: FontWeight.w700)),
        ],
      ]),
    );
  }
}

int _nowMin(DateTime now) => now.hour * 60 + now.minute;

/// ETA 칩 (Components_Companion): 제시간 / n분 여유 (Green) · n분 늦음 (Amber) · HH:MM 종료 · 규칙 넘음 / n분 늦음 (Red, 대기 중인 지연 제안).
class EtaChip extends StatelessWidget {
  const EtaChip({super.key, required this.delay, this.violation});
  final int? delay;
  final String? violation;

  @override
  Widget build(BuildContext context) {
    final d = delay ?? 0;
    if (violation != null) return InfoChip(label: violation!, icon: Icons.schedule, fg: AppColors.danger, bg: AppColors.dangerSoft);
    if (d >= 1) return InfoChip(label: '$d분 늦음', icon: Icons.schedule, fg: AppColors.warning, bg: AppColors.warningSoft);
    return InfoChip.success(d < 0 ? '${-d}분 여유' : '제시간', icon: Icons.schedule);
  }

  /// 대기 중인 지연 제안이 있으면 Red. 위반이면 "18:07 종료 · 규칙 넘음" (하루 초과·종료 규칙의 끝나는 시각) 또는 위반 종류,
  /// 일정 밀림(behind_schedule)은 규칙 위반이 아니라 "n분 늦음" (지금 지연). 이벤트 문구의 시각은 읽지 않는다 —
  /// 밀림 문구 "구룡포항 도착이 13:34 예정에서 …" 의 첫 시각은 다음 장소 계획 시작이다 (audit-2026-10-12 B1).
  static String? violationLabel(TripController t) {
    final e = t.pendingDelay;
    if (e == null) return null;
    if (e.payload['reason'] == 'behind_schedule') {
      final d = t.state?.delayMinutes;
      return d != null && d >= 1 ? '$d분 늦음' : null;
    }
    final vs = [for (final v in (e.payload['new_violations'] as List?) ?? const []) if (v is Map) v];
    for (final v in vs.where((v) => v['code'] == 'day_overflow' || v['code'] == 'end_before')) {
      final m = RegExp(r'(\d{1,2}:\d{2}) ?에 끝나').firstMatch('${v['message'] ?? ''}');
      if (m != null) return '${m.group(1)} 종료 · 규칙 넘음';
    }
    return switch (vs.firstOrNull?['code']) {
      'fixed_time_missed' => '고정 시각 놓침',
      'outside_open_hours' => '운영시간 밖',
      'closed_day' => '휴무일',
      _ => '규칙 넘음',
    };
  }
}

class _NowSection extends StatelessWidget {
  const _NowSection({required this.trip, required this.item, required this.safety, required this.stale, required this.onListen, required this.onBadge});
  final TripController trip;
  final PlanItem item;
  final SafetyItem? safety;
  final bool stale;
  final VoidCallback onListen;
  final VoidCallback onBadge;

  @override
  Widget build(BuildContext context) {
    final since = trip.state?.currentSince ?? item.arrivedAt;
    final now = _nowMin(trip.serverNow());
    final end = toMinutes(item.endAt);
    final arrived = toMinutes(since);
    // 실제 도착 + 체류 = 예정 끝 (진행 기준). 없으면 계획 end_at
    final until = arrived != null ? arrived + item.stayMinutes : end;
    final left = until == null ? null : until - now;
    final over = left != null && left < 0;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text('지금${since == null ? '' : ' · $since 도착'}${stale ? ' (마지막 확인)' : ''}', style: AppText.caption.copyWith(fontWeight: FontWeight.w700)),
      const SizedBox(height: 4),
      Row(children: [
        Expanded(child: Text(item.title, style: AppText.headline.copyWith(fontSize: 18))),
        if (safety != null) RiskBadge(item: safety!, onTap: onBadge, compact: true),
      ]),
      const SizedBox(height: 2),
      Text(
        over
            ? '예정보다 ${-left}분 더 머무는 중'
            : '머무는 중${until == null ? '' : ' · ${fromMinutes(until)}까지 예정'}${left == null ? '' : ' ($left분 남음)'}',
        style: AppText.meta.copyWith(color: over ? AppColors.warning : AppColors.text2, fontWeight: over ? FontWeight.w700 : FontWeight.w400),
      ),
      // 큰 [길 안내] 는 없앴다 — 머무는 중 안내는 다음 일정 🧭 · 지도 작은 창에서 (redesign §2.1)
      const SizedBox(height: 14),
      AppButton(label: '이야기 듣기', icon: Icons.auto_awesome, kind: ButtonKind.aiSoft, height: 48, onPressed: onListen),
    ]);
  }
}

class _NextSection extends StatelessWidget {
  const _NextSection({required this.trip, required this.next, required this.number, required this.guiding, required this.onGuide});
  final TripController trip;
  final PlanItem next;
  final int number;
  final bool guiding;

  /// 🧭 머무는 중에도 다음 장소 길 안내를 미리 (redesign §2.1 수동 켜기).
  final VoidCallback onGuide;

  @override
  Widget build(BuildContext context) {
    final s = trip.state;
    final planned = s?.nextItemId == next.id ? (s?.nextPlannedStart ?? next.startAt) : next.startAt;
    final mode = next.legMode == 'car' ? '차량' : '도보';
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text('다음', style: AppText.caption.copyWith(fontWeight: FontWeight.w700)),
      const SizedBox(height: 6),
      Row(children: [
        MapDot(color: AppColors.primary, label: '$number', size: 28),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(next.title, style: AppText.section),
            Text('${planned ?? '--:--'} 예정${next.travelMinutes == null ? '' : ' · $mode ${next.travelMinutes}분'}', style: AppText.meta),
          ]),
        ),
        IconButton(
          key: const ValueKey('next-guide'),
          tooltip: '길 안내',
          visualDensity: VisualDensity.compact,
          onPressed: guiding ? null : onGuide,
          icon: Icon(Icons.explore_outlined, size: 22, color: guiding ? AppColors.text3 : AppColors.primary),
        ),
        EtaChip(key: const ValueKey('eta-chip'), delay: s?.delayMinutes, violation: EtaChip.violationLabel(trip)),
      ]),
    ]);
  }
}

class _MovingSection extends StatelessWidget {
  const _MovingSection({required this.trip, required this.from, required this.next, required this.onArrived});
  final TripController trip;
  final PlanItem? from;
  final PlanItem next;
  final VoidCallback? onArrived;

  @override
  Widget build(BuildContext context) {
    final s = trip.state!;
    final left = s.currentSince ?? from?.doneAt;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text('이동 중${left == null ? '' : ' · $left 출발'}', style: AppText.caption.copyWith(fontWeight: FontWeight.w700)),
      const SizedBox(height: 4),
      Row(children: [
        Expanded(child: Text('${next.title}으로 가는 중', style: AppText.headline.copyWith(fontSize: 18))),
        EtaChip(key: const ValueKey('eta-chip'), delay: s.delayMinutes, violation: EtaChip.violationLabel(trip)),
      ]),
      // [경로 전체] 없음 — Map 은 하단 탭 · 지도 작은 창 [자세히 ›] 로 (redesign §2.3, 2026-10-11)
      Text('${s.nextPlannedStart ?? next.startAt ?? '--:--'} 예정${s.nextEta == null ? '' : ' → ${s.nextEta} 도착 예상'}', style: AppText.meta),
    ]);
  }
}

class _RemainingSection extends StatelessWidget {
  const _RemainingSection({
    required this.items,
    required this.total,
    required this.end,
    required this.safety,
    required this.selected,
    required this.onTap,
  });
  final List<PlanItem> items;

  /// 첫 장소 전: 머리가 "오늘 일정 n곳" (목록은 처음부터 모두).
  final bool total;
  final String? end;
  final SafetyCheck? safety;
  final int? selected;

  /// 행 → 지도에서 그 핀 + 작은 창 (탭 이동 없음).
  final ValueChanged<PlanItem> onTap;

  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        // 머리는 요약 한 줄만 — [전체 지도]·[전체 일정 ›] 는 바로 아래 하단 탭(Map·Planner)과 같아 없앴다 (2026-10-11)
        Text(
          !total && items.isEmpty ? '다음이 마지막 일정이에요${end == null ? '' : ' · $end 종료 예정'}' : '${total ? '오늘 일정' : '남은 일정'} ${items.length}곳${end == null ? '' : ' · $end 종료 예정'}',
          style: AppText.caption.copyWith(fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 4),
        for (final i in items)
          Material(
            color: i.id == selected ? AppColors.primarySoft : Colors.transparent,
            borderRadius: BorderRadius.circular(10),
            child: InkWell(
              key: ValueKey('remaining-${i.id}'),
              borderRadius: BorderRadius.circular(10),
              onTap: () => onTap(i),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 7, horizontal: 4),
                child: Row(children: [
                  SizedBox(width: 50, child: Text(i.startAt ?? '', style: AppText.meta.copyWith(fontWeight: FontWeight.w700, color: AppColors.text2))),
                  Expanded(child: Text(i.title, style: AppText.label, overflow: TextOverflow.ellipsis)),
                  if (safety?.visit(i.id) != null) RiskBadge(item: safety!.visit(i.id)!, compact: true),
                ]),
              ),
            ),
          ),
      ]);
}

/// Today 남은 일정 아래 한 줄: "가이드 노트 n/m 준비됨 · k곳 다시 준비 필요 ›" / "가이드 노트 준비 중 n/m ›" → 32.
/// stale 노트도 현장에서 그대로 쓰므로 준비됨에 센다 (docent §3.2).
class _GuideNotesLine extends StatelessWidget {
  const _GuideNotesLine({required this.notes, required this.placeIds, required this.onTap});
  final Map<String, GuideNoteSummary> notes;
  final List<String> placeIds;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final st = [for (final p in placeIds) notes[p]?.status];
    final ready = st.where((s) => s == 'ready' || s == 'stale').length;
    final stale = st.where((s) => s == 'stale').length;
    final preparing = st.any((s) => s == 'pending' || s == 'preparing');
    final m = placeIds.length;
    final base = AppText.meta.copyWith(color: AppColors.text2, fontWeight: FontWeight.w500);
    return Material(
      color: Colors.transparent,
      borderRadius: BorderRadius.circular(10),
      child: InkWell(
        key: const ValueKey('today-guide-line'),
        borderRadius: BorderRadius.circular(10),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 4),
          child: Row(children: [
            Icon(preparing ? Icons.autorenew : Icons.description_outlined, size: 16, color: AppColors.ai),
            const SizedBox(width: 6),
            Expanded(
              child: Text.rich(
                TextSpan(children: [
                  TextSpan(text: preparing ? '가이드 노트 준비 중 $ready/$m' : '가이드 노트 $ready/$m 준비됨'),
                  if (stale > 0 && !preparing) TextSpan(text: ' · $stale곳 다시 준비 필요', style: base.copyWith(color: AppColors.warning, fontWeight: FontWeight.w700)),
                ]),
                style: base,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            const Icon(Icons.chevron_right, size: 18, color: AppColors.text3),
          ]),
        ),
      ),
    );
  }
}

class _BeforeFirst extends StatelessWidget {
  const _BeforeFirst({required this.first, required this.now, required this.walk, required this.onPreview});
  final PlanItem first;
  final DateTime now;
  final int? walk;
  final VoidCallback onPreview;

  @override
  Widget build(BuildContext context) {
    final start = toMinutes(first.startAt);
    final left = start == null ? null : start - _nowMin(now);
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text(left == null || left <= 0 ? '첫 일정 시간이에요' : '첫 일정까지 ${durationKo(left)}', style: AppText.caption.copyWith(fontWeight: FontWeight.w700)),
      const SizedBox(height: 4),
      Text(first.title, style: AppText.headline.copyWith(fontSize: 18)),
      Text('${first.startAt ?? ''} 시작${walk == null ? '' : ' · 지금 위치에서 도보 $walk분'}', style: AppText.meta),
      // 길 안내는 위치가 있으면 자동으로 켜진다 (큰 [길 안내] 없음, redesign §2.1)
      const SizedBox(height: 14),
      AppButton(label: '가이드 미리 보기', icon: Icons.auto_awesome, kind: ButtonKind.aiSoft, height: 48, onPressed: onPreview),
    ]);
  }
}

/// 지도 핀 위 작은 창: 번호 · 이름 · 예정 시각 · 상태 + [설명 듣기] [길 안내] + [자세히] (redesign §2.2).
class _PlacePopup extends StatelessWidget {
  const _PlacePopup({required this.item, required this.number, required this.onListen, required this.onGuide, required this.onDetail, required this.onClose});
  final PlanItem item;
  final int number;
  final VoidCallback onListen;
  final VoidCallback? onGuide;
  final VoidCallback onDetail;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    final (status, color) = switch (item.status) {
      'visiting' => ('머무는 중', AppColors.success),
      'done' => ('완료', AppColors.text3),
      _ => ('예정', AppColors.primary),
    };
    return Container(
          key: const ValueKey('today-place-popup'),
          padding: const EdgeInsets.fromLTRB(12, 10, 4, 6),
          decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(16), boxShadow: AppShadow.floating),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Row(children: [
              MapDot(color: item.status == 'done' ? const Color(0xFFAEB6C1) : AppColors.primary, label: '$number', size: 24),
              const SizedBox(width: 8),
              Expanded(child: Text(item.title, style: AppText.section.copyWith(fontSize: 15), maxLines: 1, overflow: TextOverflow.ellipsis)),
              SizedBox(
                width: 28,
                height: 28,
                child: IconButton(tooltip: '닫기', padding: EdgeInsets.zero, iconSize: 16, onPressed: onClose, icon: const Icon(Icons.close, color: AppColors.text3)),
              ),
            ]),
            const SizedBox(height: 2),
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: Text.rich(
                TextSpan(style: AppText.meta, children: [
                  TextSpan(text: '${item.startAt ?? '--:--'}${item.endAt == null ? '' : ' – ${item.endAt}'} · '),
                  TextSpan(text: status, style: TextStyle(color: color, fontWeight: FontWeight.w700)),
                ]),
              ),
            ),
            const SizedBox(height: 8),
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: Row(children: [
                Expanded(child: AppButton(label: '설명 듣기', icon: Icons.auto_awesome, kind: ButtonKind.aiSoft, height: 36, onPressed: onListen)),
                const SizedBox(width: 6),
                Expanded(child: AppButton(label: '길 안내', icon: Icons.near_me_outlined, kind: ButtonKind.secondary, height: 36, onPressed: onGuide)),
              ]),
            ),
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                onPressed: onDetail,
                style: TextButton.styleFrom(minimumSize: const Size(0, 28), padding: const EdgeInsets.symmetric(horizontal: 8), tapTargetSize: MaterialTapTargetSize.shrinkWrap),
                child: Text('자세히 ›', style: AppText.caption.copyWith(color: AppColors.text2, fontWeight: FontWeight.w700)),
              ),
            ),
          ]),
        );
  }
}

/// 작은 창을 핀 위에 — 화면 안, 위 막대 아래, [avoid](오른쪽 버튼) 밖으로 자리를 고른다. 지도를 움직이면 따라간다.
/// 위가 좁으면 핀 아래로, 버튼과 겹치면 왼쪽(안 되면 버튼 아래)으로. 핀이 화면 밖이면 숨긴다.
class _PopupLayer extends StatelessWidget {
  const _PopupLayer({required this.point, required this.minTop, required this.avoid, required this.child});
  final LatLng point;
  final double minTop;

  /// 지도 크기 → 피할 칸들.
  final List<Rect> Function(Size map) avoid;
  final Widget child;

  static const width = 248.0, height = 172.0, gap = 22.0, margin = 12.0;

  /// 핀 화면 위치 → 창 자리.
  static Rect place(Offset pin, Size size, double minTop, List<Rect> avoid) {
    var left = (pin.dx - width / 2).clamp(margin, size.width - margin - width).toDouble();
    var top = pin.dy - gap - height;
    if (top < minTop) top = pin.dy + gap; // 위가 좁으면 핀 아래
    var r = Rect.fromLTWH(left, top, width, height);
    for (final a in avoid) {
      final ob = a.inflate(8);
      if (!r.overlaps(ob)) continue;
      if (ob.left - width >= margin) {
        left = ob.left - width; // 버튼 왼쪽으로
      } else {
        top = ob.bottom; // 화면이 좁으면 버튼 아래로
      }
      r = Rect.fromLTWH(left, top, width, height);
    }
    return r;
  }

  @override
  Widget build(BuildContext context) {
    final camera = MapCamera.of(context);
    final pin = camera.latLngToScreenOffset(point);
    final size = camera.size;
    if (pin.dx < 0 || pin.dy < 0 || pin.dx > size.width || pin.dy > size.height) return const SizedBox.shrink();
    final r = place(pin, size, minTop, avoid(size));
    final above = r.bottom <= pin.dy;
    return Stack(children: [
      // 위면 창 아래 끝을 핀에 붙이고(내용 높이가 달라도), 아래면 위 끝을
      Positioned(
        left: r.left,
        width: width,
        top: above ? null : r.top,
        bottom: above ? size.height - r.bottom : null,
        child: child,
      ),
    ]);
  }
}

class _AllDone extends StatelessWidget {
  const _AllDone({required this.onFinish});
  final VoidCallback onFinish;

  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const SizedBox(height: 8),
        const Icon(Icons.celebration_outlined, color: AppColors.primary, size: 32),
        const SizedBox(height: 8),
        Text('오늘 일정을 모두 마쳤어요', style: AppText.headline, textAlign: TextAlign.center),
        Text('마치지 않으면 2시간 뒤 자동으로 마쳐요', style: AppText.meta, textAlign: TextAlign.center),
        const SizedBox(height: 14),
        AppButton(label: '여행 마치기', onPressed: onFinish),
      ]);
}

class _PanelSkeleton extends StatelessWidget {
  const _PanelSkeleton();

  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        for (final w in const [80.0, 200.0, 160.0])
          Container(width: w, height: 16, margin: const EdgeInsets.only(bottom: 10), decoration: BoxDecoration(color: AppColors.line, borderRadius: BorderRadius.circular(6))),
      ]);
}

class _StateError extends StatelessWidget {
  const _StateError({required this.onRetry});
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Row(children: [
        const Icon(Icons.cloud_off_outlined, color: AppColors.text3, size: 18),
        const SizedBox(width: 8),
        Expanded(child: Text('여행 상태를 불러오지 못했어요. 일정은 그대로예요.', style: AppText.meta)),
        TextButton(onPressed: onRetry, child: const Text('다시 시도')),
      ]);
}
