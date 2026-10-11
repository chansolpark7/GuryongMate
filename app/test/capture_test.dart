// Figma 대조용 화면 캡처 (docs/app/review/l1.md). Figma 프레임과 같은 장면(데모 데이터 §6.1, 장면 ④ 등)을 녹화 fixture 로 그려
// docs/app/review/l1/app/fx_*.png (390×844) 로 저장한다. 실제 한글 글꼴(맑은 고딕)과 Material 아이콘을 불러 그린다. 지도 타일은 네트워크가 없어 비어 있다.
//   flutter test test/capture_test.dart --dart-define=CAPTURE=true
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/app_session.dart';
import 'package:guryong_mate/core/theme/app_theme.dart';
import 'package:guryong_mate/core/token_store.dart';
import 'package:guryong_mate/features/auth/login_screen.dart';
import 'package:guryong_mate/features/chat/chat_controller.dart';
import 'package:guryong_mate/features/guide/guide_prep_screen.dart';
import 'package:guryong_mate/features/planner/plan_controller.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';
import 'package:guryong_mate/features/plans/plan_list_screen.dart';
import 'package:guryong_mate/features/settings/memory_screen.dart';
import 'package:guryong_mate/features/settings/settings_screen.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'fixtures.dart';
import 'support/extra.dart';
import 'support/fixture_gateway.dart';
import 'support/harness.dart';

const capture = bool.fromEnvironment('CAPTURE');
const outDir = '../docs/app/review/l1/app';

Future<void> loadFonts() async {
  Future<void> family(String name, List<String> files) async {
    final loader = FontLoader(name);
    for (final f in files) {
      loader.addFont(Future.value(ByteData.sublistView(File(f).readAsBytesSync())));
    }
    await loader.load();
  }

  await family(appFontFamily!, ['C:/Windows/Fonts/malgun.ttf', 'C:/Windows/Fonts/malgunbd.ttf']);
  await family('MaterialIcons', ['C:/flutter/bin/cache/artifacts/material_fonts/materialicons-regular.otf']);
}

final _root = GlobalKey();

/// CAPTURE 를 켰을 때만 돈다 (일반 flutter test 에서는 건너뜀).
void captureTest(String name, WidgetTesterCallback body) => testWidgets(name, body, skip: !capture);

Widget framed(AppSession s, Widget home) => RepaintBoundary(key: _root, child: app(s, home));

/// 요청·응답이 실제 이벤트 루프에서 끝나도록 잠깐 실제 시간을 흘린다 (toImage 를 위해 runAsync 를 쓰기 때문).
Future<void> flush(WidgetTester tester, [int times = 5]) async {
  for (var i = 0; i < times; i++) {
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 30)));
    await tester.pump(const Duration(milliseconds: 50));
  }
}

Future<void> snap(WidgetTester tester, String name) async {
  await flush(tester);
  await tester.pump(const Duration(milliseconds: 100));
  final boundary = _root.currentContext!.findRenderObject()! as RenderRepaintBoundary;
  await tester.runAsync(() async {
    final image = await boundary.toImage(pixelRatio: 1);
    final png = await image.toByteData(format: ui.ImageByteFormat.png);
    final file = File('$outDir/fx_$name.png');
    await file.parent.create(recursive: true);
    await file.writeAsBytes(png!.buffer.asUint8List());
  });
}

void main() {
  setUpHarness();
  setUpAll(() async {
    if (capture) await loadFonts();
  });

  ({FixtureGateway gw, Map<String, dynamic> plan}) scene4() {
    final gw = FixtureGateway(['f6_safety.json']);
    final plan = (gw.response('proposal') as Map)['before'] as Map<String, dynamic>;
    gw.on('GET /planner/plans/{id}', (_) => Res.json(200, plan));
    gw.on('GET /planner/plans/{id}/safety', (_) => Res.json(200, gw.response('compare_before_safety')));
    gw.on('GET /planner/plans/{id}/safety/alerts', (_) => Res.json(200, []));
    gw.on('GET /safety/outlook', (r) => Res.json(200, outlookJson(date: r.query['date']!)));
    gw.on('GET /safety/situation', (_) => Res.json(200, situationJson()));
    gw.on('POST /routing/route', (r) => (r.body as Map)['hazards'] == 'avoid' ? Res.json(200, gw.response('detour_route')) : Res.error(503, 'routing_unavailable'));
    final sid = gw.param('session_with_proactive', 'session_id');
    gw.on('GET /agent/sessions', (_) => Res.json(200, [
          {'id': sid, 'plan_id': plan['id'], 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z'}
        ]));
    gw.on('GET /agent/sessions/{sid}', (_) => Res.json(200, gw.response('session_with_proactive')));
    return (gw: gw, plan: plan);
  }

  captureTest('장면 ④: 25 · 26 · 27 · 28 · 28-S1 · 21 · 36', (tester) async {
    await phone(tester);
    final s4 = scene4();
    final s = await signedInSession(s4.gw);
    await tester.pumpWidget(framed(s, PlannerShell(planId: s4.plan['id'] as int)));
    await settle(tester, 30);
    await snap(tester, '25_planner_safety_scene4');

    await tester.tap(find.text('위험 · 풍속 16m/s').at(1));
    await settle(tester, 20);
    await snap(tester, '26_safety_detail_scene4');
    await tester.tapAt(const Offset(195, 60));
    await settle(tester, 20);

    await tester.tap(find.textContaining('안전 점검 · 예보 기준'));
    await settle(tester, 20);
    await snap(tester, '27_outlook');
    await tester.tapAt(const Offset(195, 60));
    await settle(tester, 20);

    await tester.tap(find.text('Map'));
    await settle(tester, 40);
    await snap(tester, '28_map_safety_scene4');
    await tester.tap(find.byTooltip('지도 레이어'));
    await settle(tester, 10);
    await snap(tester, '28s1_layers');
    await tester.tap(find.byTooltip('지도 레이어'));
    await tester.tap(find.text('Planner'));
    await settle(tester, 10);

    await tester.tap(find.byTooltip('구룡메이트 열기').first);
    await settle(tester, 30);
    await snap(tester, '22like_sheet_with_card_scene4');
    await tapIn(tester, find.text('비교 보기 ›'), frames: 40);
    await snap(tester, '21_compare_scene4');
    await tester.tap(find.byTooltip('닫기').first);
    await settle(tester, 20);

    // 36: 같은 카드의 승인을 409 base_changed 로
    final f2 = FixtureGateway(['f2_proposal_actions.json']);
    s4.gw.on('POST /agent/sessions/{s}/proposals/{p}/accept', (_) => Res.json(409, f2.response('accept_base_changed')));
    await tapIn(tester, find.text('전체 적용'), frames: 20);
    await snap(tester, '36_base_changed');
  });

  captureTest('05 진행 중 · 06 카드 · 06B 추천 (장면 ①)', (tester) async {
    await phone(tester);
    final gw = FixtureGateway(['f1_plan_with_ai.json'], sseRealtime: true);
    var plan = gw.response('create_plan') as Map<String, dynamic>;
    gw.on('GET /planner/plans/{id}', (_) => Res.json(200, plan));
    gw.on('GET /planner/plans/{id}/safety', (_) => Res.json(200, gw.response('plan_safety')));
    gw.on('GET /planner/plans/{id}/safety/alerts', (_) => Res.json(200, []));
    gw.on('GET /agent/sessions/{sid}', (_) => Res.json(200, {...gw.response('create_session') as Map, 'messages': []}));
    final s = await signedInSession(gw);
    await tester.pumpWidget(framed(s, const PlannerShell(planId: 240)));
    await settle(tester, 20);
    await snap(tester, '25_planner_empty');
    await tester.tap(find.byTooltip('구룡메이트 열기').first);
    await settle(tester, 20);
    await snap(tester, '05_welcome');
    await tester.enterText(find.byType(TextField).last, '엄마랑 구룡포 일정 짜줘');
    await tester.pump();
    await tester.tap(find.byTooltip('보내기'));
    await tester.pump(const Duration(milliseconds: 3200));
    await snap(tester, '05_streaming');
    for (var i = 0; i < 40; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
    final chat = Provider.of<ChatController>(tester.element(find.text('Planner')), listen: false);
    await until(tester, () => !chat.busy);
    await settle(tester, 10);
    await reveal(tester, find.textContaining('8단계 완료'));
    await snap(tester, '06_change_preview');
    await reveal(tester, find.text('전체 적용'));
    await snap(tester, '06_change_preview_actions');
    plan = (gw.response('accept') as Map)['plan'] as Map<String, dynamic>;
    await tester.tap(find.text('전체 적용'));
    await settle(tester, 30);
    await snap(tester, '08_after_apply');
    await settle(tester, 100);
    await snap(tester, '25_planner_safety_scene1');

    await tester.tap(find.byTooltip('구룡메이트 열기').first);
    await settle(tester, 20);
    await tester.enterText(find.byType(TextField).last, '갈 만한 카페 추천해줘');
    await tester.pump();
    await tester.tap(find.byTooltip('보내기'));
    for (var i = 0; i < 60; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
    await until(tester, () => !chat.busy);
    await settle(tester, 10);
    await reveal(tester, find.text('일정에 추가'));
    await snap(tester, '06b_recommend');
    final rec = chat.turns.last.cards.single as RecommendationEntry;
    expect(rec.recommendation.candidates, hasLength(2));
  });

  captureTest('29 · 31 · 32 · 33 · 34 · 34-S1 · 35 · 01', (tester) async {
    await phone(tester);
    PlanListScreen.today = () => DateTime(2026, 10, 10);
    final gw = FixtureGateway(['f5_delay.json', 'f6_safety.json', 'f4_arrival_docent.json', 'f10_guide_prep.json', 'f1_plan_with_ai.json']);
    final plans = [
      planJson([itemJson(1, 1, '구룡포 일본인 가옥거리', '12:30', '13:30')], id: 1, title: '엄마랑 구룡포', start: '2026-10-10', end: '2026-10-10'),
      planJson([for (var i = 2; i < 9; i++) itemJson(i, i - 1, '호미곶', '10:00', '11:00')], id: 2, title: '가족 포항 1박', start: '2026-10-11', end: '2026-10-12'),
      planJson([for (var i = 10; i < 14; i++) itemJson(i, i - 9, '호미곶', '06:00', '07:00')], id: 3, title: '호미곶 일출', start: '2026-10-21', end: '2026-10-21'),
    ];
    final caution = Map<String, dynamic>.of(gw.response('compare_before_safety') as Map<String, dynamic>)
      ..['overall_risk'] = 'caution'
      ..['counts'] = {'safe': 4, 'caution': 1, 'danger': 0, 'unassessed': 0, 'info': 0};
    gw.on('GET /planner/plans', (_) => Res.json(200, plans.map((p) => Map.of(p)..remove('days')).toList()));
    gw.on('GET /planner/plans/{id}/proposals', (r) => Res.json(200, r.path.contains('/2/') ? [gw.response('proposal')] : []));
    gw.on('GET /trip/plans/{id}/state', (_) => Res.json(200, gw.response('state_after')));
    gw.on('GET /planner/plans/{id}/safety', (r) => Res.json(200, r.path.contains('/1/') ? {...gw.response('plan_safety') as Map} : caution));
    gw.on('GET /planner/plans/{id}/safety/alerts', (_) => Res.json(200, []));
    final s = await signedInSession(gw);
    await tester.pumpWidget(framed(s, const PlanListScreen()));
    await settle(tester, 20);
    await snap(tester, '29_plan_list');

    // 31: f4 메모리 + 대화에서 알게 된 것 예시 (Figma 31 과 같은 분류)
    final mem = gw.response('memory_after') as Map<String, dynamic>;
    final now = DateTime.now().toUtc().toIso8601String();
    gw.on('GET /docent/memory', (_) => Res.json(200, {
          ...mem,
          'items': [
            {'id': 41, 'scope': 'user', 'plan_id': null, 'category': 'interest', 'text': '역사·근대 건축과 사진을 좋아해요', 'source': 'chat:x', 'created_at': '2026-10-08T03:00:00Z', 'updated_at': now},
            ...(mem['items'] as List).map((e) => {...e as Map, 'created_at': now}),
            {'id': 42, 'scope': 'plan', 'plan_id': 1, 'category': 'companion', 'text': '어머니와 함께, 오래 걷기는 부담', 'source': 'chat:x', 'created_at': '2026-10-08T03:00:00Z', 'updated_at': now},
          ],
        }));
    await tester.pumpWidget(framed(s, const MemoryScreen(planId: 1)));
    await settle(tester, 20);
    await snap(tester, '31_memory');

    // 32: 작업 진행 중
    gw.on('GET /planner/plans/{id}', (_) => Res.json(200, (gw.response('accept') as Map)['plan']));
    GuidePrepScreen.pollInterval = const Duration(seconds: 2);
    final c = PlanController(s.api, 240);
    await tester.runAsync(c.load);
    gw.queues['GET /agent/plans/{plan_id}/guide/jobs/{job_id}']!.removeAt(0);
    await tester.pumpWidget(framed(s, GuidePrepScreen(plan: c)));
    await settle(tester, 10);
    await tester.tap(find.text('가이드 준비'));
    await settle(tester, 45);
    await snap(tester, '32_guide_prep_running');
    await settle(tester, 120);
    await snap(tester, '32_guide_prep_done');

    // 33
    AppConfig.demo = true;
    gw.on('GET /health', (_) => Res.json(200, {'status': 'ok'}));
    gw.on('GET /agent/info', (_) => Res.json(200, {'provider': 'scripted:scripts'}));
    gw.on('GET /safety/sim', (_) => Res.json(200, simJson()));
    await tester.pumpWidget(framed(s, const SettingsScreen()));
    await settle(tester, 20);
    await snap(tester, '33_settings');
    AppConfig.demo = false;

    // 01 · 34 · 34-S1 · 35
    SharedPreferences.setMockInitialValues({'base_url': 'http://gateway.test'});
    final f11 = FixtureGateway(['f11_login.json']);
    final out = await AppSession.load(clientFactory: f11.client, tokenStore: MemoryTokenStore());
    await tester.pumpWidget(framed(out, const LoginScreen()));
    await settle(tester, 10);
    await snap(tester, '01_login');
    await tester.pumpWidget(framed(out, const LoginIdScreen()));
    await settle(tester, 10);
    final fields = find.byType(TextField);
    await tester.enterText(fields.at(0), 'seoyun-demo');
    await tester.enterText(fields.at(1), 'wrong-pass1');
    f11.queues['POST /auth/login']!.removeRange(1, 2);
    await tester.pump();
    await tester.tap(find.text('로그인'));
    await flush(tester);
    await settle(tester, 10);
    await tester.enterText(fields.at(1), 'wrongpass2');
    await settle(tester, 5);
    await snap(tester, '34_login_id_error');
    f11.on('POST /auth/login', (_) => Res.error(429, 'too_many_attempts', '', {'retry_after': 840}));
    await tester.tap(find.text('로그인'));
    await flush(tester);
    await settle(tester, 10);
    await snap(tester, '34s1_locked');
    await tester.pumpWidget(framed(out, const SignupScreen()));
    await settle(tester, 10);
    await tester.enterText(find.byType(TextField).at(0), 'seoyun-demo');
    await tester.enterText(find.byType(TextField).at(1), 'guryong2026');
    await tester.pump();
    await tester.tap(find.text('가입하고 시작하기'));
    await flush(tester);
    await settle(tester, 10);
    await snap(tester, '35_signup_taken');
  });
}
