import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../../core/models/safety.dart';
import '../../core/theme/app_theme.dart';

const guryongpo = LatLng(35.9890, 129.5560);

/// OpenStreetMap 타일 (키 불필요, 데모용). 지도 제공자가 정해지면 여기만 바꾼다.
TileLayer osmTiles() => TileLayer(urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', userAgentPackageName: 'com.guryongmate.guryong_mate');

/// 위험 구역 다각형 (28): Red/Amber 18% 채움 + 점선 테두리 + 이름 라벨.
class ZoneLayer extends StatelessWidget {
  const ZoneLayer({super.key, required this.zones, this.onTap, this.labels = true});
  final List<AvoidZone> zones;
  final ValueChanged<AvoidZone>? onTap;
  final bool labels;

  static Color colorOf(String risk) => risk == 'danger' ? AppColors.danger : AppColors.warning;

  @override
  Widget build(BuildContext context) {
    if (zones.isEmpty) return const SizedBox.shrink();
    return Stack(children: [
      PolygonLayer(polygons: [
        for (final z in zones)
          if (z.polygon.length > 2)
            Polygon(
              points: z.polygon,
              color: colorOf(z.risk).withValues(alpha: 0.18),
              borderColor: colorOf(z.risk),
              borderStrokeWidth: 1.5,
              pattern: StrokePattern.dashed(segments: const [6, 4]),
            ),
      ]),
      if (labels)
        MarkerLayer(markers: [
          for (final z in zones)
            if (z.polygon.isNotEmpty)
              Marker(
                point: _center(z.polygon),
                width: 160,
                height: 28,
                child: GestureDetector(
                  onTap: onTap == null ? null : () => onTap!(z),
                  child: Center(child: MapLabel(text: '${_short(z.name)} · ${z.risk == 'danger' ? '위험' : '주의'}', color: colorOf(z.risk))),
                ),
              ),
        ]),
    ]);
  }

  static String _short(String name) => name.replaceAll('_스마트맨홀', '').replaceAll('_지표면', '').replaceAll('_하천수위계', '');

  static LatLng _center(List<LatLng> pts) {
    var lat = 0.0, lng = 0.0;
    for (final p in pts) {
      lat += p.latitude;
      lng += p.longitude;
    }
    return LatLng(lat / pts.length, lng / pts.length);
  }
}

/// 원형 핀 (번호 또는 아이콘).
class MapDot extends StatelessWidget {
  const MapDot({super.key, required this.color, this.label, this.icon, this.size = 30});
  final Color color;
  final String? label;
  final IconData? icon;
  final double size;

  @override
  Widget build(BuildContext context) => Container(
        width: size,
        height: size,
        alignment: Alignment.center,
        decoration: BoxDecoration(color: color, shape: BoxShape.circle, border: Border.all(color: Colors.white, width: 3), boxShadow: AppShadow.card),
        child: icon != null
            ? Icon(icon, size: size * 0.45, color: Colors.white)
            : Text(label ?? '', style: AppText.caption.copyWith(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 13)),
      );
}

class MapLabel extends StatelessWidget {
  const MapLabel({super.key, required this.text, this.color = AppColors.text});
  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(8), boxShadow: AppShadow.card),
        child: Text(text, style: AppText.caption.copyWith(color: color, fontWeight: FontWeight.w700), overflow: TextOverflow.ellipsis),
      );
}
