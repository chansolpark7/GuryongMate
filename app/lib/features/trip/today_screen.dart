import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';
import 'package:provider/provider.dart';

import '../../core/format.dart';
import '../../core/models/plan.dart';
import '../../core/models/safety.dart';
import '../../core/models/trip.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../../core/widgets/safety_widgets.dart';
import '../map/map_layers.dart';
import '../planner/plan_controller.dart';
import '../safety/safety_detail_sheet.dart';
import 'location.dart';
import 'trip_controller.dart';

/// 14 Today (동행 홈) · 15 이동 중 · 14-S1 위치 꺼짐/오프라인 · 14-S2 첫 장소 전 + 데모 위치 칩 (Figma 73:2042, 73:2240, 73:7222, 73:7401).
/// 지도(현재 위치 · 오늘 남은 경로 · 다녀온 구간 · 위험 구역) + 패널(지금 · 다음 · 남은 일정).
class TodayScreen extends StatefulWidget {
  const TodayScreen({super.key, required this.onEndTrip});

  /// 패널 맨 아래 "여행 종료" / 모두 마침 [여행 마치기] → 셸이 확인 다이얼로그 → 30.
  final VoidCallback onEndTrip;

  @override
  State<TodayScreen> createState() => _TodayScreenState();
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

  /// 14 [길 안내]: 머무는 중에도 다음 장소 안내 카드를 보인다.
  bool _guiding = false;

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
    if (_ready) WidgetsBinding.instance.addPostFrameCallback((_) => mounted ? _fit(t, items) : null);
    _ensureLegs(c, items.where((i) => i.status != 'done' || i == done.lastOrNull).toList());
    final showGuide = (moving || _guiding) && next != null;
    if (showGuide) _ensureGuide(t, next);
    if (beforeFirst) _ensureFirstWalk(t, next);
    final top = MediaQuery.paddingOf(context).top;

    return Scaffold(
      body: Stack(children: [
        Positioned.fill(child: _buildMap(c, t, items, done, current, next, showGuide)),
        Positioned(
          left: 16,
          right: 16,
          top: top + 11,
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Expanded(child: TripPill(title: plan.title, time: s?.now == null ? null : clockText(t.serverNow()))),
              const SizedBox(width: 12),
              _Bell(count: t.unseenCount, onTap: () => t.onOpenInbox?.call()),
            ]),
            if (t.demoMode) ...[const SizedBox(height: 8), const _DemoChip()],
            if (t.locationOff) ...[const SizedBox(height: 10), _LocationOffBanner(onEnable: t.requestAccess)],
            if (t.offline) ...[const SizedBox(height: 10), const _OfflineStrip()],
            if (showGuide && !t.offline && !t.locationOff) ...[const SizedBox(height: 10), _GuideCard(route: _guide, next: next, eta: s?.nextEta, loc: _loc(t), loading: _guideLoading)],
          ]),
        ),
        Positioned(
          right: 16,
          top: top + 222,
          child: Column(children: [
            AiFab(onTap: t.openAiFromFab, hasSuggestion: t.hasSuggestion),
            const SizedBox(height: 12),
            _RoundButton(
              icon: Icons.my_location,
              tooltip: '현재 위치',
              onTap: () {
                final l = _loc(t);
                if (l != null && _ready) _map.move(l, 16.5);
              },
            ),
          ]),
        ),
        DraggableScrollableSheet(
          initialChildSize: 0.49,
          minChildSize: 0.19,
          maxChildSize: 0.86,
          snap: true,
          snapSizes: const [0.19, 0.49, 0.86],
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
                    onGuide: () => setState(() => _guiding = true),
                    onPreview: () => t.onOpenGuide?.call(next.placeId),
                  )
                else if (moving && next != null)
                  _MovingSection(trip: t, from: t.item(s.currentItemId), next: next, onArrived: null, onRoute: () => c.setTab(1))
                else if (current != null)
                  _NowSection(
                    trip: t,
                    item: current,
                    safety: c.safety?.visit(current.id),
                    stale: t.locationOff,
                    onListen: () => t.onOpenGuide?.call(current.placeId),
                    onGuide: () => setState(() => _guiding = !_guiding),
                    onBadge: () => showSafetyDetailSheet(context, c, current),
                  ),
                if (s != null && !allDone && !beforeFirst && !moving && next != null) ...[
                  const Divider(height: 28, color: AppColors.line),
                  _NextSection(trip: t, next: next, number: items.indexOf(next) + 1),
                ],
                if (s != null && !allDone) ...[
                  const Divider(height: 28, color: AppColors.line),
                  _RemainingSection(
                    items: beforeFirst ? items : remaining.where((i) => i.id != next?.id || moving).toList(),
                    total: beforeFirst,
                    end: (t.today?.endAt ?? items.lastOrNull?.endAt),
                    safety: c.safety,
                    onAll: () => c.setTab(0),
                    onTap: (i) => c.showOnMap(i.id),
                  ),
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
      ]),
    );
  }

  Widget _buildMap(PlanController c, TripController t, List<PlanItem> items, List<PlanItem> done, PlanItem? current, PlanItem? next, bool showGuide) {
    final loc = _loc(t), acc = t.state?.location?.accuracy;
    final lines = <Polyline>[];
    for (var i = 1; i < items.length; i++) {
      final a = items[i - 1], b = items[i];
      final mode = b.legMode ?? c.plan?.travelMode ?? 'walk';
      final leg = _legs['${a.id}>${b.id}:$mode'];
      final walked = b.status != 'planned';
      final pts = leg?.$1 ?? [_pt(a), _pt(b)];
      final dotted = mode == 'car' || (leg?.$2 ?? true);
      lines.add(Polyline(
        points: pts,
        strokeWidth: walked ? 4 : 5,
        color: walked ? const Color(0xFFAEB6C1) : AppColors.primary,
        borderColor: Colors.white,
        borderStrokeWidth: walked ? 0 : 2,
        pattern: dotted && !walked ? StrokePattern.dotted(spacingFactor: 2) : const StrokePattern.solid(),
      ));
    }
    if (showGuide && _guide != null && _guide!.points.length > 1) {
      lines.add(Polyline(points: _guide!.points, strokeWidth: 5, color: AppColors.primary, borderColor: Colors.white, borderStrokeWidth: 2));
    }
    return FlutterMap(
      mapController: _map,
      options: MapOptions(
        initialCenter: loc ?? (items.isEmpty ? guryongpo : _pt(items.first)),
        initialZoom: 15.5,
        onMapReady: () {
          _ready = true;
          _fit(t, items);
        },
      ),
      children: [
        osmTiles(),
        // 위험 구역: 라벨은 지금 경고 중인 구역만 (23 지도 — 그 구역 Red 강조)
        ZoneLayer(zones: t.zones, labels: false),
        if (t.hazard != null) ZoneLayer(zones: t.zones.where((z) => t.hazard!.zones.any((h) => h.id == z.id)).toList()),
        PolylineLayer(polylines: lines),
        if (loc != null && acc != null)
          CircleLayer(circles: [
            CircleMarker(point: loc, radius: acc, useRadiusInMeter: true, color: AppColors.primary.withValues(alpha: 0.15), borderColor: AppColors.primary.withValues(alpha: 0.3), borderStrokeWidth: 1),
          ]),
        MarkerLayer(markers: [
          for (final (n, i) in items.indexed)
            Marker(
              point: _pt(i),
              width: i.id == current?.id ? 40 : 32,
              height: i.id == current?.id ? 40 : 32,
              child: GestureDetector(
                onTap: () => c.showOnMap(i.id),
                child: i.status == 'done'
                    ? const MapDot(color: Color(0xFFAEB6C1), icon: Icons.check, size: 28)
                    : MapDot(color: AppColors.primary, label: '${n + 1}', size: i.id == current?.id ? 36 : 28),
              ),
            ),
          if (loc != null)
            Marker(
              point: loc,
              width: 22,
              height: 22,
              child: Container(
                key: const ValueKey('today-location'),
                decoration: BoxDecoration(color: AppColors.primary, shape: BoxShape.circle, border: Border.all(color: Colors.white, width: 3), boxShadow: AppShadow.card),
              ),
            ),
        ]),
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
        child: Material(
          color: AppColors.surface,
          shape: const CircleBorder(),
          elevation: 3,
          shadowColor: const Color(0x33111827),
          child: InkWell(customBorder: const CircleBorder(), onTap: onTap, child: SizedBox(width: 44, height: 44, child: Icon(icon, color: AppColors.text))),
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
  const _GuideCard({required this.route, required this.next, required this.eta, required this.loc, required this.loading});
  final RouteResult? route;
  final PlanItem next;
  final String? eta;
  final LatLng? loc;
  final bool loading;

  static RouteStep? currentStep(RouteResult r, LatLng? loc) {
    if (r.steps.isEmpty) return null;
    var k = 0;
    if (loc != null) {
      var best = double.infinity;
      for (final (i, p) in r.points.indexed) {
        final d = distanceM(loc.latitude, loc.longitude, p.latitude, p.longitude);
        if (d < best) {
          best = d;
          k = i;
        }
      }
    }
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
    final title = r == null
        ? (loading ? '경로를 찾고 있어요' : '${next.title} 방향')
        : car
            ? '차로 ${r.minutes}분'
            : step?.instruction ?? '${next.title} 방향 · 약 ${r.minutes}분';
    final sub = '${next.title}까지 $mode ${r?.minutes ?? next.travelMinutes ?? '-'}분${eta == null ? '' : ' · $eta 도착 예상'}';
    final detour = r?.hazards?.avoided.isNotEmpty == true && (r?.hazards?.detourMinutes ?? 0) > 0 ? r!.hazards!.detourMinutes : null;
    return Container(
      key: const ValueKey('guide-card'),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(16), boxShadow: AppShadow.floating),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(color: AppColors.primary, borderRadius: BorderRadius.circular(10)),
            child: Icon(car ? Icons.directions_car : turnIcon(step?.type ?? ''), color: Colors.white),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: AppText.label.copyWith(fontSize: 15, fontWeight: FontWeight.w700), maxLines: 1, overflow: TextOverflow.ellipsis),
              Text(sub, style: AppText.meta, maxLines: 1, overflow: TextOverflow.ellipsis),
            ]),
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

/// ETA 칩 (Components_Companion): 제시간 / n분 여유 (Green) · n분 늦음 (Amber) · HH:MM 종료 · 규칙 넘음 (Red, 새 위반 + 대기 제안).
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

  /// 대기 중인 지연 제안이 있으면 Red "18:07 종료 · 규칙 넘음" (시각은 이벤트 문구·위반에서).
  static String? violationLabel(TripController t) {
    final e = t.pendingDelay;
    if (e == null) return null;
    final texts = [e.message, for (final v in (e.payload['new_violations'] as List?) ?? const []) if (v is Map) '${v['message'] ?? ''}'];
    for (final s in texts) {
      final m = RegExp(r'(\d{1,2}:\d{2})').allMatches(s).map((m) => m.group(1)!).where((x) => x != '18:00' || texts.length == 1).toList();
      if (m.isNotEmpty) return '${m.first} 종료 · 규칙 넘음';
    }
    return '규칙 넘음';
  }
}

class _NowSection extends StatelessWidget {
  const _NowSection({required this.trip, required this.item, required this.safety, required this.stale, required this.onListen, required this.onGuide, required this.onBadge});
  final TripController trip;
  final PlanItem item;
  final SafetyItem? safety;
  final bool stale;
  final VoidCallback onListen;
  final VoidCallback onGuide;
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
      const SizedBox(height: 14),
      Row(children: [
        Expanded(child: AppButton(label: '이야기 듣기', icon: Icons.auto_awesome, kind: ButtonKind.aiSoft, onPressed: onListen)),
        const SizedBox(width: 8),
        Expanded(child: AppButton(label: '길 안내', icon: Icons.near_me_outlined, kind: ButtonKind.secondary, onPressed: onGuide)),
      ]),
    ]);
  }
}

class _NextSection extends StatelessWidget {
  const _NextSection({required this.trip, required this.next, required this.number});
  final TripController trip;
  final PlanItem next;
  final int number;

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
        EtaChip(key: const ValueKey('eta-chip'), delay: s?.delayMinutes, violation: EtaChip.violationLabel(trip)),
      ]),
    ]);
  }
}

class _MovingSection extends StatelessWidget {
  const _MovingSection({required this.trip, required this.from, required this.next, required this.onArrived, required this.onRoute});
  final TripController trip;
  final PlanItem? from;
  final PlanItem next;
  final VoidCallback? onArrived;
  final VoidCallback onRoute;

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
      Text('${s.nextPlannedStart ?? next.startAt ?? '--:--'} 예정${s.nextEta == null ? '' : ' → ${s.nextEta} 도착 예상'}', style: AppText.meta),
      const SizedBox(height: 14),
      AppButton(label: '경로 전체', icon: Icons.alt_route, kind: ButtonKind.secondary, onPressed: onRoute),
    ]);
  }
}

class _RemainingSection extends StatelessWidget {
  const _RemainingSection({required this.items, required this.total, required this.end, required this.safety, required this.onAll, required this.onTap});
  final List<PlanItem> items;
  final bool total;
  final String? end;
  final SafetyCheck? safety;
  final VoidCallback onAll;
  final ValueChanged<PlanItem> onTap;

  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          Expanded(
            child: Text(
              !total && items.isEmpty ? '다음이 마지막 일정이에요${end == null ? '' : ' · $end 종료 예정'}' : '${total ? '오늘 일정' : '남은 일정'} ${items.length}곳${end == null ? '' : ' · $end 종료 예정'}',
              style: AppText.caption.copyWith(fontWeight: FontWeight.w700),
            ),
          ),
          InkWell(onTap: onAll, child: Text('전체 일정 ›', style: AppText.caption.copyWith(color: AppColors.primary, fontWeight: FontWeight.w700))),
        ]),
        if (!total)
          for (final i in items)
            InkWell(
              onTap: () => onTap(i),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 7),
                child: Row(children: [
                  SizedBox(width: 50, child: Text(i.startAt ?? '', style: AppText.meta.copyWith(fontWeight: FontWeight.w700, color: AppColors.text2))),
                  Expanded(child: Text(i.title, style: AppText.label, overflow: TextOverflow.ellipsis)),
                  if (safety?.visit(i.id) != null) RiskBadge(item: safety!.visit(i.id)!, compact: true),
                ]),
              ),
            ),
      ]);
}

class _BeforeFirst extends StatelessWidget {
  const _BeforeFirst({required this.first, required this.now, required this.walk, required this.onGuide, required this.onPreview});
  final PlanItem first;
  final DateTime now;
  final int? walk;
  final VoidCallback onGuide;
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
      const SizedBox(height: 14),
      Row(children: [
        Expanded(child: AppButton(label: '길 안내', icon: Icons.near_me_outlined, onPressed: onGuide)),
        const SizedBox(width: 8),
        Expanded(child: AppButton(label: '가이드 미리 보기', icon: Icons.auto_awesome, kind: ButtonKind.aiSoft, onPressed: onPreview)),
      ]),
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
