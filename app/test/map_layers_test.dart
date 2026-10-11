// 사용자 위치 마커 보간 (UserLocationLayer): 새 위치로 옮겨 가고, 멀리 뛰면 바로.
import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/features/map/map_layers.dart';
import 'package:latlong2/latlong.dart';

const _dot = ValueKey('dot');
const _a = LatLng(35.9890, 129.5560);
const _b = LatLng(35.9890, 129.5565); // 동쪽 약 45m
const _far = LatLng(35.9990, 129.5560); // 북쪽 약 1.1km (순간이동)

Widget _map(LatLng p, {double? accuracy, double zoom = 18}) => MaterialApp(
      home: SizedBox(
        width: 400,
        height: 400,
        child: FlutterMap(
          options: MapOptions(initialCenter: _a, initialZoom: zoom),
          children: [UserLocationLayer(point: p, accuracy: accuracy, dotKey: _dot)],
        ),
      ),
    );

void main() {
  testWidgets('새 위치를 받으면 지금 그려진 곳에서 약 1초 동안 옮겨 간다 · 정확도 원도 같이', (tester) async {
    await tester.pumpWidget(_map(_a, accuracy: 10));
    final start = tester.getCenter(find.byKey(_dot));
    LatLng circle() => tester.widget<CircleLayer>(find.byType(CircleLayer)).circles.single.point;
    expect(circle(), _a);

    await tester.pumpWidget(_map(_b, accuracy: 10));
    final end = start + const Offset(1, 0) * 1000; // 방향만 (오른쪽)
    await tester.pump(const Duration(milliseconds: 300));
    final mid = tester.getCenter(find.byKey(_dot));
    expect(mid.dx, greaterThan(start.dx + 2), reason: '움직이기 시작');
    expect(circle().longitude, greaterThan(_a.longitude));
    expect(circle().longitude, lessThan(_b.longitude), reason: '아직 가는 중 (점과 같은 위치)');
    await tester.pump(const Duration(milliseconds: 800));
    final done = tester.getCenter(find.byKey(_dot));
    expect(done.dx, greaterThan(mid.dx));
    expect(circle(), _b);
    expect(end.dx, greaterThan(done.dx));

    // 가는 중에 다음 위치가 오면 그 자리에서 이어서 (처음으로 튀지 않는다)
    await tester.pumpWidget(_map(_a, accuracy: 10));
    await tester.pump(const Duration(milliseconds: 200));
    final back = tester.getCenter(find.byKey(_dot)).dx;
    await tester.pumpWidget(_map(_b, accuracy: 10));
    await tester.pump(const Duration(milliseconds: 16));
    expect((tester.getCenter(find.byKey(_dot)).dx - back).abs(), lessThan(3), reason: '그려진 곳에서 이어서');
    await tester.pumpAndSettle();
  });

  testWidgets('300m 넘게 뛰면(순간이동) 애니메이션 없이 바로', (tester) async {
    await tester.pumpWidget(_map(_a, zoom: 14));
    await tester.pumpWidget(_map(_far, zoom: 14));
    await tester.pump();
    final at = tester.getCenter(find.byKey(_dot));
    await tester.pump(const Duration(seconds: 1));
    expect(tester.getCenter(find.byKey(_dot)), at, reason: '이미 도착해 있다');
    expect(tester.hasRunningAnimations, isFalse);
  });
}
