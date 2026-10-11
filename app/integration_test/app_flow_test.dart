// 실제 앱을 각본 LLM 스택(AGENT_PROVIDER=scripted, SAFETY_SOURCE=mock)에 붙여 F1·F2 를 눌러 보는 시나리오.
//   docker compose up -d --build          (저장소 루트, .env 의 API_PORT)
//   flutter test integration_test/app_flow_test.dart -d windows --dart-define=LIVE_API=http://127.0.0.1:8018 --dart-define=DEMO=true
// 단계마다 화면을 build/screens/NN_이름.png (390×844) 로 저장한다. 만든 계정은 끝에 탈퇴로 지운다.
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/api/api_client.dart';
import 'package:guryong_mate/core/app_session.dart';
import 'package:guryong_mate/core/token_store.dart';
import 'package:guryong_mate/features/chat/chat_controller.dart';
import 'package:guryong_mate/features/planner/plan_controller.dart';
import 'package:guryong_mate/main.dart';
import 'package:integration_test/integration_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

const live = String.fromEnvironment('LIVE_API', defaultValue: 'http://127.0.0.1:8018');

final _root = GlobalKey();
var _shotNo = 0;

Future<void> shot(WidgetTester tester, String name) async {
  await tester.pump(const Duration(milliseconds: 300));
  final boundary = _root.currentContext!.findRenderObject()! as RenderRepaintBoundary;
  final image = await boundary.toImage(pixelRatio: 1);
  final png = await image.toByteData(format: ui.ImageByteFormat.png);
  final file = File('build/screens/${(++_shotNo).toString().padLeft(2, '0')}_$name.png');
  await file.parent.create(recursive: true);
  await file.writeAsBytes(png!.buffer.asUint8List());
}

Future<void> settle(WidgetTester tester, [int ms = 800]) async {
  for (var t = 0; t < ms; t += 100) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

/// 네트워크 응답을 기다리며 [finder] 가 나타날 때까지 화면을 갱신한다.
Future<void> waitFor(WidgetTester tester, Finder finder, {Duration timeout = const Duration(seconds: 30)}) async {
  final end = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(end)) {
    await tester.pump(const Duration(milliseconds: 100));
    if (finder.evaluate().isNotEmpty) return;
  }
  await shot(tester, 'FAIL');
  throw TestFailure('시간 안에 나타나지 않음: $finder');
}

Future<void> waitUntil(WidgetTester tester, bool Function() cond, String what, {Duration timeout = const Duration(seconds: 60)}) async {
  final end = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(end)) {
    await tester.pump(const Duration(milliseconds: 100));
    if (cond()) return;
  }
  await shot(tester, 'FAIL');
  throw TestFailure('시간 안에 조건을 만족하지 않음: $what');
}

/// 시트 안 목록(DraggableScrollableSheet)은 ensureVisible 로 스크롤되지 않아 끌어서 화면 안으로 가져온다.
bool _exists(Finder f) {
  try {
    return f.evaluate().isNotEmpty;
  } catch (_) {
    return false; // .last 같은 finder 는 비어 있으면 던진다
  }
}

Future<void> reveal(WidgetTester tester, Finder f) async {
  final sheet = find.descendant(of: find.byType(DraggableScrollableSheet), matching: find.byType(Scrollable));
  if (sheet.evaluate().isEmpty) {
    await tester.ensureVisible(f.first);
    await tester.pump();
    return;
  }
  final view = tester.getRect(sheet.first);
  for (var i = 0; i < 40; i++) {
    if (_exists(f)) {
      final r = tester.getRect(f.first);
      if (r.top >= view.top + 8 && r.bottom <= view.bottom - 8) return;
      await tester.drag(sheet.first, Offset(0, r.center.dy > view.center.dy ? -200 : 200));
    } else {
      await tester.drag(sheet.first, const Offset(0, -200));
    }
    await settle(tester, 200);
  }
}

Future<void> tap(WidgetTester tester, Finder f, {int ms = 600}) async {
  await reveal(tester, f);
  await tester.tap(f.first);
  await settle(tester, ms);
}

/// 입력창에 쓰고 보낸다. 카드 버튼을 누른 뒤엔 입력창 포커스가 빠져 글이 안 들어갈 수 있어 확인하고 다시 넣는다.
Future<void> say(WidgetTester tester, String text) async {
  final field = find.byType(TextField).last;
  for (var i = 0; i < 3; i++) {
    await tester.tap(field);
    await settle(tester, 300);
    await tester.enterText(field, text);
    await settle(tester, 300);
    if (tester.widget<TextField>(field).controller?.text == text) break;
  }
  await tester.tap(find.byTooltip('보내기'));
  await tester.pump();
}

PlanController planOf(WidgetTester tester) => Provider.of<PlanController>(tester.element(find.text('Planner')), listen: false);
ChatController chatOf(WidgetTester tester) => Provider.of<ChatController>(tester.element(find.text('Planner')), listen: false);

Future<void> waitTurn(WidgetTester tester) => waitUntil(tester, () => !chatOf(tester).busy, '응답 끝');

Future<void> closeSheet(WidgetTester tester) async {
  if (find.byTooltip('닫기').evaluate().isNotEmpty) await tap(tester, find.byTooltip('닫기').last);
  await settle(tester, 600);
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('각본 LLM 스택: 로그인 → F1 AI 로 계획 만들기 → 안전 화면 → F2 제안 승인·base_changed → 설정', (tester) async {
    await tester.binding.setSurfaceSize(const Size(390, 844));
    AppConfig.demo = true;
    final prefs = await SharedPreferences.getInstance();
    await prefs.clear();
    await prefs.setString('base_url', live);
    final store = SecureTokenStore();
    await store.clear();

    // 계획 모드 흐름은 여행 전날 10:00, 평상시 (fixtures.md §1)
    await ApiClient(baseUrl: live).put('/safety/sim', {'scenario': 'calm', 'now': '2026-10-09T10:00:00+09:00'});

    final session = await AppSession.load(tokenStore: store);
    final loginId = 'l1ui${DateTime.now().millisecondsSinceEpoch % 100000000}';
    await tester.pumpWidget(RepaintBoundary(key: _root, child: ChangeNotifierProvider.value(value: session, child: const GuryongApp())));
    await settle(tester, 1500);

    // ---------- 01 · 33(축약) · 35 ----------
    expect(find.text('구룡메이트'), findsOneWidget);
    await shot(tester, '01_login');
    await tap(tester, find.text('서버 설정'));
    await waitFor(tester, find.text('연결됨'));
    await shot(tester, '33_server_only');
    await tap(tester, find.byType(BackButton));
    await tap(tester, find.text('아이디로 계속하기'));
    await shot(tester, '34_login_id');
    await tap(tester, find.text('가입하기'));
    final fields = find.byType(TextField);
    await tester.enterText(fields.at(0), loginId);
    await tester.enterText(fields.at(1), 'guryong2026');
    await tester.enterText(fields.at(2), '서윤');
    await settle(tester, 300);
    await shot(tester, '35_signup');
    await tap(tester, find.text('가입하고 시작하기'));

    // ---------- 29 빈 목록 → 계획 만들기 (API, 데모 데이터 §6.1) ----------
    await waitFor(tester, find.text('My Plans'));
    await settle(tester, 800);
    final plan = await session.api.createPlan({
      'title': '엄마랑 구룡포', 'start_date': '2026-10-10', 'end_date': '2026-10-10', 'day_start': '12:30', 'day_end': '18:00', 'travel_mode': 'walk',
    });
    await tester.fling(find.text('My Plans'), const Offset(0, 300), 1000);
    await waitFor(tester, find.text('엄마랑 구룡포'));
    await settle(tester, 1500);
    await shot(tester, '29_plan_list');
    await tap(tester, find.text('엄마랑 구룡포'));
    await waitFor(tester, find.text('아직 일정이 없어요'));
    await settle(tester, 1500);
    await shot(tester, '25_planner_empty');

    // ---------- F1: 05 스트리밍 → 06 카드 → 21 비교 → 승인 ----------
    await tap(tester, find.byTooltip('구룡메이트 열기').first, ms: 1200);
    await shot(tester, '05_ai_sheet_welcome');
    await say(tester, '엄마랑 구룡포 일정 짜줘');
    await waitFor(tester, find.text('일정 조정'));
    await shot(tester, '05_ai_sheet_streaming');
    await waitTurn(tester);
    await settle(tester, 800);
    await reveal(tester, find.text('일정 변경 제안'));
    await shot(tester, '06_change_preview');
    await reveal(tester, find.text('전체 적용'));
    await shot(tester, '06_change_preview_actions');
    await tap(tester, find.text('비교 보기 ›'), ms: 2500);
    await waitFor(tester, find.text('기존안 · 수정안 비교'));
    await settle(tester, 3000);
    await shot(tester, '21_compare');
    await tap(tester, find.byTooltip('닫기').first);
    await tap(tester, find.text('전체 적용'), ms: 2500);
    await waitFor(tester, find.textContaining('AI가 일정'));
    await shot(tester, '08_after_apply');
    final c = planOf(tester);
    expect(c.plan!.allItems, hasLength(5));
    await waitUntil(tester, () => c.safety != null && c.safety!.items.isNotEmpty, '안전 점검');
    // 테스트 바인딩은 접근성 탐색을 켜 SnackBar 가 스스로 내려가지 않는다 → 직접 내린다
    ScaffoldMessenger.of(tester.element(find.text('Planner'))).hideCurrentSnackBar();
    await settle(tester, 800);
    await shot(tester, '25_planner_safety');

    // 26 근거 시트 · 27 전망
    await tap(tester, find.text('안전').first, ms: 1200);
    await shot(tester, '26_safety_detail');
    await tester.tapAt(const Offset(195, 60));
    await settle(tester, 800);
    await tap(tester, find.textContaining('안전 점검'), ms: 2500);
    await shot(tester, '27_outlook');
    await tester.tapAt(const Offset(195, 60));
    await settle(tester, 800);

    // 07 지도 (routing geometry) · 28-S1 레이어
    await tap(tester, find.text('Map'), ms: 6000);
    await shot(tester, '07_map_route');
    await tap(tester, find.byTooltip('지도 레이어'));
    await shot(tester, '28s1_layers');
    await tap(tester, find.byTooltip('지도 레이어'));
    await tap(tester, find.text('Planner'));

    // 06B 추천 → 추가 → 빼기
    await tap(tester, find.byTooltip('구룡메이트 열기').first, ms: 1200);
    await say(tester, '갈 만한 카페 추천해줘');
    await waitTurn(tester);
    await settle(tester, 800);
    await reveal(tester, find.text('일정에 추가'));
    await shot(tester, '06b_recommend');
    await tap(tester, find.text('일정에 추가'), ms: 2000);
    await waitUntil(tester, () => find.text('일정에 추가').evaluate().isEmpty, '추가됨');
    await shot(tester, '06b_recommend_added');
    await tap(tester, find.text('취소').last, ms: 2000);
    await waitFor(tester, find.text('일정에 추가'));
    await settle(tester, 2000); // 카드 동작 직후엔 세션 잠금이 잠깐 남아 있다 (409 agent_busy)

    // ---------- F2: 제안 → 직접 편집 → 승인 409 base_changed → 36 → 다시 제안 → 거절 ----------
    await say(tester, '구룡포항 점심은 1시간으로 늘려줘');
    await waitTurn(tester);
    await settle(tester, 600);
    expect(chatOf(tester).turns.last.user, '구룡포항 점심은 1시간으로 늘려줘');
    final house = c.plan!.allItems.firstWhere((i) => i.title.contains('가옥거리'));
    await session.api.updateItem(plan.id, house.id, {'stay_minutes': 70}); // 다른 기기에서 고친 것처럼
    await tap(tester, find.text('전체 적용').last, ms: 1500);
    await waitFor(tester, find.text('이 제안 이후 일정이 바뀌었어요'));
    await shot(tester, '36_base_changed');
    await tap(tester, find.text('현재 일정에 맞춰 다시 제안'));
    await waitTurn(tester);
    await settle(tester, 800);
    expect(chatOf(tester).turns.where((t) => t.proposals.any((e) => e.state == ProposalState.superseded)), isNotEmpty);
    await reveal(tester, find.text('전체 적용').last);
    await shot(tester, '20_reproposed');
    await tap(tester, find.text('거절').last, ms: 1500);
    await waitFor(tester, find.text('거절했어요. 일정은 그대로예요.'));

    // 다시 요청 → 또 편집 → 409 → 카드 그대로 반영(overwrite)
    await say(tester, '점심 1시간으로 늘려줘');
    await waitTurn(tester);
    await session.api.updateItem(plan.id, house.id, {'stay_minutes': 60});
    await tap(tester, find.text('전체 적용').last, ms: 1500);
    await waitFor(tester, find.text('카드에서 본 일정 그대로 반영'));
    await tap(tester, find.text('카드에서 본 일정 그대로 반영'), ms: 2500);
    await waitFor(tester, find.textContaining('AI가 일정'));
    await shot(tester, '08_after_overwrite');
    ScaffoldMessenger.of(tester.element(find.text('Planner'))).hideCurrentSnackBar();
    await settle(tester, 800);

    // ---------- 32 가이드 준비 ----------
    await tap(tester, find.byTooltip('더보기'));
    await tap(tester, find.text('현장 가이드 준비'), ms: 1500);
    await shot(tester, '32_guide_prep');
    await tap(tester, find.text('가이드 준비'), ms: 1500);
    await shot(tester, '32_guide_prep_running');
    await waitFor(tester, find.text('모두 준비됐어요'), timeout: const Duration(seconds: 120));
    await settle(tester, 800);
    await shot(tester, '32_guide_prep_done');
    await tap(tester, find.byType(BackButton));

    // ---------- 29 · 33 · 31 ----------
    await tap(tester, find.byTooltip('뒤로'), ms: 2500);
    await waitFor(tester, find.text('My Plans'));
    await settle(tester, 2000);
    await shot(tester, '29_plan_list_safety');
    await tap(tester, find.byTooltip('설정'), ms: 2500);
    await waitFor(tester, find.text('연결됨'));
    await shot(tester, '33_settings');
    await tap(tester, find.text('나에 대해'), ms: 1500);
    await shot(tester, '31_memory');
    await tap(tester, find.byType(BackButton));

    // 정리: 탈퇴 (계획·대화·메모리 함께) → 01
    final scroll = find.byType(Scrollable).first;
    await tester.scrollUntilVisible(find.text('계정 삭제'), 200, scrollable: scroll);
    await tap(tester, find.text('계정 삭제'));
    await tap(tester, find.text('계속'));
    await tap(tester, find.text('계정 삭제').last, ms: 2500);
    await waitFor(tester, find.text('아이디로 계속하기'));
    await ApiClient(baseUrl: live).put('/safety/sim', {'reset_clock': true});
    debugPrint('UI 시나리오 완료: $loginId 탈퇴함, 캡처 $_shotNo장');
  });
}
