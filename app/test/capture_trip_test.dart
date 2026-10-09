// L2 Figma 대조용 동행 화면 캡처 (docs/app/review/l2.md). 여행 재생 fixture(trip/scene2·3·4)와 f3~f6 로
// Figma 와 같은 장면을 그려 docs/app/review/l2/app/fx_*.png (390×844) 로 저장한다. 글꼴은 맑은 고딕, 지도 타일은 네트워크가 없어 비어 있다.
//   flutter test test/capture_trip_test.dart --dart-define=CAPTURE=true
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/app_session.dart';
import 'package:guryong_mate/features/chat/chat_controller.dart';
import 'package:guryong_mate/features/planner/plan_controller.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';
import 'package:guryong_mate/features/trip/location.dart';
import 'package:guryong_mate/features/trip/trip_controller.dart';
import 'package:provider/provider.dart';

import 'capture_test.dart' show loadFonts;
import 'support/fixture_gateway.dart';
import 'support/harness.dart';
import 'support/trip_replay.dart';

const capture = bool.fromEnvironment('CAPTURE');
const outDir = '../docs/app/review/l2/app';
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
    final file = File('$outDir/fx_$name.png');
    await file.parent.create(recursive: true);
    await file.writeAsBytes(png!.buffer.asUint8List());
  });
}

Future<AppSession> open(WidgetTester tester, FixtureGateway gw, int planId, {bool demo = true, FakeLocationSource? source, bool startTrip = false}) async {
  await phone(tester);
  final s = await signedInSession(gw, prefs: {'demo_location_mode': demo});
  await tester.pumpWidget(RepaintBoundary(key: _root, child: app(s, PlannerShell(planId: planId, locationSource: source ?? FakeLocationSource(), startTrip: startTrip))));
  await flush(tester, 10);
  return s;
}

Future<void> poll(WidgetTester tester, [int seconds = 5]) async {
  await tester.pump(Duration(seconds: seconds));
  await flush(tester, 8);
}

Element _shell(WidgetTester tester) => tester.element(find.byType(IndexedStack, skipOffstage: false).first);
TripController tripOf(WidgetTester tester) => Provider.of<TripController>(_shell(tester), listen: false);
ChatController chatOf(WidgetTester tester) => Provider.of<ChatController>(_shell(tester), listen: false);
PlanController planOf(WidgetTester tester) => Provider.of<PlanController>(_shell(tester), listen: false);

Future<void> waitChat(WidgetTester tester) async {
  for (var i = 0; i < 200 && chatOf(tester).busy; i++) {
    await flush(tester, 1);
  }
  await flush(tester);
}

/// 장면 ④ 의 안전 점검(f6)을 Today·카드 배지에 쓴다.
TripReplay scene4() {
  final r = TripReplay('scene4_safety', flows: ['f6_safety.json']);
  r.gw.on('GET /planner/plans/{id}/safety', (_) => Res.json(200, r.flowResponse('compare_before_safety')));
  r.gw.on('POST /planner/plans/{id}/safety/preview', (_) => Res.json(200, r.flowResponse('compare_after_safety')));
  return r;
}

void main() {
  setUpHarness();
  setUpAll(() async {
    if (capture) await loadFonts();
  });

  captureTest('11 · 12 · 13 · 14-S2', (tester) async {
    final r = scene4();
    final started = r.timeline.firstWhere((e) => '${e['route']}'.endsWith('/start'))['response'] as Map<String, dynamic>;
    var first = true;
    r.gw.on('POST /trip/plans/{id}/start', (_) {
      if (first) {
        first = false;
        return Res.error(409, 'not_trip_day', '', {'start_date': '2026-10-17', 'end_date': '2026-10-17'});
      }
      r.state = Map.of(started);
      return Res.json(200, started);
    });
    await open(tester, r.gw, r.planId, demo: false, source: FakeLocationSource(access: LocationAccess.denied), startTrip: true);
    await snap(tester, '11_trip_start');
    await tester.tap(find.text('여행 시작').last);
    await flush(tester, 10);
    await snap(tester, '13_location');
    await tester.tap(find.text('위치 없이 계속'));
    await flush(tester, 10);
    await snap(tester, '12_not_trip_day');
    await tester.tap(find.text('일정 둘러보기만 할게요'));
    await flush(tester);
    // 데모 위치 모드로 다시 시작 → 14-S2
    await tripOf(tester).session.setDemoLocationMode(true);
    await tester.tap(find.byTooltip('더보기'));
    await flush(tester);
    await tester.tap(find.text('여행 시작').last);
    await flush(tester, 5);
    await tester.tap(find.text('여행 시작').last);
    await flush(tester, 10);
    r.advanceTo('12:30:30');
    await poll(tester, 15);
    await snap(tester, '14s2_idle_demo');
  });

  captureTest('14 · 14-S1 · 15 (장면 ④ 데이터)', (tester) async {
    final r = scene4();
    r.advanceTo('12:40:00');
    final gps = FakeLocationSource(access: LocationAccess.denied);
    await open(tester, r.gw, r.planId, source: gps);
    tripOf(tester).dismissArrival();
    await flush(tester);
    await snap(tester, '14_today');
    // 14-S1: 위치 꺼짐 + 오프라인
    final t = tripOf(tester);
    await t.session.setDemoLocationMode(false);
    await flush(tester);
    t.offline = true; // 폴링이 네트워크 오류였던 상태
    t.notifyListeners();
    await flush(tester, 2);
    await snap(tester, '14s1_offline');
    await t.session.setDemoLocationMode(true);
    t.offline = false;
    r.gw.on('POST /routing/route', (_) => Res.error(503, 'routing_unavailable')); // f6 우회 경로(차량)는 이 구간 것이 아니다
    r.advanceTo('13:35:24'); // 13:33:31 departed → 이동 중
    await poll(tester, 5);
    await snap(tester, '15_moving');
  });

  captureTest('16 · 17 · 18 (장면 ②)', (tester) async {
    final r = TripReplay('scene2_arrival', flows: ['f4_arrival_docent.json']);
    r.advanceTo('12:30:30');
    await open(tester, r.gw, r.planId);
    r.advanceTo('12:33:00');
    await poll(tester);
    await snap(tester, '16_arrival');
    await tester.tap(find.text('짧게'));
    await flush(tester, 10);
    await snap(tester, '17_guide_note');
    await tester.scrollUntilVisible(find.text('출처'), 200, scrollable: find.byType(Scrollable).first);
    await flush(tester);
    await tester.tap(find.text('집들은 언제 지어졌어요?'));
    await flush(tester, 10);
    await waitChat(tester);
    await tester.enterText(find.byType(TextField).last, '다음부터는 더 짧게 해줘요');
    await tester.pump();
    await tester.tap(find.byTooltip('보내기').last);
    await flush(tester);
    await waitChat(tester);
    await tester.drag(find.byType(DraggableScrollableSheet).last, const Offset(0, -500));
    await flush(tester);
    await snap(tester, '18_docent_chat');
  });

  captureTest('16-S1 (노트 없음)', (tester) async {
    final r = TripReplay('scene2_arrival');
    r.advanceTo('12:30:30');
    await open(tester, r.gw, r.planId);
    r.advanceTo('12:33:00');
    await poll(tester);
    await snap(tester, '16s1_no_note');
  });

  captureTest('14-S3 · 19 · 20 · 21-S1 (장면 ③)', (tester) async {
    final r = TripReplay('scene3_delay', flows: ['f5_delay.json']);
    r.advanceTo('13:50:00');
    await open(tester, r.gw, r.planId);
    tripOf(tester).dismissArrival();
    planOf(tester).setTab(0);
    r.advanceTo('14:00:00');
    await poll(tester);
    await snap(tester, '14s3_fab_preview');
    await tester.tap(find.text('보기 ›'));
    await flush(tester, 15);
    await snap(tester, '19_proactive_delay');
    await tester.tap(find.text('구룡포과메기문화관은 유지해줘'));
    await flush(tester);
    await waitChat(tester);
    await snap(tester, '20_revised');
    final cmp = find.text('비교 보기 ›').last;
    await tester.ensureVisible(cmp);
    await tester.tap(cmp);
    await flush(tester, 15);
    await snap(tester, '21s1_compare_delay');
  });

  captureTest('22 · 23 · 24 · 30 (장면 ④)', (tester) async {
    final r = scene4();
    r.advanceTo('15:16:30');
    await open(tester, r.gw, r.planId);
    tripOf(tester).dismissArrival();
    r.advanceTo('15:22:00');
    await poll(tester);
    await tester.tap(find.text('보기 ›'));
    await flush(tester, 15);
    await snap(tester, '22_proactive_safety');
    await tester.tap(find.descendant(of: find.byType(DraggableScrollableSheet).last, matching: find.byTooltip('닫기')));
    await flush(tester);
    r.advanceTo('16:34:07');
    await poll(tester);
    tripOf(tester).dismissArrival();
    await flush(tester);
    await snap(tester, '23_hazard');
    await tester.tap(find.text('확인'));
    await flush(tester);
    await snap(tester, '23_hazard_collapsed');
    await tester.tap(find.byTooltip('알림'));
    await flush(tester, 15);
    await snap(tester, '24_inbox');
    tester.state<NavigatorState>(find.byType(Navigator).first).pop();
    await flush(tester);
    r.advanceTo('16:44:52');
    await poll(tester);
    r.gw.on('GET /planner/plans/{id}/proposals', (_) => Res.json(200, [
          {...(r.flowResponse('proposal')!), 'status': 'accepted', 'decided_at': DateTime.now().toUtc().toIso8601String()}
        ]));
    tripOf(tester).heard['kto-1909659'] = 'short';
    tripOf(tester).heard['kto-2610501'] = 'short';
    await tester.drag(find.byType(DraggableScrollableSheet).first, const Offset(0, -400));
    await flush(tester);
    await tester.tap(find.text('여행 종료'));
    await flush(tester);
    await tester.tap(find.text('마치기'));
    await flush(tester, 15);
    await snap(tester, '30_trip_end');
  });
}
