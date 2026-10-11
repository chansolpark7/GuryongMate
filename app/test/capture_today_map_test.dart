// Today · Map 역할 나누기 (docs/app/today-map-redesign.md) 변경 전·후 비교 캡처. 여행 재생 fixture 로 같은 장면을 그려
// docs/app/review/today-map/{before|after}/*.png (390×844) 로 저장한다. 지도 타일은 네트워크가 없어 비어 있다.
//   flutter test test/capture_today_map_test.dart --dart-define=CAPTURE=true --dart-define=CAPTURE_DIR=../docs/app/review/today-map/after
// 변경 전 코드에는 없는 조작(작은 창 [설명 듣기], Map 시트 끌기)은 화면에 그 버튼이 있을 때만 찍는다.
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/widgets/common.dart';
import 'package:guryong_mate/features/planner/plan_controller.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';
import 'package:guryong_mate/features/trip/trip_controller.dart';
import 'package:provider/provider.dart';

import 'capture_test.dart' show loadFonts;
import 'support/fixture_gateway.dart';
import 'support/harness.dart';
import 'support/trip_replay.dart';

const capture = bool.fromEnvironment('CAPTURE');
const outDir = String.fromEnvironment('CAPTURE_DIR', defaultValue: '../docs/app/review/today-map/after');
final _root = GlobalKey();

void captureTest(String name, WidgetTesterCallback body) => testWidgets(name, body, skip: !capture);

Future<void> flush(WidgetTester tester, [int times = 6]) async {
  for (var i = 0; i < times; i++) {
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 30)));
    await tester.pump(const Duration(milliseconds: 60));
  }
}

Future<void> snap(WidgetTester tester, String name) async {
  await flush(tester);
  await tester.pump(const Duration(milliseconds: 400));
  final boundary = _root.currentContext!.findRenderObject()! as RenderRepaintBoundary;
  await tester.runAsync(() async {
    final image = await boundary.toImage(pixelRatio: 1);
    final png = await image.toByteData(format: ui.ImageByteFormat.png);
    final file = File('$outDir/$name.png');
    await file.parent.create(recursive: true);
    await file.writeAsBytes(png!.buffer.asUint8List());
  });
}

Element _shell(WidgetTester tester) => tester.element(find.byType(IndexedStack, skipOffstage: false).first);
TripController tripOf(WidgetTester tester) => Provider.of<TripController>(_shell(tester), listen: false);
PlanController planOf(WidgetTester tester) => Provider.of<PlanController>(_shell(tester), listen: false);

/// 길 찾기 대역: 정류장 사이 직선 + 가운데 점, steps 요청이면 회전 안내 3개.
Res fakeRoute(Req q) {
  final b = q.body as Map;
  final st = (b['stops'] as List).cast<Map>();
  final (lat0, lng0, lat1, lng1) = (st[0]['lat'] as num, st[0]['lng'] as num, st.last['lat'] as num, st.last['lng'] as num);
  return Res.json(200, {
    'mode': b['mode'] ?? 'walk', 'source': 'road', 'minutes': 8, 'distance_m': 600,
    'geometry': {'type': 'LineString', 'coordinates': [
      [lng0, lat0], [lng0 + (lng1 - lng0) * 0.3, lat0 + (lat1 - lat0) * 0.6], [lng1, lat1],
    ]},
    if (b['steps'] == true)
      'legs': [{'steps': [
        {'type': 'start', 'instruction': '출발', 'distance_m': 0, 'begin_index': 0},
        {'type': 'left', 'instruction': '구룡포길에서 왼쪽으로', 'distance_m': 300, 'begin_index': 1},
        {'type': 'arrive', 'instruction': '도착', 'distance_m': 0, 'begin_index': 2},
      ]}],
  });
}

TripReplay scene(String name, {List<String> flows = const []}) {
  final r = TripReplay(name, flows: flows);
  r.gw.on('POST /routing/route', fakeRoute);
  r.gw.on('GET /places/{id}', (q) => Res.json(200, {
        'id': q.path.split('/').last, 'title': '구룡포항', 'content_type': '관광지', 'category_names': ['자연', '항구'], 'tags': [],
        'lat': 35.9894, 'lng': 129.5574, 'addr': '경상북도 포항시 남구 구룡포읍 구룡포리', 'area': '구룡포읍',
        'open_hours': '상시', 'rest_date': '연중무휴', 'fee': '무료', 'parking': '가능 (공영주차장)', 'tel': '054-270-5000',
        'overview': '동해안 최대 어항 중 하나로, 대게와 과메기가 유명하다. 이른 아침 경매 풍경과 항구를 따라 이어진 수산물 시장을 볼 수 있다.',
        'images': [], 'image_url': '', 'story_count': 0,
      }));
  return r;
}

Future<void> open(WidgetTester tester, TripReplay r) async {
  await phone(tester);
  final s = await signedInSession(r.gw, prefs: {'demo_location_mode': true});
  await tester.pumpWidget(RepaintBoundary(key: _root, child: app(s, PlannerShell(planId: r.planId, locationSource: FakeLocationSource()))));
  await flush(tester, 10);
}

Future<void> poll(WidgetTester tester, [int seconds = 5]) async {
  await tester.pump(Duration(seconds: seconds));
  await flush(tester, 8);
}

/// 보이는 화면의 지도에서 번호 핀 [n].
Finder pin(String n) => find.descendant(of: find.byType(FlutterMap).hitTestable(), matching: find.text(n)).hitTestable();

/// 보이는 시트의 손잡이를 끈다 (변경 전 Map 카드는 손잡이 그림만 있어 움직이지 않는다).
Future<void> dragSheet(WidgetTester tester, double dy) async {
  final g = find.byType(Grabber).hitTestable();
  if (g.evaluate().isEmpty) return;
  await tester.drag(g.last, Offset(0, dy));
  await flush(tester, 8);
}

void main() {
  setUpHarness();
  setUpAll(() async {
    if (capture) await loadFonts();
  });

  captureTest('a 여행 시작 직후 (첫 장소 전)', (tester) async {
    final r = scene('scene2_arrival', flows: ['f4_arrival_docent.json']);
    r.advanceTo('12:30:30');
    await open(tester, r);
    await snap(tester, 'a1_before_first');
    await dragSheet(tester, -330);
    await snap(tester, 'a2_before_first_panel');
  });

  captureTest('b 이동 중 (자동 길 안내)', (tester) async {
    final r = scene('scene4_safety', flows: ['f6_safety.json']);
    r.gw.on('POST /routing/route', fakeRoute);
    r.advanceTo('13:35:24');
    await open(tester, r);
    await poll(tester);
    await snap(tester, 'b1_moving');
  });

  captureTest('c 머무는 중 → 핀 → 설명 듣기', (tester) async {
    final r = scene('scene2_arrival', flows: ['f4_arrival_docent.json']);
    r.advanceTo('12:30:30');
    await open(tester, r);
    r.advanceTo('12:33:00');
    await poll(tester);
    tripOf(tester).dismissArrival();
    await flush(tester);
    await snap(tester, 'c1_visiting');
    final keyed = find.byKey(const ValueKey('today-pin-503'));
    await tester.tap(keyed.evaluate().isNotEmpty ? keyed : pin('2').first);
    await flush(tester, 10);
    await snap(tester, 'c2_pin_tap');
    final listen = find.text('설명 듣기').hitTestable();
    if (listen.evaluate().isNotEmpty) {
      await tester.tap(listen.first);
      await flush(tester, 12);
      await snap(tester, 'c3_listen_guide_note');
    }
  });

  captureTest('d Map (동행 중 · 장소 선택 · 시트)', (tester) async {
    final r = scene('scene2_arrival', flows: ['f4_arrival_docent.json']);
    r.advanceTo('12:30:30');
    await open(tester, r);
    r.advanceTo('12:33:00');
    await poll(tester);
    tripOf(tester).dismissArrival();
    planOf(tester).setTab(1);
    await flush(tester, 10);
    await snap(tester, 'd1_map_trip');
    await dragSheet(tester, -300);
    await snap(tester, 'd2_map_day_list');
    planOf(tester).select(503);
    await flush(tester, 10);
    await snap(tester, 'd3_map_selected');
    await dragSheet(tester, -600);
    await snap(tester, 'd4_map_selected_expanded');
  });

  // 2026-10-11 겹치는 버튼 정리 (docs/app/review/tidy): 도착 직후 카드 · 남은 일정 머리 · 오른쪽 끝 핀 작은 창 · Map 시트 펼침
  captureTest('e 정리 (도착 카드 · 머리 링크 · 작은 창 자리 · 시트 제목)', (tester) async {
    final r = scene('scene2_arrival', flows: ['f4_arrival_docent.json']);
    r.advanceTo('12:30:30');
    await open(tester, r);
    r.advanceTo('12:33:00');
    await poll(tester);
    await flush(tester, 10);
    await snap(tester, 'e1_arrived');
    tripOf(tester).dismissArrival();
    await flush(tester);
    final pin2 = find.byKey(const ValueKey('today-pin-503'));
    await tester.tap(pin2.evaluate().isNotEmpty ? pin2 : pin('2').first);
    await flush(tester, 10);
    // 지도를 끌어 핀을 오른쪽 AI·현재 위치 버튼 바로 아래로
    final from = tester.getCenter(pin2.evaluate().isNotEmpty ? pin2 : pin('2').first);
    final to = Offset(346, 394);
    final g = await tester.startGesture(const Offset(30, 200));
    for (var i = 1; i <= 10; i++) {
      await g.moveTo(const Offset(30, 200) + (to - from) * (i / 10));
      await tester.pump(const Duration(milliseconds: 16));
    }
    await tester.pump(const Duration(milliseconds: 500));
    await g.up();
    await flush(tester, 10);
    await snap(tester, 'e2_pin_right_edge');
    planOf(tester).setTab(1);
    planOf(tester).select(503);
    await flush(tester, 10);
    await snap(tester, 'e3_map_selected');
    await dragSheet(tester, -600);
    await snap(tester, 'e4_map_selected_expanded');
  });
}
