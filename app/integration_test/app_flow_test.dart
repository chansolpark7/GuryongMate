// 실제 앱을 실제 백엔드에 붙여 화면을 눌러 보는 시나리오.
//   docker compose up -d   (저장소 루트, 게이트웨이 :8000)
//   flutter test integration_test/app_flow_test.dart -d windows
// 단계마다 화면을 build/screens/*.png 로 저장한다. 만든 여행은 끝에 지운다.
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/app_session.dart';
import 'package:guryong_mate/core/models/plan.dart';
import 'package:guryong_mate/features/planner/plan_controller.dart';
import 'package:guryong_mate/main.dart';
import 'package:integration_test/integration_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

final _root = GlobalKey();
var _shotNo = 0;

Future<void> shot(WidgetTester tester, String name) async {
  await tester.pump(const Duration(milliseconds: 300));
  final boundary = _root.currentContext!.findRenderObject()! as RenderRepaintBoundary;
  final image = await boundary.toImage(pixelRatio: 1.5);
  final png = await image.toByteData(format: ui.ImageByteFormat.png);
  final file = File('build/screens/${(++_shotNo).toString().padLeft(2, '0')}_$name.png');
  await file.parent.create(recursive: true);
  await file.writeAsBytes(png!.buffer.asUint8List());
}

/// 네트워크 응답을 기다리며 [finder] 가 나타날 때까지 화면을 갱신한다.
Future<void> waitFor(WidgetTester tester, Finder finder, {Duration timeout = const Duration(seconds: 20)}) async {
  final end = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(end)) {
    await tester.pump(const Duration(milliseconds: 100));
    if (finder.evaluate().isNotEmpty) return;
  }
  await shot(tester, 'FAIL');
  throw TestFailure('시간 안에 나타나지 않음: $finder');
}

Future<void> waitUntil(WidgetTester tester, bool Function() cond, String what, {Duration timeout = const Duration(seconds: 20)}) async {
  final end = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(end)) {
    await tester.pump(const Duration(milliseconds: 100));
    if (cond()) return;
  }
  throw TestFailure('시간 안에 조건을 만족하지 않음: $what');
}

Future<void> waitGone(WidgetTester tester, Finder finder, {Duration timeout = const Duration(seconds: 20)}) async {
  final end = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(end)) {
    await tester.pump(const Duration(milliseconds: 100));
    if (finder.evaluate().isEmpty) return;
  }
  throw TestFailure('시간 안에 사라지지 않음: $finder');
}

Future<void> settle(WidgetTester tester, [int ms = 800]) async {
  for (var t = 0; t < ms; t += 100) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

Future<void> tap(WidgetTester tester, Finder f) async {
  await tester.ensureVisible(f);
  await tester.pump();
  await tester.tap(f);
  await settle(tester, 500);
}

/// 실제 시간으로 누르고 있는 길게 누르기 (live 바인딩에선 pump 시간이 실제로 흐르지 않을 수 있다).
Future<void> longPress(WidgetTester tester, Finder f) async {
  final g = await tester.startGesture(tester.getCenter(f.first));
  for (var i = 0; i < 8; i++) {
    await Future<void>.delayed(const Duration(milliseconds: 100));
    await tester.pump();
  }
  await g.up();
  await settle(tester, 500);
}

PlanController planOf(WidgetTester tester) =>
    Provider.of<PlanController>(tester.element(find.text('Planner')), listen: false);

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('실제 백엔드: 로그인 → 여행 만들기 → 일정 편집 → 지도', (tester) async {
    await tester.binding.setSurfaceSize(const Size(390, 844));
    final prefs = await SharedPreferences.getInstance();
    await prefs.clear();
    final session = await AppSession.load();
    final user = 'app-ui-${DateTime.now().millisecondsSinceEpoch}@test.dev';

    await tester.pumpWidget(RepaintBoundary(
      key: _root,
      child: ChangeNotifierProvider.value(value: session, child: const GuryongApp()),
    ));
    await settle(tester, 1500);

    // ---------- 01 로그인 ----------
    expect(find.text('Planit'), findsOneWidget);
    await shot(tester, 'login');
    await tap(tester, find.text('Google로 계속하기'));
    expect(find.textContaining('Google 로그인은 계정 서비스가'), findsOneWidget);
    await tap(tester, find.text('이메일로 계속하기'));
    await tester.enterText(find.byType(TextField).first, user);
    await settle(tester, 300);
    await shot(tester, 'login_email');
    await tap(tester, find.text('계속하기'));

    // ---------- 02 빈 목록 ----------
    await waitFor(tester, find.text('My Plans'));
    await settle(tester, 1000);
    expect(find.text('아직 여행 계획이 없어요'), findsOneWidget);
    await shot(tester, 'plan_list_empty');

    // ---------- 03 여행 만들기 ----------
    await tap(tester, find.byTooltip('새 여행 만들기'));
    await tester.enterText(find.byType(TextField).first, '엄마랑 구룡포');
    await tap(tester, find.text('날짜를 선택하세요'));
    // 달력: 오늘이 있는 달의 날짜 두 개를 누른다 (첫 번째로 보이는 달)
    final now = DateTime.now();
    final startDay = now.day + 6 <= 26 ? now.day + 6 : 3;
    await tester.tap(find.text('$startDay').first);
    await settle(tester, 300);
    await tester.tap(find.text('${startDay + 2}').first);
    await settle(tester, 300);
    await shot(tester, 'date_picker');
    await tap(tester, find.text('저장'));
    await tap(tester, find.text('맛집'));
    await tap(tester, find.text('바다·자연'));
    await tap(tester, find.text('도보'));
    await shot(tester, 'create_plan');
    await tap(tester, find.text('여행 만들기'));

    // ---------- 04 빈 Planner ----------
    await waitFor(tester, find.text('Day 1 일정이 비어 있어요'));
    await settle(tester, 500);
    expect(find.text('엄마랑 구룡포'), findsOneWidget);
    expect(find.textContaining('2박 3일'), findsOneWidget);
    await shot(tester, 'planner_empty');

    // ---------- 장소 검색 → 3곳 추가 ----------
    Future<void> addPlace(String query, {bool viaDetail = false}) async {
      await tap(tester, find.text('일정 추가').last);
      await waitFor(tester, find.text('Day 1에 일정 추가'));
      await tester.enterText(find.byType(TextField).last, query);
      await settle(tester, 600);
      await waitFor(tester, find.byTooltip('바로 추가'));
      await settle(tester, 1500);
      if (viaDetail) {
        await shot(tester, 'search_$query');
        await tester.tap(find.descendant(of: find.byType(ListTile), matching: find.byType(Text)).first);
        await waitFor(tester, find.text('Day 1에 추가'));
        await settle(tester, 2500); // 사진 로딩
        await shot(tester, 'place_detail');
        await tap(tester, find.text('1시간 30분'));
        await tap(tester, find.text('Day 1에 추가'));
      } else {
        await tap(tester, find.byTooltip('바로 추가').first);
      }
      await waitFor(tester, find.text('Day 1에 추가했어요'));
      await settle(tester, 500);
    }

    await addPlace('구룡포 시장', viaDetail: true);
    await addPlace('일본인 가옥');
    await addPlace('구룡포 해수욕장');
    final c = planOf(tester);
    final items = c.currentDay!.items;
    expect(items, hasLength(3));
    expect(items.first.stayMinutes, 90);
    await settle(tester, 4500); // 스낵바 사라질 때까지
    await shot(tester, 'planner_3_items');

    // ---------- 드래그로 순서 변경: 마지막 → 첫 번째 ----------
    final lastTitle = items.last.title;
    final handles = find.byIcon(Icons.drag_indicator);
    final gesture = await tester.startGesture(tester.getCenter(handles.last));
    await tester.pump(const Duration(milliseconds: 600));
    for (var i = 0; i < 20; i++) {
      await gesture.moveBy(const Offset(0, -25));
      await tester.pump(const Duration(milliseconds: 30));
    }
    await gesture.up();
    await waitUntil(tester, () => planOf(tester).currentDay!.items.first.title == lastTitle && !planOf(tester).busy, 'planOf(tester).currentDay!.items.first.title == lastTitle && !planOf(tester).busy');
    await settle(tester, 800);
    expect(c.currentDay!.items.first.title, lastTitle);
    await shot(tester, 'planner_reordered');

    // ---------- ⋮ 메뉴: 시작 시간 고정 ----------
    await tap(tester, find.byTooltip('일정 편집').at(1));
    await shot(tester, 'item_menu');
    await tap(tester, find.text('시작 시간 고정'));
    await tap(tester, find.text('확인'));
    await waitFor(tester, find.textContaining('고정').hitTestable());
    await settle(tester, 800);
    final fixed = c.currentDay!.items[1];
    expect(fixed.startTime, isNotNull);

    // ⋮ 메모
    await tap(tester, find.byTooltip('일정 편집').at(0));
    await tap(tester, find.text('메모'));
    await tester.enterText(find.byType(TextField).last, '현금 준비');
    await tap(tester, find.text('저장'));
    await waitFor(tester, find.text('현금 준비'));

    // ⋮ 꼭 유지하기
    await tap(tester, find.byTooltip('일정 편집').at(2));
    await tap(tester, find.text('꼭 유지하기'));
    await waitUntil(tester, () => c.plan!.constraints.any((r) => r.type == 'must_include'), "c.plan!.constraints.any((r) => r.type == 'must_include')");

    // ---------- ⋯ 여행 조건: 이동 시간 상한 → 경고 배너 ----------
    await tap(tester, find.byTooltip('더보기'));
    await tap(tester, find.text('여행 조건'));
    await tap(tester, find.text('이동 시간 상한'));
    await tap(tester, find.text('10분 이하'));
    await waitFor(tester, find.textContaining('장소 간 이동 10분 이하'));
    await settle(tester, 500);
    await shot(tester, 'rules_sheet');
    await tester.tapAt(const Offset(195, 60)); // 시트 밖을 눌러 닫기
    await settle(tester, 800);
    final travelViolations = c.plan!.violations.where((v) => v.code == 'max_travel_minutes').toList();
    if (travelViolations.isNotEmpty) {
      expect(find.text('AI로 조정'), findsOneWidget);
    }
    await shot(tester, 'planner_with_rules');

    // ---------- Day 2 로 이동 ----------
    await tap(tester, find.byTooltip('일정 편집').at(2));
    await tap(tester, find.text('다른 날로 이동'));
    await tap(tester, find.text('Day 2').last);
    await waitUntil(tester, () => (c.plan!.dayOf(2)?.items.length ?? 0) == 1 && !c.busy, '(c.plan!.dayOf(2)?.items.length ?? 0) == 1 && !c.busy');
    await tap(tester, find.text('Day 2').first);
    await settle(tester, 800);
    await shot(tester, 'planner_day2');
    await tap(tester, find.text('Day 1').first);

    // ---------- 카드 선택 → 지도에서 보기 ----------
    await tap(tester, find.text(c.currentDay!.items.first.title));
    await tap(tester, find.text('지도에서 보기'));
    await waitFor(tester, find.text('일정에서 보기'));
    await settle(tester, 4000); // 지도 타일
    await shot(tester, 'map_selected');

    // 레이어 → 관광지
    await tap(tester, find.byTooltip('지도 레이어'));
    await tap(tester, find.text('관광지'));
    await settle(tester, 3000);
    await shot(tester, 'map_layers');
    await tap(tester, find.byTooltip('지도 레이어'));

    // 지도 빈 곳 → 경로 요약
    await tester.tapAt(const Offset(60, 300));
    await settle(tester, 800);
    await waitFor(tester, find.textContaining('Day 1 경로'));
    await shot(tester, 'map_route_summary');

    // 지도 Day 전환
    await tap(tester, find.byTooltip('Day 선택'));
    await tap(tester, find.textContaining('Day 2 ·'));
    await settle(tester, 2500);
    await shot(tester, 'map_day2');

    // 핀 → 카드 → 일정에서 보기
    await tap(tester, find.byTooltip('Day 선택'));
    await tap(tester, find.textContaining('Day 1 ·'));
    await settle(tester, 1500);
    await tap(tester, find.text('2').hitTestable().first);
    await waitFor(tester, find.text('일정에서 보기'));
    await tap(tester, find.text('일정에서 보기'));
    await waitFor(tester, find.text('Planner'));
    expect(c.tab, 0);
    await settle(tester, 800);
    await shot(tester, 'back_to_planner');

    // ---------- 삭제 ----------
    final before = c.currentDay!.items.length;
    await tap(tester, find.byTooltip('일정 편집').at(0));
    await tap(tester, find.text('삭제').last);
    await tap(tester, find.text('삭제').last); // 확인 대화상자
    await waitUntil(tester, () => c.currentDay!.items.length == before - 1 && !c.busy, 'c.currentDay!.items.length == before - 1 && !c.busy');

    // ---------- 목록으로 → 복제 → 삭제 ----------
    final planId = c.planId;
    await tap(tester, find.byTooltip('뒤로'));
    await waitFor(tester, find.text('My Plans'));
    await settle(tester, 1500);
    expect(find.text('엄마랑 구룡포'), findsOneWidget);
    await shot(tester, 'plan_list');
    await longPress(tester, find.text('엄마랑 구룡포'));
    await waitFor(tester, find.text('복제'));
    await shot(tester, 'plan_actions');
    await tap(tester, find.text('복제'));
    await waitFor(tester, find.text('엄마랑 구룡포 (복사본)'));
    await settle(tester, 800);
    await shot(tester, 'plan_list_copied');

    final api = session.api;
    final copy = (await api.plans()).firstWhere((p) => p.title.endsWith('(복사본)'));
    final copied = await api.plan(copy.id);
    final original = await api.plan(planId);
    expect(copied.allItems.map((i) => i.placeId).toList(), original.allItems.map((i) => i.placeId).toList());
    expect(copied.constraints.length, original.constraints.length);

    // 복사본을 열어 Planner 에서 확인한 뒤 ⋯ → 여행 삭제
    await tap(tester, find.text('엄마랑 구룡포 (복사본)'));
    await waitFor(tester, find.text('Planner'));
    await settle(tester, 1500);
    expect(find.text(copied.allItems.first.title), findsWidgets);
    await shot(tester, 'copy_planner');
    await tap(tester, find.byTooltip('더보기'));
    await tap(tester, find.text('여행 삭제'));
    await tap(tester, find.text('삭제').last);
    await waitFor(tester, find.text('My Plans'));
    await settle(tester, 1500);
    expect(find.text('엄마랑 구룡포 (복사본)'), findsNothing);
    expect(find.text('엄마랑 구룡포'), findsOneWidget);

    // 원본은 API 로 정리하고 목록을 당겨서 새로고침
    // (원본 카드는 이미 길게 눌렀다. live 테스트 바인딩의 합성 제스처에선 같은 카드를 다시 누르면 인식되지 않는다.
    //  실제 마우스로는 정상 동작함을 확인했다.)
    await api.deletePlan(planId);
    await tester.fling(find.text('My Plans'), const Offset(0, 300), 1000);
    await waitGone(tester, find.text('엄마랑 구룡포'));
    await settle(tester, 800);
    expect(find.text('아직 여행 계획이 없어요'), findsOneWidget);
    await shot(tester, 'plan_list_cleaned');

    // 로그아웃
    await tap(tester, find.byTooltip('계정'));
    await tap(tester, find.text('로그아웃'));
    await waitFor(tester, find.text('Google로 계속하기'));
    debugPrint('UI 시나리오 완료: 사용자 $user, 마지막 계획 ${original.title} (${original.allItems.length}곳) 정리됨');
    expect(original, isA<PlanDetail>());
  });
}
