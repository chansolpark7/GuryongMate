import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';
import 'package:provider/provider.dart';

import '../../core/format.dart';
import '../../core/models/place.dart';
import '../../core/models/plan.dart';
import '../../core/models/safety.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../../core/widgets/safety_widgets.dart';
import '../chat/chat_controller.dart';
import '../chat/proposal_card.dart';
import '../planner/item_actions.dart';
import '../planner/place_search_sheet.dart';
import '../planner/plan_controller.dart';
import '../trip/trip_controller.dart';
import 'map_layers.dart';

const _guryongpo = guryongpo;

/// 07_Map / 09_Map_After_Apply / 10·28-S1 Layers / 28_Map_Safety. Planner 와 같은 PlanController 를 쓴다.
/// 경로선은 routing geometry (구간마다 leg_mode, 차량은 점선), 위험 구역은 TripController.zones (GET /safety/avoid, Today 와 같은 데이터).
/// Map 은 "둘러보기·자세히": 아래 시트를 끌어 올리면 그날 일정 목록 · 장소 상세 (docs/app/today-map-redesign.md §3).
/// 동행 중이면 내 위치 · 지나온 구간 회색 · 지금/다음 강조 (§3.2).
class MapScreen extends StatefulWidget {
  const MapScreen({super.key});

  @override
  State<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends State<MapScreen> {
  final _map = MapController();
  bool _ready = false;
  bool _layersOpen = false;
  bool _showItinerary = true;
  bool _showRoute = true;
  bool _showPlaces = false;
  bool _showZones = true;
  List<PlaceSummary> _places = const [];
  String? _fittedKey;
  int? _focusedItem;

  /// 관광지 레이어에서 고른 장소 (아래 시트에 상세 + [Day N에 추가]).
  PlaceSummary? _place;

  /// 아래 시트: 접힘(요약) · 반 · 펼침. 내용이 바뀌면(경로 ↔ 장소 ↔ 우회) 접힘으로.
  final _sheet = DraggableScrollableController();
  String? _sheetMode;

  /// 내용이 바뀐 첫 프레임: 시트가 아직 이전 높이라 펼침으로 보지 않는다 (접기 전에 상세를 부르지 않게).
  bool _settling = false;

  /// 접힘 내용(손잡이 + 카드/요약)을 잰 높이(px), 내용별("item:12"). 칩이 여러 줄이어도 버튼까지 보이게 (2026-10-11).
  final _headH = <String, double>{};

  /// 펼친 일정 장소의 분류 줄 ("항구/포구 · 구룡포읍") — 상세를 받아 오면 카드에 한 줄로 (상세의 제목 줄은 숨김).
  final _category = <String, String>{};

  /// 구간 경로선: "fromId>toId:mode" → (점 목록, 직선 근사인지).
  final _legs = <String, (List<LatLng>, bool)>{};
  final _legsLoading = <String>{};

  /// 우회 카드 (28): 닫았거나 [원래 길] 이면 숨김.
  int? _detourDismissed;
  int? _detourOn;

  PlanController get _c => context.read<PlanController>();

  List<PlanItem> _visibleItems(PlanController c) =>
      (c.currentDay?.items ?? const <PlanItem>[]).where((i) => i.place != null && !i.skipped).toList();

  /// Day 가 바뀌거나 일정이 바뀌면 전체가 보이게, 선택이 바뀌면 그 핀으로.
  void _syncCamera(PlanController c) {
    if (!_ready) return;
    final items = _visibleItems(c);
    final key = '${c.day}:${items.map((i) => i.id).join(',')}';
    final sel = c.selectedItemId == null ? null : c.plan?.item(c.selectedItemId!);
    if (sel?.place != null && sel!.id != _focusedItem) {
      _focusedItem = sel.id;
      _map.move(LatLng(sel.place!.lat, sel.place!.lng), _map.camera.zoom < 15 ? 15 : _map.camera.zoom);
      return;
    }
    if (c.selectedItemId == null) _focusedItem = null;
    if (key == _fittedKey) return;
    _fittedKey = key;
    _fit(items);
  }

  void _fit(List<PlanItem> items) {
    final pts = items.map((i) => LatLng(i.place!.lat, i.place!.lng)).toList();
    if (pts.isEmpty) {
      _map.move(_guryongpo, 14);
    } else if (pts.length == 1) {
      _map.move(pts.first, 15);
    } else {
      _map.fitCamera(CameraFit.coordinates(
        coordinates: pts,
        padding: const EdgeInsets.fromLTRB(48, 140, 48, 260),
        maxZoom: 16.5,
      ));
    }
  }

  Future<void> _togglePlaces(bool on) async {
    setState(() => _showPlaces = on);
    if (!on || _places.isNotEmpty) return;
    try {
      final center = _ready ? _map.camera.center : _guryongpo;
      final res = await _c.api.searchPlaces(lat: center.latitude, lng: center.longitude, radiusM: 4000, limit: 80);
      if (mounted) setState(() => _places = res);
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  /// 관광지 레이어 장소 → 같은 아래 시트로 (모달 대신, redesign §3.1).
  void _openPlace(PlaceSummary p) {
    setState(() => _place = p);
    if (_c.selectedItemId != null) _c.select(null);
  }

  Future<void> _addPlace(PlaceSummary p, int stay) async {
    final c = _c;
    final day = c.day;
    try {
      await c.addPlace(p.id, day: day, stayMinutes: stay);
      if (!mounted) return;
      setState(() => _place = null);
      showInfo(context, 'Day $day에 추가했어요');
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  void dispose() {
    _sheet.dispose();
    super.dispose();
  }

  static String _legKey(PlanItem a, PlanItem b, String mode) => '${a.id}>${b.id}:$mode';

  /// 지도가 보일 때만 구간 경로선을 묻는다 (IndexedStack 안이라 Planner 탭에서는 부르지 않는다).
  void _ensureLegs(PlanController c, List<PlanItem> items) {
    for (var i = 1; i < items.length; i++) {
      final a = items[i - 1], b = items[i];
      final mode = b.legMode ?? c.plan?.travelMode ?? 'walk';
      final key = _legKey(a, b, mode);
      if (_legs.containsKey(key) || !_legsLoading.add(key)) continue;
      final pa = LatLng(a.place!.lat, a.place!.lng), pb = LatLng(b.place!.lat, b.place!.lng);
      c.routes.leg(mode, pa, pb).then((r) {
        _legsLoading.remove(key);
        if (!mounted) return;
        setState(() => _legs[key] = r == null || r.points.length < 2 ? ([pa, pb], true) : (r.points, r.estimated));
      });
    }
  }

  void _showZone(AvoidZone z, DateTime? at) => showModalBottomSheet<void>(
        context: context,
        showDragHandle: true,
        builder: (ctx) => SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Expanded(child: Text(z.name, style: AppText.headline)),
                RiskChip(risk: z.risk, label: riskWord(z.risk)),
              ]),
              const SizedBox(height: 4),
              Text(z.kind == 'breakwater' ? '방파제' : '침수 감시 구역', style: AppText.caption),
              const SizedBox(height: 10),
              Text(z.reason.split('; ').join('\n'), style: AppText.meta.copyWith(color: AppColors.text)),
              const SizedBox(height: 8),
              Text('지금 기준${at == null ? '' : ' ${hhmm(at)}'}', style: AppText.caption),
            ]),
          ),
        ),
      );

  /// 이 날 이동 중 우회할 구역이 있는 첫 구간 (safety travel 항목의 detour.avoided).
  PlanItem? _detourCandidate(PlanController c) {
    if (!_showZones) return null;
    for (final t in c.safety?.items ?? const <SafetyItem>[]) {
      if (!t.isVisit && (t.detour?.avoided.isNotEmpty ?? false) && (t.day == null || t.day == c.day)) {
        final it = c.plan?.item(t.itemId);
        if (it != null && it.place != null && !it.skipped) return it;
      }
    }
    return null;
  }

  PlanItem? _previousOf(List<PlanItem> items, PlanItem item) {
    final i = items.indexWhere((x) => x.id == item.id);
    return i > 0 ? items[i - 1] : null;
  }

  static const _walkedGrey = Color(0xFFAEB6C1);

  /// 시트 위 투명 칸 (FAB · 전체 경로 버튼 · 토스트 · 지도 출처). 시트와 함께 움직이고 펼치면 사라진다.
  static const _floatH = 150.0;

  Widget _buildSheet(PlanController c, TripController t, List<PlanItem> items, PlanItem? selected, PlanItem? detourItem, bool showChange) {
    final sel = selected != null && items.any((i) => i.id == selected.id) ? selected : null;
    final detour = sel == null && _place == null && detourItem != null && _detourDismissed != detourItem.id ? detourItem : null;
    final mode = sel != null ? 'item' : _place != null ? 'place' : detour != null ? 'detour' : 'route';
    // 접힘 높이(px) = 잰 접힘 내용 높이. 재기 전 첫 프레임은 예전 고정값 (장소 카드 · 관광지 요약 + 추가 · 우회 카드 · 경로 한 줄)
    final headKey = switch (mode) { 'item' => 'item:${sel!.id}', 'place' => 'place:${_place!.id}', 'detour' => 'detour:${detour!.id}', _ => 'route' };
    final cardPx = _headH[headKey] ?? switch (mode) { 'item' => 236.0, 'place' => 214.0, 'detour' => 300.0, _ => 104.0 };
    return LayoutBuilder(builder: (context, box) {
      final h = box.maxHeight;
      final min = ((_floatH + cardPx) / h).clamp(0.2, 0.55);
      final half = min + 0.15 > 0.62 ? min + 0.15 : 0.62;
      const full = 1.0;
      final request = c.mapDetailRequest;
      if (mode != _sheetMode || (request != null && request == sel?.id)) {
        final first = _sheetMode == null;
        _sheetMode = mode;
        _settling = true;
        final to = request != null && request == sel?.id ? full : min;
        c.mapDetailRequest = null;
        WidgetsBinding.instance.addPostFrameCallback((_) {
          _settling = false;
          if (!mounted || !_sheet.isAttached || (first && to == min)) return;
          to == min ? _sheet.jumpTo(min) : _sheet.animateTo(to, duration: const Duration(milliseconds: 220), curve: Curves.easeOut);
        });
      }
      return DraggableScrollableSheet(
        controller: _sheet,
        initialChildSize: min,
        minChildSize: min,
        maxChildSize: full,
        snap: true,
        snapSizes: [min, half, full],
        builder: (context, scroll) => LayoutBuilder(builder: (context, bc) {
          final open = !_settling && bc.maxHeight > (_floatH + cardPx) + 40;
          return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            SizedBox(
              height: _floatH,
              child: IgnorePointer(
                ignoring: open,
                child: AnimatedOpacity(
                  opacity: open ? 0 : 1,
                  duration: const Duration(milliseconds: 150),
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(16, 0, 16, 6),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisAlignment: MainAxisAlignment.end, children: [
                      Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
                        if (showChange && sel == null) const _Toast(text: 'AI 변경이 지도에 반영됐어요'),
                        const Spacer(),
                        Column(children: [
                          _RoundButton(icon: Icons.my_location, onTap: () => _fit(items), tooltip: '전체 경로 보기'),
                          const SizedBox(height: 10),
                          AiFab(onTap: () => c.openAi()),
                        ]),
                      ]),
                      const SizedBox(height: 4),
                      Text('© OpenStreetMap contributors', style: AppText.caption.copyWith(fontSize: 10, color: AppColors.text2)),
                    ]),
                  ),
                ),
              ),
            ),
            Expanded(
              child: Container(
                key: const ValueKey('map-sheet'),
                decoration: const BoxDecoration(
                  color: AppColors.surface,
                  borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
                  boxShadow: [BoxShadow(color: Color(0x14111827), blurRadius: 16, offset: Offset(0, -2))],
                ),
                child: ListView(
                  controller: scroll,
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
                  children: [
                    // 접힘에 보일 머리만 잰다 (펼침에서 더해지는 줄은 재지 않음)
                    _MeasureHeight(
                      onHeight: (px) => _onHeadHeight(headKey, px, open, min),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
                        const Grabber(),
                        ...switch (mode) {
                          'item' => [_PlaceMapCard(controller: c, items: items, item: sel!, category: open ? _category[sel.placeId] : null)],
                          'place' => _placeHead(c, _place!),
                          'detour' => [
                              _DetourCard(
                                controller: c,
                                item: detour!,
                                previous: _previousOf(items, detour),
                                on: _detourOn == detour.id,
                                onOriginal: () => setState(() {
                                  _detourOn = null;
                                  _detourDismissed = detour.id;
                                }),
                                onUse: () => _useDetour(c, t, detour),
                              ),
                            ],
                          _ => [_RouteSummary(controller: c, items: items, showDelta: showChange)],
                        },
                      ]),
                    ),
                    ...switch (mode) {
                      'item' => _itemDetail(c, t, sel!, open),
                      'place' => _placeDetail(c, _place!, open),
                      'detour' => const <Widget>[],
                      _ => [
                          const Divider(height: 24, color: AppColors.line),
                          for (final (n, i) in items.indexed) _DayRow(number: n + 1, item: i, safety: c.safety?.visit(i.id), walked: t.active && i.status == 'done', onTap: () => c.select(i.id)),
                        ],
                    },
                  ],
                ),
              ),
            ),
          ]);
        }),
      );
    });
  }

  /// 접힘 머리를 다시 잰 높이 → 접힘 높이. 접혀 있었으면 새 높이로 맞춘다.
  void _onHeadHeight(String key, double h, bool open, double min) {
    if (!mounted || open || _settling) return;
    final px = h + 16; // 아래 여백 (버튼 그림자)
    final old = _headH[key];
    if (old != null && (old - px).abs() < 1) return;
    final wasMin = _sheet.isAttached && _sheet.size <= min + 0.01;
    setState(() {
      _headH[key] = px;
      _settling = wasMin; // 새 접힘 높이로 옮기는 동안 펼침으로 보지 않는다
    });
    if (!wasMin) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _settling = false;
      if (!mounted || !_sheet.isAttached) return;
      final box = context.findRenderObject() as RenderBox?;
      if (box == null || !box.hasSize) return;
      _sheet.jumpTo(((_floatH + px) / box.size.height).clamp(0.2, 0.55));
    });
  }

  /// 고른 일정 장소: 접힘 = 카드(좌우로 넘기기), 펼침 = [설명 듣기](동행 중 오늘 장소) + 일정 추가 화면과 같은 장소 상세 (제목·분류는 카드에).
  List<Widget> _itemDetail(PlanController c, TripController t, PlanItem item, bool open) => [
        if (open) ...[
          const Divider(height: 28, color: AppColors.line),
          if (t.active && t.today?.items.any((i) => i.id == item.id) == true && item.place != null) ...[
            AppButton(
              key: const ValueKey('map-listen'),
              label: '설명 듣기',
              icon: Icons.auto_awesome,
              kind: ButtonKind.aiSoft,
              height: 44,
              onPressed: () => t.onOpenGuide?.call(item.placeId),
            ),
            const SizedBox(height: 16),
          ],
          if (item.place != null)
            PlaceDetailContent(
              key: ValueKey('detail-${item.placeId}'),
              api: c.api,
              placeId: item.placeId,
              showTitle: false,
              onLoaded: (p) {
                final line = [p.categoryLabel, if (p.area.isNotEmpty) p.area].join(' · ');
                if (mounted && _category[item.placeId] != line) setState(() => _category[item.placeId] = line);
              },
            ),
        ],
      ];

  /// 관광지 레이어 장소: 접힘 = 이름·분류 + [Day N에 추가], 펼침 = 장소 상세 (제목 줄은 위에).
  List<Widget> _placeHead(PlanController c, PlaceSummary p) => [
        Row(children: [
          PlaceThumb(contentType: p.contentType, url: p.thumbnailUrl, size: 44),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(p.title, style: AppText.headline, overflow: TextOverflow.ellipsis),
              Text([p.categoryLabel, if (p.area.isNotEmpty) p.area].join(' · '), style: AppText.meta, overflow: TextOverflow.ellipsis),
            ]),
          ),
          IconButton(tooltip: '닫기', onPressed: () => setState(() => _place = null), icon: const Icon(Icons.close, color: AppColors.text3)),
        ]),
        const SizedBox(height: 12),
        AddPlaceFooter(key: ValueKey('add-${p.id}'), day: c.day, onAdd: (stay) => _addPlace(p, stay)),
      ];

  List<Widget> _placeDetail(PlanController c, PlaceSummary p, bool open) => [
        if (open) ...[
          const Divider(height: 28, color: AppColors.line),
          PlaceDetailContent(key: ValueKey('detail-${p.id}'), api: c.api, placeId: p.id, showTitle: false),
        ],
      ];

  /// 28 [이 길로 안내]: 동행 중이면 Today 로 돌아가 이 장소로 길 안내 (위험 구역을 피하는 길, 15). 아니면 지도에 표시만.
  void _useDetour(PlanController c, TripController t, PlanItem item) {
    setState(() => _detourOn = item.id);
    if (t.active && t.today?.items.any((i) => i.id == item.id) == true && item.status != 'done') {
      t.setGuide(item.id);
      c.setTab(2);
      showInfo(context, '${item.title}까지 위험 구역을 피해 안내해요');
    } else {
      showInfo(context, '여행 중에는 이 길로 다음 장소를 안내해요');
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = context.watch<PlanController>();
    final t = context.watch<TripController>();
    final plan = c.plan;
    final items = _visibleItems(c);
    if (c.tab == 1) {
      _ensureLegs(c, items);
      if (_showZones) t.loadZones(); // 60초에 한 번 (Today 와 같은 구역)
    }
    final zones = t.zones;
    // 동행 중이고 여행 날을 보고 있으면: 내 위치 · 지나온 구간 회색 · 지금/다음 강조
    final trip = t.active && (t.state?.day ?? c.day) == c.day ? t.state : null;
    final loc = trip?.location == null ? null : LatLng(trip!.location!.lat, trip.location!.lng);
    final detourItem = _detourCandidate(c);
    final applied = c.lastApplied;
    final ghostDay = applied?.before.dayOf(c.day);
    final oldRoute = (ghostDay?.items ?? const <PlanItem>[]).where((i) => i.place != null && !i.skipped).map((i) => LatLng(i.place!.lat, i.place!.lng)).toList();
    final removed = plan == null || applied == null ? const <PlanItem>[] : applied.removedItems(plan).where((i) => i.day == c.day && i.place != null).toList();
    final showChange = applied != null && c.aiUpdates.isNotEmpty && oldRoute.isNotEmpty;
    final selected = c.selectedItemId == null ? null : plan?.item(c.selectedItemId!);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _syncCamera(c);
    });

    final legLines = <Polyline>[];
    for (var i = 1; i < items.length; i++) {
      final a = items[i - 1], b = items[i];
      final mode = b.legMode ?? plan?.travelMode ?? 'walk';
      final leg = _legs[_legKey(a, b, mode)];
      final pts = leg?.$1 ?? [LatLng(a.place!.lat, a.place!.lng), LatLng(b.place!.lat, b.place!.lng)];
      final dashed = mode == 'car' || (leg?.$2 ?? true);
      final walked = trip != null && b.status != 'planned';
      legLines.add(Polyline(
        points: pts,
        strokeWidth: walked ? 4 : 5,
        color: walked ? _walkedGrey : AppColors.primary,
        borderStrokeWidth: dashed || walked ? 0 : 3,
        borderColor: Colors.white,
        pattern: dashed && !walked ? StrokePattern.dashed(segments: const [10, 8]) : const StrokePattern.solid(),
      ));
    }
    return Scaffold(
      body: Stack(children: [
        FlutterMap(
          mapController: _map,
          options: MapOptions(
            initialCenter: _guryongpo,
            initialZoom: 14,
            onMapReady: () {
              _ready = true;
              _syncCamera(c);
            },
            onTap: (_, _) {
              if (_layersOpen) {
                setState(() => _layersOpen = false);
              } else if (_place != null) {
                setState(() => _place = null);
              } else {
                c.select(null);
              }
            },
          ),
          children: [
            osmTiles(),
            if (_showZones) ZoneLayer(zones: zones, onTap: (z) => _showZone(z, t.zonesNow)),
            if (_showRoute)
              PolylineLayer(polylines: [
                if (showChange && oldRoute.length > 1)
                  Polyline(points: oldRoute, strokeWidth: 3, color: AppColors.text3, pattern: StrokePattern.dashed(segments: const [6, 6])),
                if (_detourOn == null) ...legLines,
              ]),
            if (_showRoute && _detourOn != null && detourItem != null) _DetourLines(controller: c, item: detourItem, previous: _previousOf(items, detourItem)),
            if (_showPlaces)
              MarkerLayer(markers: [
                for (final p in _places)
                  if (!items.any((i) => i.placeId == p.id))
                    Marker(
                      point: LatLng(p.lat, p.lng),
                      width: 26,
                      height: 26,
                      child: GestureDetector(
                        onTap: () => _openPlace(p),
                        child: Tooltip(
                          message: p.title,
                          child: Container(
                            decoration: BoxDecoration(color: AppColors.surface, shape: BoxShape.circle, border: Border.all(color: AppColors.text3), boxShadow: AppShadow.card),
                            child: Icon(placeVisual(p.contentType).$1, size: 14, color: AppColors.text2),
                          ),
                        ),
                      ),
                    ),
              ]),
            if (_showItinerary)
              MarkerLayer(markers: [
                if (showChange)
                  for (final g in removed)
                    Marker(
                      point: LatLng(g.place!.lat, g.place!.lng),
                      width: 26,
                      height: 26,
                      child: Tooltip(
                        message: '제거됨: ${g.title}',
                        child: Container(
                          decoration: BoxDecoration(color: const Color(0xFFC4C9D1), shape: BoxShape.circle, border: Border.all(color: Colors.white, width: 3)),
                        ),
                      ),
                    ),
                for (final (i, it) in items.indexed)
                  if (it.id != selected?.id)
                    Marker(
                      point: LatLng(it.place!.lat, it.place!.lng),
                      width: 48,
                      height: 48,
                      child: _Pin(
                        key: ValueKey('map-pin-${it.id}'),
                        number: i + 1,
                        ai: c.aiUpdates.containsKey(it.id),
                        done: trip != null && it.status == 'done',
                        now: trip != null && it.id == trip.currentItemId && trip.visiting,
                        next: trip != null && it.id == trip.nextItemId,
                        onTap: () {
                          if (_place != null) setState(() => _place = null);
                          c.select(it.id);
                        },
                      ),
                    ),
                if (selected?.place != null && items.any((i) => i.id == selected!.id))
                  Marker(
                    point: LatLng(selected!.place!.lat, selected.place!.lng),
                    width: 160,
                    height: 112,
                    alignment: const Alignment(0, -0.18),
                    child: _SelectedPin(
                      number: items.indexWhere((i) => i.id == selected.id) + 1,
                      label: selected.title,
                      ai: c.aiUpdates.containsKey(selected.id),
                    ),
                  ),
                if (_place != null)
                  Marker(
                    point: LatLng(_place!.lat, _place!.lng),
                    width: 160,
                    height: 112,
                    alignment: const Alignment(0, -0.18),
                    child: _SelectedPin(label: _place!.title, icon: placeVisual(_place!.contentType).$1, ai: false),
                  ),
              ]),
            // 동행 중 내 위치 (Today 와 같은 점)
            if (loc != null) UserLocationLayer(point: loc, accuracy: trip?.location?.accuracy, duration: t.markerGlide, dotKey: const ValueKey('map-location')),
          ],
        ),
        // Map Top Bar (floating)
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
              Row(children: [
                _RoundButton(icon: Icons.arrow_back_ios_new, onTap: () => Navigator.of(context).maybePop(), tooltip: '뒤로'),
                const SizedBox(width: 10),
                Expanded(child: _PlanSwitcher(controller: c)),
                const SizedBox(width: 10),
                _RoundButton(
                  icon: Icons.layers_outlined,
                  active: _layersOpen,
                  onTap: () => setState(() => _layersOpen = !_layersOpen),
                  tooltip: '지도 레이어',
                ),
              ]),
              if (_showZones && zones.isNotEmpty && !_layersOpen)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Align(alignment: Alignment.centerLeft, child: _ZoneLegend(at: t.zonesNow)),
                ),
              if (_layersOpen)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: _LayerPanel(
                    itinerary: _showItinerary,
                    route: _showRoute,
                    zones: _showZones,
                    places: _showPlaces,
                    onItinerary: (v) => setState(() => _showItinerary = v),
                    onRoute: (v) => setState(() => _showRoute = v),
                    onZones: (v) => setState(() => _showZones = v),
                    onPlaces: _togglePlaces,
                  ),
                ),
            ]),
          ),
        ),
        // 하단: 끄는 시트 (위 투명 칸에 AI FAB · 전체 경로 · 토스트). 지도가 보일 때만 — Today 패널·AI 시트와 찾기가 섞이지 않게.
        if (plan != null && c.tab == 1) _buildSheet(c, t, items, selected, detourItem, showChange),
      ]),
    );
  }
}

/// 위험 구역 범례 pill: "● 위험 ● 주의 · 지금 기준 HH:MM".
class _ZoneLegend extends StatelessWidget {
  const _ZoneLegend({required this.at});
  final DateTime? at;

  @override
  Widget build(BuildContext context) {
    Widget dot(Color c) => Container(width: 8, height: 8, decoration: BoxDecoration(color: c, shape: BoxShape.circle));
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(14), boxShadow: AppShadow.card),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        dot(AppColors.danger),
        const SizedBox(width: 4),
        Text('위험', style: AppText.caption.copyWith(color: AppColors.text)),
        const SizedBox(width: 8),
        dot(AppColors.warning),
        const SizedBox(width: 4),
        Text('주의', style: AppText.caption.copyWith(color: AppColors.text)),
        Text(' · 지금 기준${at == null ? '' : ' ${hhmm(at!)}'}', style: AppText.caption),
      ]),
    );
  }
}

/// 원래 길(회색 점선) + 피해 가는 길(Primary) — [이 길로 안내] 를 눌렀을 때.
class _DetourLines extends StatelessWidget {
  const _DetourLines({required this.controller, required this.item, required this.previous});
  final PlanController controller;
  final PlanItem item;
  final PlanItem? previous;

  @override
  Widget build(BuildContext context) {
    final prev = previous;
    if (prev == null) return const SizedBox.shrink();
    final a = LatLng(prev.place!.lat, prev.place!.lng), b = LatLng(item.place!.lat, item.place!.lng);
    final mode = item.legMode ?? 'walk';
    return FutureBuilder<List<RouteResult?>>(
      future: Future.wait([controller.routes.leg(mode, a, b, hazards: 'report'), controller.routes.leg(mode, a, b, hazards: 'avoid')]),
      builder: (context, snap) {
        final r = snap.data;
        return PolylineLayer(polylines: [
          if (r?[0] != null) Polyline(points: r![0]!.points, strokeWidth: 4, color: AppColors.text3, pattern: StrokePattern.dashed(segments: const [8, 6])),
          if (r?[1] != null) Polyline(points: r![1]!.points, strokeWidth: 5, color: AppColors.primary, borderStrokeWidth: 3, borderColor: Colors.white),
        ]);
      },
    );
  }
}

/// 28 하단 우회 카드: "위험 구역을 피해 가는 길" · [원래 길 n분 · m m] [피해 가는 길 n분 · m m (+k분)].
class _DetourCard extends StatelessWidget {
  const _DetourCard({required this.controller, required this.item, required this.previous, required this.on, required this.onOriginal, required this.onUse});
  final PlanController controller;
  final PlanItem item;
  final PlanItem? previous;
  final bool on;
  final VoidCallback onOriginal;
  final VoidCallback onUse;

  @override
  Widget build(BuildContext context) {
    final prev = previous;
    if (prev == null) return const SizedBox.shrink();
    final a = LatLng(prev.place!.lat, prev.place!.lng), b = LatLng(item.place!.lat, item.place!.lng);
    final mode = item.legMode ?? 'walk';
    return FutureBuilder<RouteResult?>(
      future: controller.routes.leg(mode, a, b, hazards: 'avoid'),
      builder: (context, snap) {
        final r = snap.data;
        final h = r?.hazards;
        Widget box(String title, String value, {bool strike = false, bool primary = false}) => Expanded(
              child: Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(color: primary ? AppColors.primarySoft : AppColors.background, borderRadius: BorderRadius.circular(12)),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(title, style: AppText.caption.copyWith(color: primary ? AppColors.primary : AppColors.text2)),
                  Text(value,
                      style: AppText.label.copyWith(
                          fontWeight: FontWeight.w700, color: primary ? AppColors.primary : AppColors.text3, decoration: strike ? TextDecoration.lineThrough : null)),
                ]),
              ),
            );
        String dist(int? m) => m == null ? '' : (m >= 1000 ? '${(m / 1000).toStringAsFixed(1)}km' : '${m}m');
        return Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
            Row(children: [
              const Icon(Icons.alt_route, color: AppColors.primary, size: 20),
              const SizedBox(width: 6),
              Text('위험 구역을 피해 가는 길', style: AppText.section),
            ]),
            const SizedBox(height: 2),
            Text('${prev.title} → ${item.title} · ${mode == 'car' ? '차량' : '도보'}', style: AppText.meta),
            const SizedBox(height: 10),
            if (snap.connectionState != ConnectionState.done)
              const LinearProgressIndicator(minHeight: 2)
            else if (r == null || h == null || !h.checked)
              Text('안전 정보를 확인하지 못했어요 (안전하다는 뜻이 아니에요)', style: AppText.caption.copyWith(color: AppColors.warning))
            else ...[
              Row(children: [
                box('원래 길', '${h.baselineMinutes ?? '-'}분 · ${dist(h.baselineDistanceM)}', strike: true),
                const SizedBox(width: 8),
                box('피해 가는 길', '${r.minutes}분 · ${dist(r.distanceM)}${h.detourMinutes == null ? '' : ' (+${h.detourMinutes}분)'}', primary: true),
              ]),
              const SizedBox(height: 8),
              Text(h.avoided.isEmpty ? '피할 구역이 경로에 없어요' : '${h.avoided.join(', ')}을(를) 지나지 않아요', style: AppText.caption),
              if (h.note != null) Text('${h.note} 목적지 자체가 위험하면 일정을 바꿔야 해요.', style: AppText.caption.copyWith(color: AppColors.warning)),
            ],
            const SizedBox(height: 12),
            Row(children: [
              Expanded(child: AppButton(label: '원래 길', kind: ButtonKind.secondary, height: 44, onPressed: onOriginal)),
              const SizedBox(width: 8),
              Expanded(flex: 2, child: AppButton(label: on ? '이 길로 보는 중' : '이 길로 안내', icon: Icons.near_me_outlined, height: 44, onPressed: on ? null : onUse)),
            ]),
          ]),
        );
      },
    );
  }
}

class _RoundButton extends StatelessWidget {
  const _RoundButton({required this.icon, required this.onTap, required this.tooltip, this.active = false});
  final IconData icon;
  final VoidCallback onTap;
  final String tooltip;
  final bool active;

  @override
  Widget build(BuildContext context) => Tooltip(
        message: tooltip,
        child: Container(
          decoration: const BoxDecoration(shape: BoxShape.circle, boxShadow: AppShadow.floating),
          child: Material(
            color: active ? AppColors.primarySoft : AppColors.surface,
            shape: const CircleBorder(),
            child: InkWell(
              customBorder: const CircleBorder(),
              onTap: onTap,
              child: SizedBox(width: 44, height: 44, child: Icon(icon, size: 20, color: active ? AppColors.primary : AppColors.text)),
            ),
          ),
        ),
      );
}

class _PlanSwitcher extends StatelessWidget {
  const _PlanSwitcher({required this.controller});
  final PlanController controller;

  @override
  Widget build(BuildContext context) {
    final plan = controller.plan;
    final n = plan?.numDays ?? 1;
    return Container(
      height: 44,
      decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(22), boxShadow: AppShadow.floating),
      child: PopupMenuButton<int>(
        tooltip: 'Day 선택',
        position: PopupMenuPosition.under,
        onSelected: controller.setDay,
        itemBuilder: (_) => [
          for (var d = 1; d <= n; d++)
            PopupMenuItem(
              value: d,
              child: Text('Day $d · ${plan == null ? '' : md(plan.startDate.add(Duration(days: d - 1)))}',
                  style: TextStyle(fontWeight: d == controller.day ? FontWeight.w700 : FontWeight.w400)),
            ),
        ],
        child: Center(
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Flexible(child: Text(plan?.title ?? '', style: AppText.label.copyWith(fontWeight: FontWeight.w700), overflow: TextOverflow.ellipsis)),
            Text(' · Day ${controller.day}', style: AppText.label.copyWith(color: AppColors.text3)),
            const Icon(Icons.expand_more, size: 18, color: AppColors.text3),
          ]),
        ),
      ),
    );
  }
}

/// 28-S1 레이어 패널: "날씨" → 위험 구역(지금 기준), "혼잡도" → 비활성 "데이터 없음".
class _LayerPanel extends StatelessWidget {
  const _LayerPanel({required this.itinerary, required this.route, required this.zones, required this.places, required this.onItinerary, required this.onRoute, required this.onZones, required this.onPlaces});
  final bool itinerary;
  final bool route;
  final bool zones;
  final bool places;
  final ValueChanged<bool> onItinerary;
  final ValueChanged<bool> onRoute;
  final ValueChanged<bool> onZones;
  final ValueChanged<bool> onPlaces;

  @override
  Widget build(BuildContext context) {
    Widget row(IconData icon, String label, bool? value, ValueChanged<bool>? onChanged, {String? note}) => InkWell(
          onTap: onChanged == null ? null : () => onChanged(!(value ?? false)),
          child: SizedBox(
            height: 44,
            child: Row(children: [
              Checkbox(value: value ?? false, onChanged: onChanged == null ? null : (v) => onChanged(v ?? false)),
              Icon(icon, size: 18, color: onChanged == null ? AppColors.text3 : AppColors.text2),
              const SizedBox(width: 8),
              Expanded(child: Text(label, style: AppText.label.copyWith(color: onChanged == null ? AppColors.text3 : AppColors.text))),
              if (note != null) ...[const SizedBox(width: 6), Text(note, style: AppText.caption)],
            ]),
          ),
        );
    return Container(
      width: 232,
      padding: const EdgeInsets.fromLTRB(8, 12, 12, 8),
      decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(16), boxShadow: AppShadow.floating),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(padding: const EdgeInsets.only(left: 12, bottom: 4), child: Text('지도 레이어', style: AppText.label.copyWith(fontWeight: FontWeight.w700))),
        row(Icons.place_outlined, '일정', itinerary, onItinerary),
        row(Icons.route, '이동 경로', route, onRoute),
        row(Icons.shield_outlined, '위험 구역', zones, onZones, note: '지금 기준'),
        row(Icons.photo_camera_outlined, '관광지', places, onPlaces),
        Opacity(opacity: 0.5, child: row(Icons.groups_outlined, '혼잡도', false, null, note: '데이터 없음')),
      ]),
    );
  }
}

/// 일정 핀. 동행 중이면 다녀온 곳 = 회색 ✓, 지금 머무는 곳 = 크게 + 옅은 원, 다음 장소 = Primary 테두리 고리 (14 와 같은 규칙).
class _Pin extends StatelessWidget {
  const _Pin({super.key, required this.number, required this.ai, required this.onTap, this.done = false, this.now = false, this.next = false});
  final int number;
  final bool ai;
  final VoidCallback onTap;
  final bool done;
  final bool now;
  final bool next;

  @override
  Widget build(BuildContext context) {
    final color = done ? const Color(0xFFAEB6C1) : (ai ? AppColors.ai : AppColors.primary);
    final size = now ? 36.0 : 30.0;
    final dot = Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(color: color, shape: BoxShape.circle, border: Border.all(color: Colors.white, width: 3), boxShadow: AppShadow.card),
      child: done
          ? const Icon(Icons.check, size: 14, color: Colors.white)
          : Text('$number', style: AppText.caption.copyWith(color: Colors.white, fontWeight: FontWeight.w700, fontSize: now ? 15 : 13)),
    );
    return GestureDetector(
      onTap: onTap,
      child: Center(
        child: now
            ? Container(width: 48, height: 48, alignment: Alignment.center, decoration: BoxDecoration(color: color.withValues(alpha: 0.18), shape: BoxShape.circle), child: dot)
            : next
                ? Container(padding: const EdgeInsets.all(2), decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: AppColors.primary, width: 2)), child: dot)
                : dot,
      ),
    );
  }
}

class _SelectedPin extends StatelessWidget {
  const _SelectedPin({this.number, this.icon, required this.label, required this.ai});
  final int? number;

  /// 번호 대신 아이콘 (관광지 레이어 장소).
  final IconData? icon;
  final String label;
  final bool ai;

  @override
  Widget build(BuildContext context) {
    final color = ai ? AppColors.ai : AppColors.primary;
    return Column(mainAxisSize: MainAxisSize.min, children: [
      Container(
        constraints: const BoxConstraints(maxWidth: 160),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(8), boxShadow: AppShadow.card),
        child: Text(label, style: AppText.caption.copyWith(color: AppColors.text, fontWeight: FontWeight.w700), overflow: TextOverflow.ellipsis),
      ),
      const SizedBox(height: 2),
      SizedBox(
        width: 64,
        height: 64,
        child: Stack(alignment: Alignment.center, children: [
          Container(width: 64, height: 64, decoration: BoxDecoration(color: color.withValues(alpha: 0.18), shape: BoxShape.circle)),
          Container(
            width: 40,
            height: 40,
            alignment: Alignment.center,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle, border: Border.all(color: Colors.white, width: 3), boxShadow: AppShadow.floating),
            child: icon != null ? Icon(icon, color: Colors.white, size: 20) : Text('$number', style: AppText.section.copyWith(color: Colors.white)),
          ),
        ]),
      ),
    ]);
  }
}

class _Toast extends StatelessWidget {
  const _Toast({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(color: AppColors.aiSoft.withValues(alpha: 0.95), borderRadius: BorderRadius.circular(18), boxShadow: AppShadow.card),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.auto_awesome, size: 14, color: AppColors.ai),
          const SizedBox(width: 6),
          Text(text, style: AppText.caption.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700)),
        ]),
      );
}

class _PlaceMapCard extends StatelessWidget {
  const _PlaceMapCard({required this.controller, required this.items, required this.item, this.category});
  final PlanController controller;
  final List<PlanItem> items;
  final PlanItem item;

  /// 펼쳤을 때 분류 줄 (장소 상세의 제목·분류 줄 대신, 2026-10-11).
  final String? category;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final idx = items.indexWhere((i) => i.id == item.id);
    final next = idx + 1 < items.length ? items[idx + 1] : null;
    final mode = (next?.legMode ?? c.plan?.travelMode) == 'car' ? '차량' : '도보';
    final vs = c.plan?.violationsFor(item.id) ?? const <Violation>[];
    final ai = c.aiUpdates.containsKey(item.id);
    final safety = c.safety?.visit(item.id);
    return GestureDetector(
      // 카드 좌우 스와이프로 이전/다음 장소
      onHorizontalDragEnd: (d) {
        final v = d.primaryVelocity ?? 0;
        if (v < -200 && idx + 1 < items.length) c.select(items[idx + 1].id);
        if (v > 200 && idx > 0) c.select(items[idx - 1].id);
      },
      child: Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
          Row(children: [
            Container(
              width: 28,
              height: 28,
              alignment: Alignment.center,
              decoration: BoxDecoration(color: ai ? AppColors.ai : AppColors.primary, shape: BoxShape.circle),
              child: Text('${idx + 1}', style: AppText.label.copyWith(color: Colors.white, fontWeight: FontWeight.w700)),
            ),
            const SizedBox(width: 10),
            Expanded(child: Text(item.title, style: AppText.headline, overflow: TextOverflow.ellipsis)),
            Text('${idx + 1} / ${items.length}', style: AppText.caption),
          ]),
          if (category != null) ...[
            const SizedBox(height: 4),
            Text(category!, key: const ValueKey('map-card-category'), style: AppText.meta, overflow: TextOverflow.ellipsis),
          ],
          const SizedBox(height: 6),
          Text('${item.startAt} – ${item.endAt} · ${durationKo(item.stayMinutes)}', style: AppText.meta),
          const SizedBox(height: 10),
          Wrap(spacing: 6, runSpacing: 6, children: [
            if (ai) InfoChip(label: 'AI 변경', icon: Icons.auto_awesome, fg: AppColors.ai, bg: AppColors.aiSoft),
            if (safety != null) RiskBadge(item: safety),
            for (final v in vs) v.isError ? InfoChip.error(v.shortLabel) : InfoChip.warning(v.shortLabel),
            if (next?.travelMinutes != null)
              InfoChip(label: '다음 장소 $mode ${next!.estimated ? '약 ' : ''}${next.travelMinutes}분', icon: mode == '차량' ? Icons.directions_car_outlined : Icons.directions_walk),
            if (next == null) const InfoChip(label: '오늘 마지막 장소', icon: Icons.flag_outlined),
          ]),
          const SizedBox(height: 14),
          Row(children: [
            Expanded(
              flex: 150,
              child: AppButton(label: '일정에서 보기', icon: Icons.calendar_month_outlined, kind: ButtonKind.soft, height: 44, onPressed: () => c.showInPlanner(item.id)),
            ),
            const SizedBox(width: 8),
            Expanded(
              flex: 80,
              child: AppButton(
                label: '교체',
                icon: Icons.swap_horiz,
                kind: ButtonKind.secondary,
                height: 44,
                onPressed: () => c.openAi(prefill: "Day ${item.day}의 '${item.title}' 대신 비슷한 시간에 갈 만한 다른 곳으로 바꿔줘", send: true),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              flex: 80,
              child: AppButton(
                label: '삭제',
                icon: Icons.delete_outline,
                kind: ButtonKind.danger,
                height: 44,
                onPressed: () async {
                  if (!await confirmDelete(context, item.title)) return;
                  try {
                    await c.removeItem(item.id);
                  } catch (e) {
                    if (context.mounted) showError(context, e);
                  }
                },
              ),
            ),
          ]),
        ]),
      ),
    );
  }
}

class _RouteSummary extends StatelessWidget {
  const _RouteSummary({required this.controller, required this.items, required this.showDelta});
  final PlanController controller;
  final List<PlanItem> items;
  final bool showDelta;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final day = c.currentDay;
    final total = day?.travelMinutes ?? 0;
    final before = c.lastApplied?.before.dayOf(c.day)?.travelMinutes;
    final delta = showDelta && before != null ? total - before : null;
    return Padding(
      padding: const EdgeInsets.only(top: 2),
      child: Row(children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            Text('Day ${c.day} 경로 · ${items.length}곳', style: AppText.section.copyWith(fontSize: 15)),
            const SizedBox(height: 2),
            Text.rich(
              TextSpan(style: AppText.meta, children: [
                TextSpan(text: '총 이동 $total분'),
                if (delta != null && delta != 0)
                  TextSpan(
                    text: ' (${delta > 0 ? '+' : ''}$delta분)',
                    style: TextStyle(color: delta > 0 ? AppColors.warning : AppColors.success, fontWeight: FontWeight.w700),
                  ),
                if (day?.endAt != null) TextSpan(text: ' · ${day!.endAt} 종료'),
                if (day?.items.any((i) => i.estimated) ?? false) const TextSpan(text: ' · 일부 구간 약'),
              ]),
            ),
            if (items.isEmpty) Text('일정을 추가하면 지도에 경로가 그려져요', style: AppText.caption),
          ]),
        ),
        if (showDelta)
          IntrinsicWidth(
            child: AppButton(
              label: '변경 내역',
              kind: ButtonKind.soft,
              height: 44,
              onPressed: () {
                final p = c.lastApplied?.proposal;
                if (p == null) return;
                final entry = ProposalEntry(p)..state = ProposalState.applied;
                final chat = context.read<ChatController>();
                // 시트는 앱 Navigator 에 따로 올라가 셸의 Provider 밖이다 → 카드가 읽는 두 컨트롤러를 다시 넘긴다
                showModalBottomSheet<void>(
                  context: context,
                  isScrollControlled: true,
                  showDragHandle: true,
                  builder: (ctx) => MultiProvider(
                    providers: [ChangeNotifierProvider.value(value: c), ChangeNotifierProvider.value(value: chat)],
                    child: SafeArea(
                      child: SingleChildScrollView(
                        padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                        child: ProposalCard(entry: entry, readOnly: true),
                      ),
                    ),
                  ),
                );
              },
            ),
          ),
      ]),
    );
  }
}

/// 시트 반·펼침: 그날 일정 한 줄 (누르면 그 장소 선택).
class _DayRow extends StatelessWidget {
  const _DayRow({required this.number, required this.item, required this.safety, required this.walked, required this.onTap});
  final int number;
  final PlanItem item;
  final SafetyItem? safety;
  final bool walked;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => InkWell(
        key: ValueKey('map-day-${item.id}'),
        borderRadius: BorderRadius.circular(10),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 2),
          child: Row(children: [
            MapDot(color: walked ? const Color(0xFFAEB6C1) : AppColors.primary, label: '$number', size: 26),
            const SizedBox(width: 10),
            SizedBox(width: 46, child: Text(item.startAt ?? '', style: AppText.meta.copyWith(fontWeight: FontWeight.w700, color: AppColors.text2))),
            Expanded(child: Text(item.title, style: AppText.label.copyWith(color: walked ? AppColors.text3 : AppColors.text), overflow: TextOverflow.ellipsis)),
            if (safety != null) RiskBadge(item: safety!, compact: true),
          ]),
        ),
      );
}

/// 자식 레이아웃 높이가 바뀌면 알려 준다 (Map 시트 접힘 높이).
class _MeasureHeight extends SingleChildRenderObjectWidget {
  const _MeasureHeight({required this.onHeight, required super.child});
  final ValueChanged<double> onHeight;

  @override
  RenderObject createRenderObject(BuildContext context) => _RenderMeasureHeight(onHeight);

  @override
  void updateRenderObject(BuildContext context, _RenderMeasureHeight renderObject) => renderObject.onHeight = onHeight;
}

class _RenderMeasureHeight extends RenderProxyBox {
  _RenderMeasureHeight(this.onHeight);
  ValueChanged<double> onHeight;


  @override
  void performLayout() {
    super.performLayout();
    final h = size.height;


    WidgetsBinding.instance.addPostFrameCallback((_) => onHeight(h));
  }
}
