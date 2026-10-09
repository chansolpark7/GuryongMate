import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../../core/models/safety.dart';
import '../../core/models/trip.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../map/map_layers.dart';
import 'trip_controller.dart';
import 'trip_overlay.dart';

/// 23 [피해 가는 길 보기] → 28 우회 (F7): `POST /routing/route {mode, stops:[현재, 다음 장소], hazards:"avoid", steps:true}`.
/// 지금 위치에서 다음 장소까지 원래 길(회색 점선)과 피해 가는 길(Primary)을 그리고 시간 차이를 보여 준다.
Future<void> showDetourSheet(BuildContext context, TripController t, TripEvent event) => showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => _DetourSheet(t: t, event: event),
    );

class _DetourSheet extends StatefulWidget {
  const _DetourSheet({required this.t, required this.event});
  final TripController t;
  final TripEvent event;

  @override
  State<_DetourSheet> createState() => _DetourSheetState();
}

class _DetourSheetState extends State<_DetourSheet> {
  RouteResult? _avoid;
  RouteResult? _base;
  bool _loading = true;
  String? _error;

  TripController get t => widget.t;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final loc = t.state?.location;
    final next = t.item(t.state?.nextItemId);
    if (loc == null || next?.place == null) {
      setState(() {
        _loading = false;
        _error = loc == null ? '지금 위치를 아직 받지 못했어요' : '다음 장소가 없어요';
      });
      return;
    }
    final mode = next!.legMode ?? t.plan.plan?.travelMode ?? 'walk';
    final stops = [(loc.lat, loc.lng), (next.place!.lat, next.place!.lng)];
    try {
      final r = await Future.wait([
        t.api.route(mode, stops, hazards: 'avoid', steps: true),
        t.api.route(mode, stops, hazards: 'report'),
      ]);
      _avoid = r[0];
      _base = r[1];
    } catch (_) {
      _error = '길을 찾지 못했어요. 물이 고인 길은 피해서 가 주세요';
    }
    if (mounted) setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    final next = t.item(t.state?.nextItemId);
    final loc = t.state?.location;
    final h = _avoid?.hazards;
    final zoneIds = widget.event.zones.map((z) => z.id).toSet();
    final zones = t.zones.where((z) => zoneIds.isEmpty || zoneIds.contains(z.id)).toList();
    String dist(int? m) => m == null ? '' : (m >= 1000 ? '${(m / 1000).toStringAsFixed(1)}km' : '${m}m');
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            const Icon(Icons.alt_route, color: AppColors.primary, size: 20),
            const SizedBox(width: 6),
            Text('위험 구역을 피해 가는 길', style: AppText.section),
          ]),
          if (next != null) Text('지금 위치 → ${next.title} · ${next.legMode == 'car' ? '차량' : '도보'}', style: AppText.meta),
          const SizedBox(height: 10),
          if (loc != null)
            ClipRRect(
              borderRadius: BorderRadius.circular(14),
              child: SizedBox(
                height: 200,
                child: FlutterMap(
                  options: MapOptions(
                    initialCameraFit: CameraFit.coordinates(
                      coordinates: [LatLng(loc.lat, loc.lng), if (next?.place != null) LatLng(next!.place!.lat, next.place!.lng), ...?_avoid?.points],
                      padding: const EdgeInsets.all(28),
                      maxZoom: 17,
                    ),
                  ),
                  children: [
                    osmTiles(),
                    ZoneLayer(zones: zones),
                    PolylineLayer(polylines: [
                      if (_base != null && _base!.points.length > 1)
                        Polyline(points: _base!.points, strokeWidth: 4, color: AppColors.text3, pattern: StrokePattern.dashed(segments: const [8, 6])),
                      if (_avoid != null && _avoid!.points.length > 1)
                        Polyline(points: _avoid!.points, strokeWidth: 5, color: AppColors.primary, borderStrokeWidth: 2, borderColor: Colors.white),
                    ]),
                    MarkerLayer(markers: [
                      Marker(
                        point: LatLng(loc.lat, loc.lng),
                        width: 20,
                        height: 20,
                        child: Container(decoration: BoxDecoration(color: AppColors.primary, shape: BoxShape.circle, border: Border.all(color: Colors.white, width: 3))),
                      ),
                      if (next?.place != null) Marker(point: LatLng(next!.place!.lat, next.place!.lng), width: 28, height: 28, child: const MapDot(color: AppColors.primary, icon: Icons.flag, size: 28)),
                    ]),
                  ],
                ),
              ),
            ),
          const SizedBox(height: 10),
          if (_loading)
            const LinearProgressIndicator(minHeight: 2)
          else if (_error != null)
            Text(_error!, style: AppText.caption.copyWith(color: AppColors.warning))
          else if (h == null || !h.checked)
            Text('안전 정보를 확인하지 못했어요 (안전하다는 뜻이 아니에요)', style: AppText.caption.copyWith(color: AppColors.warning))
          else ...[
            Row(children: [
              Expanded(child: _Box(title: '원래 길', value: '${h.baselineMinutes ?? _base?.minutes ?? '-'}분 · ${dist(h.baselineDistanceM ?? _base?.distanceM)}', strike: true)),
              const SizedBox(width: 8),
              Expanded(child: _Box(title: '피해 가는 길', value: '${_avoid!.minutes}분 · ${dist(_avoid!.distanceM)}${h.detourMinutes == null || h.detourMinutes == 0 ? '' : ' (+${h.detourMinutes}분)'}', primary: true)),
            ]),
            const SizedBox(height: 8),
            Text(h.avoided.isEmpty ? '피할 구역이 경로에 없어요' : '${h.avoided.map(shortZoneName).join(', ')}을(를) 지나지 않아요', style: AppText.caption),
            if (h.note != null) Text('${h.note} 물이 고인 길은 피해서 가 주세요.', style: AppText.caption.copyWith(color: AppColors.warning)),
          ],
          const SizedBox(height: 12),
          AppButton(label: '이 길로 안내', icon: Icons.near_me_outlined, onPressed: () => Navigator.of(context).pop()),
        ]),
      ),
    );
  }
}

class _Box extends StatelessWidget {
  const _Box({required this.title, required this.value, this.strike = false, this.primary = false});
  final String title;
  final String value;
  final bool strike;
  final bool primary;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: primary ? AppColors.primarySoft : AppColors.background, borderRadius: BorderRadius.circular(12)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: AppText.caption.copyWith(color: primary ? AppColors.primary : AppColors.text2)),
          Text(value, style: AppText.label.copyWith(fontWeight: FontWeight.w700, color: primary ? AppColors.primary : AppColors.text3, decoration: strike ? TextDecoration.lineThrough : null)),
        ]),
      );
}
