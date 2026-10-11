// 01·34·35 로그인/가입 (F11) · 401 → refresh 1회 · 29 계획 목록 · 31 나에 대해 · 32 가이드 준비 · 33 설정 — fixture 재생.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/features/auth/login_screen.dart';
import 'package:http/http.dart' as http;
import 'package:guryong_mate/core/app_session.dart';
import 'package:guryong_mate/core/token_store.dart';
import 'package:guryong_mate/features/guide/guide_prep_screen.dart';
import 'package:guryong_mate/features/planner/plan_controller.dart';
import 'package:guryong_mate/features/plans/plan_list_screen.dart';
import 'package:guryong_mate/features/settings/memory_screen.dart';
import 'package:guryong_mate/features/settings/settings_screen.dart';
import 'package:guryong_mate/main.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'fixtures.dart';
import 'support/extra.dart';
import 'support/fixture_gateway.dart';
import 'support/harness.dart';

Future<AppSession> signedOutSession(FixtureGateway gw, MemoryTokenStore store) async {
  SharedPreferences.setMockInitialValues({'base_url': 'http://gateway.test'});
  return AppSession.load(clientFactory: gw.client, tokenStore: store);
}

Future<void> type(WidgetTester tester, String label, String text) async {
  final field = find.descendant(of: find.ancestor(of: find.text(label), matching: find.byType(Column)).first, matching: find.byType(TextField));
  await tester.enterText(field, text);
  await tester.pump();
}

void main() {
  setUpHarness();

  testWidgets('F11: 01 → 35 가입 오류(409·422) → 34 로그인 401 → 200 → 02, 토큰 저장', (tester) async {
    await phone(tester);
    final gw = FixtureGateway(['f11_login.json']);
    gw.on('GET /planner/plans', (_) => Res.json(200, []));
    final store = MemoryTokenStore();
    final s = await signedOutSession(gw, store);
    await tester.pumpWidget(ChangeNotifierProvider.value(value: s, child: const GuryongApp()));
    await settle(tester);

    expect(find.text('구룡메이트'), findsOneWidget);
    expect(find.text('여행을 함께 걷는 AI'), findsOneWidget);
    expect(find.text('Planit'), findsNothing);
    expect(find.text('서버 설정'), findsOneWidget);
    await tapText(tester, '아이디로 계속하기', frames: 15);
    expect(find.text('아이디로 로그인'), findsOneWidget);
    await tapText(tester, '가입하기', frames: 15);
    expect(find.text('아이디와 비밀번호만 있으면 돼요'), findsOneWidget);

    // 입력 중 규칙 확인 (서버를 부르지 않음)
    await type(tester, '비밀번호', 'short');
    expect(find.text('8자 이상, 영문과 숫자를 함께 써 주세요'), findsWidgets);
    // 409 login_id_taken
    await type(tester, '아이디', 'seoyun-demo');
    await type(tester, '비밀번호', 'guryong2026');
    await tapText(tester, '가입하고 시작하기');
    expect(find.text('이미 쓰는 아이디예요'), findsOneWidget);
    // 422 (서버 검증) → 비밀번호 필드
    await type(tester, '아이디', 'seoyun-new');
    await tapText(tester, '가입하고 시작하기');
    expect(gw.sent('POST', '/auth/signup').map((r) => (r.body as Map)['login_id']), ['seoyun-demo', 'seoyun-new']);
    expect(tester.widget<Text>(find.text('8자 이상, 영문과 숫자를 함께 써 주세요').first).style!.color, const Color(0xFFE5484D));

    await tester.tap(find.byType(BackButton));
    await settle(tester);
    await type(tester, '아이디', 'Seoyun-Demo');
    await type(tester, '비밀번호', 'wrong-pass1');
    await tapText(tester, '로그인');
    expect(find.text('아이디 또는 비밀번호가 맞지 않아요'), findsOneWidget);
    expect(gw.sent('POST', '/auth/login').single.body, {'login_id': 'seoyun-demo', 'password': 'wrong-pass1'}, reason: '아이디는 소문자로');
    await type(tester, '비밀번호', 'guryong2026');
    await tapText(tester, '로그인', frames: 20);
    expect(find.text('My Plans'), findsOneWidget, reason: '로그인 → 02 (스택 교체)');
    expect(find.text('아이디로 로그인'), findsNothing);
    expect(store.access, '<access_token>');
    expect(store.refresh, '<refresh_token>');
    expect(gw.sent('GET', '/planner/plans').single.headers['Authorization'], 'Bearer <access_token>');
  });

  testWidgets('34-S1: 429 too_many_attempts → 잠김 박스 + 버튼 비활성 카운트다운', (tester) async {
    await phone(tester);
    final gw = FixtureGateway([]);
    gw.on('POST /auth/login', (_) => Res.error(429, 'too_many_attempts', '잠겼습니다', {'retry_after': 600}));
    final s = await signedOutSession(gw, MemoryTokenStore());
    await tester.pumpWidget(ChangeNotifierProvider.value(value: s, child: const GuryongApp()));
    await settle(tester);
    await tapText(tester, '아이디로 계속하기', frames: 15);
    await type(tester, '아이디', 'seoyun');
    await type(tester, '비밀번호', 'guryong2026');
    await tapText(tester, '로그인');
    expect(find.textContaining('로그인을 여러 번 실패해 잠겼어요'), findsOneWidget);
    expect(find.textContaining(RegExp(r'로그인 \(\d\d:\d\d 남음\)')), findsOneWidget);
    expect(find.ancestor(of: find.textContaining('남음'), matching: find.byType(TextButton)), findsNothing, reason: '누를 수 없는 회색 버튼');
    expect(find.textContaining('에 다시 시도할 수 있어요'), findsOneWidget);
  });

  testWidgets('35 가입 오류 문구: 403 admin_only → 화면 안 안내 · 409 → 아이디 · 네트워크 → 연결(주소) · 그 밖 → 서버 메시지', (tester) async {
    await phone(tester);
    final gw = FixtureGateway([]);
    var mode = 'admin_only';
    gw.on('POST /auth/signup', (_) => switch (mode) {
          'admin_only' => Res.error(403, 'admin_only', '가입은 운영팀에 요청해 주세요 (관리 토큰이 필요한 API 입니다).'),
          'taken' => Res.error(409, 'login_id_taken', '이미 사용 중인 아이디입니다.'),
          'network' => throw http.ClientException('connection refused'),
          _ => Res.error(429, 'rate_limited', '잠시 후 다시 시도하세요.'),
        });
    final s = await signedOutSession(gw, MemoryTokenStore());
    await tester.pumpWidget(ChangeNotifierProvider.value(value: s, child: const GuryongApp()));
    await settle(tester);
    await tapText(tester, '아이디로 계속하기', frames: 15);
    await tapText(tester, '가입하기', frames: 15);
    await type(tester, '아이디', 'mentor9');
    await type(tester, '비밀번호', 'guryong2026');

    await tapText(tester, '가입하고 시작하기');
    expect(find.text(signupClosedMessage), findsOneWidget, reason: '토스트가 아니라 화면 안 안내');
    expect(find.byType(SnackBar), findsNothing);
    expect(find.textContaining('서버에 연결하지 못했어요'), findsNothing);

    mode = 'taken';
    await type(tester, '아이디', 'mentor10');
    await tapText(tester, '가입하고 시작하기');
    expect(find.text('이미 쓰는 아이디예요'), findsOneWidget);

    mode = 'network';
    await type(tester, '아이디', 'mentor11');
    await tapText(tester, '가입하고 시작하기');
    expect(find.text('서버에 연결하지 못했어요 (http://gateway.test)'), findsOneWidget);

    mode = 'other';
    await tester.pump(const Duration(seconds: 5));
    await type(tester, '아이디', 'mentor12');
    await tapText(tester, '가입하고 시작하기');
    expect(find.text('잠시 후 다시 시도하세요.'), findsOneWidget, reason: '처리하지 않은 오류는 서버 메시지');
  });

  testWidgets('34 로그인 오류 문구: 401 비밀번호 · 네트워크 → 연결(주소) · 그 밖 → 서버 메시지', (tester) async {
    await phone(tester);
    final gw = FixtureGateway([]);
    var mode = 'wrong';
    gw.on('POST /auth/login', (_) => switch (mode) {
          'wrong' => Res.error(401, 'invalid_credentials', '아이디 또는 비밀번호가 올바르지 않습니다.'),
          'network' => throw http.ClientException('connection refused'),
          _ => Res.error(500, 'internal', '서버 오류가 났어요.'),
        });
    final s = await signedOutSession(gw, MemoryTokenStore());
    await tester.pumpWidget(ChangeNotifierProvider.value(value: s, child: const GuryongApp()));
    await settle(tester);
    await tapText(tester, '아이디로 계속하기', frames: 15);
    await type(tester, '아이디', 'seoyun');
    await type(tester, '비밀번호', 'guryong2026');
    await tapText(tester, '로그인');
    expect(find.text('아이디 또는 비밀번호가 맞지 않아요'), findsOneWidget);
    expect(find.byType(SnackBar), findsNothing);

    mode = 'network';
    await type(tester, '비밀번호', 'guryong2026');
    await tapText(tester, '로그인');
    expect(find.text('서버에 연결하지 못했어요 (http://gateway.test)'), findsOneWidget);

    mode = 'other';
    await tester.pump(const Duration(seconds: 5));
    await tapText(tester, '로그인');
    expect(find.text('서버 오류가 났어요.'), findsOneWidget);
  });

  testWidgets('시작: 저장된 refresh 로 01 건너뜀 / refresh_token_reused → 01 + 안내', (tester) async {
    await phone(tester);
    final gw = FixtureGateway(['f11_login.json']);
    gw.on('POST /auth/refresh', (_) => Res.json(401, gw.response('refresh_reused')));
    final store = MemoryTokenStore(access: 'old', refresh: '<refresh_token>');
    final s = await signedOutSession(gw, store);
    expect(s.status, AuthStatus.restoring);
    await tester.pumpWidget(ChangeNotifierProvider.value(value: s, child: const GuryongApp()));
    await settle(tester, 20);
    expect(find.text('아이디로 계속하기'), findsOneWidget);
    expect(find.text('다른 곳에서 로그인 정보가 쓰여 다시 로그인해 주세요'), findsOneWidget);
    expect(store.refresh, isNull, reason: '토큰 삭제');
  });

  test('401 unauthorized → refresh 한 번(동시 요청도 한 번) → 원래 요청 재시도', () async {
    final gw = FixtureGateway([]);
    final s = await signedInSession(gw);
    var calls = 0;
    gw.on('GET /planner/plans', (r) => r.headers['Authorization'] == 'Bearer <access_token>' ? Res.json(200, []) : Res.error(401, 'unauthorized'));
    gw.overrides['POST /auth/refresh'] = (r) async {
      calls++;
      await Future<void>.delayed(const Duration(milliseconds: 20));
      return Res.json(200, (loadFixture('f11_login.json')['exchanges'] as List).firstWhere((e) => e['name'] == 'refresh')['response']);
    };
    expect(s.signedIn, isTrue);
    // 만료된 access 를 가진 세션
    final store = MemoryTokenStore(access: 'expired', refresh: '<refresh_token>');
    SharedPreferences.setMockInitialValues({'base_url': 'http://gateway.test'});
    final s2 = await AppSession.load(clientFactory: gw.client, tokenStore: store);
    final r = await Future.wait([s2.api.plans(), s2.api.plans(), s2.api.plans()]);
    expect(r, everyElement(isEmpty));
    expect(calls, 1, reason: 'refresh 는 1회용이라 한 번만');
    expect(gw.sent('GET', '/planner/plans').length, 6, reason: '3번 401 → 3번 재시도');
  });

  testWidgets('29: 지금 여행 중 카드 · 안전 배지(오늘~3일) · 먼 여행 "날씨는 MM/DD부터" · 새 경고 점', (tester) async {
    await phone(tester);
    PlanListScreen.today = () => DateTime(2026, 10, 10);
    addTearDown(() => PlanListScreen.today = null);
    final gw = FixtureGateway(['f5_delay.json', 'f6_safety.json']);
    final plans = [
      planJson([itemJson(1, 1, '구룡포 일본인 가옥거리', '12:30', '13:30')], id: 1, title: '엄마랑 구룡포', start: '2026-10-10', end: '2026-10-10'),
      planJson([itemJson(2, 1, '호미곶', '10:00', '11:00')], id: 2, title: '가족 포항 1박', start: '2026-10-11', end: '2026-10-12'),
      planJson([itemJson(3, 1, '호미곶', '06:00', '07:00')], id: 3, title: '호미곶 일출', start: '2026-10-21', end: '2026-10-21'),
    ];
    gw.on('GET /planner/plans', (_) => Res.json(200, plans.map((p) => Map.of(p)..remove('days')).toList()));
    gw.on('GET /planner/plans/{id}/proposals', (r) => Res.json(200, r.path.contains('/2/') ? [gw.response('proposal')] : []));
    gw.on('GET /trip/plans/{id}/state', (_) => Res.json(200, gw.response('state_after')));
    gw.on('GET /planner/plans/{id}/safety', (_) => Res.json(200, gw.response('compare_before_safety')));
    gw.on('GET /planner/plans/{id}/safety/alerts', (r) => Res.json(200, r.path.contains('/2/') ? [{'id': 1, 'level': 'danger', 'needs_action': true, 'status': 'new', 'message': '', 'changes': []}] : []));
    final s = await signedInSession(gw);
    await tester.pumpWidget(app(s, const PlanListScreen()));
    await settle(tester);

    expect(find.text('지금 여행 중'), findsOneWidget);
    expect(find.text('여행 중'), findsOneWidget);
    expect(find.text('지금 방문 중 · 다음 구룡포과메기문화관'), findsOneWidget);
    expect(find.text('동행 화면 열기'), findsOneWidget);
    expect(find.text('위험 · 3곳'), findsNWidgets(2), reason: '여행 중 + 내일 여행');
    expect(find.text('AI 제안 1개'), findsOneWidget);
    expect(find.text('날씨는 10/18부터 확인'), findsOneWidget);
    expect(gw.sent('GET', '/planner/plans/3/safety'), isEmpty, reason: '3일 넘게 남으면 부르지 않는다');
    expect(gw.sent('GET', '/trip/plans/{id}/state').map((r) => r.path), ['/trip/plans/1/state']);
    expect(find.text('한 줄만 말하면 구룡메이트가 일정 초안을 만들어요'), findsOneWidget);
  });

  testWidgets('31: 분류별 항목 · 지우기(확인 없음) → 되돌리기 · 빈 상태', (tester) async {
    await phone(tester);
    final gw = FixtureGateway(['f4_arrival_docent.json']);
    final mem = gw.response('memory_after') as Map<String, dynamic>;
    var items = [...(mem['items'] as List)];
    gw.on('GET /docent/memory', (_) => Res.json(200, {...mem, 'items': items}));
    gw.on('DELETE /docent/memory/{id}', (_) {
      items = [];
      return Res.json(204, null);
    });
    gw.on('POST /docent/memory', (r) => Res.json(201, {}));
    final s = await signedInSession(gw);
    await tester.pumpWidget(app(s, const MemoryScreen()));
    await settle(tester);
    expect(find.textContaining('구룡메이트가 대화에서 알게 된 것이에요'), findsOneWidget);
    expect(find.text('해설 스타일'), findsOneWidget);
    expect(find.text('해설은 핵심만 짧게'), findsOneWidget);
    expect(find.textContaining('모든 여행 ·'), findsOneWidget);
    await tester.tap(find.byTooltip('지우기'));
    await settle(tester);
    expect(gw.sent('DELETE', '/docent/memory/40'), hasLength(1));
    expect(find.text('아직 기억한 게 없어요'), findsOneWidget);
    expect(find.text('되돌리기'), findsOneWidget);
    await tester.tap(find.text('되돌리기'));
    await settle(tester);
    expect(gw.sent('POST', '/docent/memory').single.body, {'scope': 'user', 'category': 'style', 'text': '해설은 핵심만 짧게', 'source': 'user_edit'});
  });

  testWidgets('32: 노트 목록 → 가이드 준비 202 → 작업 폴링(진행) → 끝나면 노트 다시', (tester) async {
    await phone(tester);
    GuidePrepScreen.pollInterval = const Duration(milliseconds: 200);
    final gw = FixtureGateway(['f10_guide_prep.json', 'f1_plan_with_ai.json']);
    gw.on('GET /planner/plans/{id}', (_) => Res.json(200, (gw.response('accept') as Map)['plan']));
    gw.on('GET /planner/plans/{id}/safety', (_) => Res.json(200, gw.response('plan_safety')));
    gw.on('GET /planner/plans/{id}/safety/alerts', (_) => Res.json(200, []));
    final s = await signedInSession(gw);
    final c = PlanController(s.api, 240);
    await c.load();
    await tester.pumpWidget(app(s, GuidePrepScreen(plan: c)));
    await settle(tester);
    expect(find.text('5곳 중 0곳 준비됐어요'), findsOneWidget);
    expect(find.text('대기'), findsNWidgets(10), reason: '칩 + 문구');
    await tester.tap(find.text('가이드 준비'));
    await tester.pump(const Duration(milliseconds: 50));
    await settle(tester, 6);
    expect(find.text('준비 중…'), findsOneWidget);
    expect(find.text('자료를 찾는 중'), findsWidgets);
    expect(find.textContaining('약 '), findsOneWidget);
    await settle(tester, 30);
    expect(find.text('5곳 중 5곳 준비됐어요'), findsOneWidget);
    expect(find.text('준비됨 · 출처 2개'), findsWidgets);
    expect(find.text('모두 준비됐어요'), findsOneWidget);
    expect(gw.sent('GET', '/docent/plans/{id}/notes'), hasLength(2));
    expect(find.textContaining('지어내지 않아요'), findsOneWidget);
  });

  testWidgets('33: 계정 · 나에 대해 · 서버 연결 확인 · 가상 상황/시각 · 점검 실행(404 면 숨김) · 로그아웃', (tester) async {
    await phone(tester);
    AppConfig.demo = true;
    addTearDown(() => AppConfig.demo = false);
    final gw = FixtureGateway(['f4_arrival_docent.json']);
    var sim = simJson();
    gw.on('GET /health', (_) => Res.json(200, {'status': 'ok'}));
    gw.on('GET /agent/info', (_) => Res.json(200, {'provider': 'scripted:scripts'}));
    gw.on('GET /safety/sim', (_) => Res.json(200, sim));
    gw.on('PUT /safety/sim', (r) {
      final b = r.body as Map;
      sim = simJson(scenario: (b['scenario'] ?? sim['scenario']) as String, now: (b['now'] ?? sim['now']) as String);
      return Res.json(200, sim);
    });
    gw.on('GET /docent/memory', (_) => Res.json(200, gw.response('memory_after')));
    gw.on('POST /planner/safety/scan', (_) => Res.error(404, 'not_available'));
    gw.on('POST /auth/logout', (_) => Res.json(204, null));
    final s = await signedInSession(gw);
    await tester.pumpWidget(ChangeNotifierProvider.value(value: s, child: const GuryongApp()));
    await settle(tester);
    await tester.tap(find.byTooltip('설정'));
    await settle(tester, 20);

    expect(find.text('서윤'), findsOneWidget);
    expect(find.text('seoyun-demo · 아이디 로그인'), findsOneWidget);
    expect(find.text('1개'), findsOneWidget);
    expect(find.text('발표용'), findsOneWidget);
    expect(find.text('연결됨'), findsOneWidget);
    expect(find.text('agent: scripted:scripts · safety: 가상 상황'), findsOneWidget);
    expect(find.text('10/10 (토) 14:00 · 흐르는 중'), findsOneWidget);

    await tapText(tester, '내일 강풍');
    expect(gw.sent('PUT', '/safety/sim').last.body, {'scenario': 'wind_tomorrow'});
    expect(find.text('가상 상황: 내일 강풍'), findsOneWidget);
    await tapText(tester, '+10분');
    expect(DateTime.parse((gw.sent('PUT', '/safety/sim').last.body as Map)['now'] as String), DateTime.parse('2026-10-10T14:10:00+09:00'));
    await tapText(tester, '실제 시각');
    expect(gw.sent('PUT', '/safety/sim').last.body, {'reset_clock': true});

    await tester.scrollUntilVisible(find.text('안전 점검 지금 실행'), 200, scrollable: find.byType(Scrollable).first);
    await tapText(tester, '안전 점검 지금 실행');
    expect(find.text('안전 점검 지금 실행'), findsNothing, reason: '404 not_available → 행 숨김');

    await tester.scrollUntilVisible(find.text('로그아웃'), -200, scrollable: find.byType(Scrollable).first);
    await tapText(tester, '로그아웃', frames: 20);
    expect(gw.sent('POST', '/auth/logout').single.body, {'refresh_token': '<refresh_token>'});
    expect(find.text('아이디로 계속하기'), findsOneWidget, reason: '설정 화면을 걷어 내고 01');
  });

  testWidgets('01 "서버 설정" (로그인 전 축약판): 서버 주소 줄만', (tester) async {
    await phone(tester);
    final gw = FixtureGateway([]);
    gw.on('GET /health', (_) => Res.json(200, {'status': 'ok'}));
    gw.on('GET /agent/info', (_) => Res.error(401, 'unauthorized'));
    gw.on('GET /safety/sim', (_) => Res.error(404, 'sim_disabled'));
    final s = await signedOutSession(gw, MemoryTokenStore());
    await tester.pumpWidget(app(s, const SettingsScreen(serverOnly: true)));
    await settle(tester);
    expect(find.text('서버 주소'), findsOneWidget);
    expect(find.text('연결됨'), findsOneWidget);
    expect(find.text('safety: 실제 데이터'), findsOneWidget);
    expect(find.text('로그아웃'), findsNothing);
    expect(find.text('가상 상황'), findsNothing);
  });
}
