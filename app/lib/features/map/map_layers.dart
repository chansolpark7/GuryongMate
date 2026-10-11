import 'dart:async';

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../../core/models/safety.dart';
import '../../core/theme/app_theme.dart';
import '../trip/location.dart' show distanceM;

const guryongpo = LatLng(35.9890, 129.5560);

/// OpenStreetMap 타일 (키 불필요, 데모용). 지도 제공자가 정해지면 여기만 바꾼다.
TileLayer osmTiles() => TileLayer(urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', userAgentPackageName: 'com.guryongmate.guryong_mate');

/// 지도 카메라 맞춤·이동을 프레임 뒤에. 웹(CanvasKit)에서는 프레임 뒤 콜백 안에서 바로 옮기면 새 카메라의 타일을 받고도
/// 지도가 옛 카메라로 남아 회색으로 보였다 (손으로 움직이면 보임) → 웹만 다음 이벤트 루프로 한 번 더 미룬다.
void afterFrame(VoidCallback f) => WidgetsBinding.instance.addPostFrameCallback((_) => deferOnWeb(f));

/// [afterFrame] 의 웹 쪽: 웹이면 [Timer.run], 앱은 바로 (onMapReady 도 프레임 뒤 콜백이다).
void deferOnWeb(VoidCallback f) => kIsWeb ? Timer.run(f) : f();

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
    return Stack(
      children: [
        PolygonLayer(
          polygons: [
            for (final z in zones)
              if (z.polygon.length > 2)
                Polygon(
                  points: z.polygon,
                  color: colorOf(z.risk).withValues(alpha: 0.18),
                  borderColor: colorOf(z.risk),
                  borderStrokeWidth: 1.5,
                  pattern: StrokePattern.dashed(segments: const [6, 4]),
                ),
          ],
        ),
        if (labels)
          MarkerLayer(
            markers: [
              for (final z in zones)
                if (z.polygon.isNotEmpty)
                  Marker(
                    point: _center(z.polygon),
                    width: 160,
                    height: 28,
                    child: GestureDetector(
                      onTap: onTap == null ? null : () => onTap!(z),
                      child: Center(
                        child: MapLabel(text: '${_short(z.name)} · ${z.risk == 'danger' ? '위험' : '주의'}', color: colorOf(z.risk)),
                      ),
                    ),
                  ),
            ],
          ),
      ],
    );
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
    decoration: BoxDecoration(
      color: color,
      shape: BoxShape.circle,
      border: Border.all(color: Colors.white, width: 3),
      boxShadow: AppShadow.card,
    ),
    child: icon != null
        ? Icon(icon, size: size * 0.45, color: Colors.white)
        : Text(
            label ?? '',
            style: AppText.caption.copyWith(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 13),
          ),
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
    child: Text(
      text,
      style: AppText.caption.copyWith(color: color, fontWeight: FontWeight.w700),
      overflow: TextOverflow.ellipsis,
    ),
  );
}

/// 사용자 위치 (점 + 정확도 원). Today·우회 안내 등 사용자 마커를 그리는 곳은 모두 이것으로.
/// 새 위치를 받으면 지금 그려진 곳에서 [duration](≈ 위치 갱신 주기) 동안 ease-out 으로 옮겨 간다 — 데모 재생기가 1초에 25m 씩 보내도
/// 뚝뚝 끊기지 않게. [jumpMeters] 이상 떨어진 곳(순간이동)은 애니메이션 없이 바로.
class UserLocationLayer extends StatefulWidget {
  const UserLocationLayer({super.key, required this.point, this.accuracy, this.duration = const Duration(seconds: 1), this.size = 22, this.dotKey});
  final LatLng point;

  /// 정확도 원 반지름(m). null 이면 원 없이 점만.
  final double? accuracy;
  final Duration duration;
  final double size;
  final Key? dotKey;

  static const jumpMeters = 300.0;

  @override
  State<UserLocationLayer> createState() => _UserLocationLayerState();
}

class _UserLocationLayerState extends State<UserLocationLayer> with SingleTickerProviderStateMixin {
  late final _anim = AnimationController(vsync: this, value: 1);
  late LatLng _from = widget.point, _to = widget.point;

  /// 지금 그려진 위치.
  LatLng get _shown {
    final t = Curves.easeOut.transform(_anim.value);
    return LatLng(_from.latitude + (_to.latitude - _from.latitude) * t, _from.longitude + (_to.longitude - _from.longitude) * t);
  }

  @override
  void didUpdateWidget(UserLocationLayer old) {
    super.didUpdateWidget(old);
    final p = widget.point;
    if (p == old.point) return;
    final cur = _shown;
    if (widget.duration == Duration.zero || distanceM(cur.latitude, cur.longitude, p.latitude, p.longitude) >= UserLocationLayer.jumpMeters) {
      _anim.stop();
      _from = _to = p;
      _anim.value = 1;
      return;
    }
    _from = cur;
    _to = p;
    _anim.duration = widget.duration;
    _anim.forward(from: 0);
  }

  @override
  void dispose() {
    _anim.dispose();
    super.dispose();
  }

  // 지금 머무는 곳 핀 위에 점이 겹쳐도 핀을 누를 수 있게 (점은 누르는 대상이 아니다)
  @override
  Widget build(BuildContext context) => IgnorePointer(
    child: AnimatedBuilder(
      animation: _anim,
      builder: (context, _) {
        final at = _shown, acc = widget.accuracy;
        return Stack(
          children: [
            if (acc != null)
              CircleLayer(
                circles: [
                  CircleMarker(
                    point: at,
                    radius: acc,
                    useRadiusInMeter: true,
                    color: AppColors.primary.withValues(alpha: 0.15),
                    borderColor: AppColors.primary.withValues(alpha: 0.3),
                    borderStrokeWidth: 1,
                  ),
                ],
              ),
            MarkerLayer(
              markers: [
                Marker(
                  point: at,
                  width: widget.size,
                  height: widget.size,
                  child: Container(
                    key: widget.dotKey,
                    decoration: BoxDecoration(
                      color: AppColors.primary,
                      shape: BoxShape.circle,
                      border: Border.all(color: Colors.white, width: 3),
                      boxShadow: AppShadow.card,
                    ),
                  ),
                ),
              ],
            ),
          ],
        );
      },
    ),
  );
}
