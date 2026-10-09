import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:guryong_mate/core/app_session.dart';
import 'package:guryong_mate/features/chat/chat_controller.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';
import 'package:guryong_mate/main.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'fixtures.dart';

/// 게이트웨이를 흉내 내는 가짜 서버. 요청을 기록하고 정해 둔 응답을 돌려준다.
class FakeGateway {
  final requests = <String>[];
  final bodies = <String, Object?>{};

  final before = planJson([
    itemJson(1, 1, '구룡포 시장', '09:00', '10:30', stay: 90, lat: 35.9862, lng: 129.5553),
    itemJson(2, 2, '일본인 가옥거리', '11:00', '12:30', stay: 90, travel: 8, lat: 35.9905, lng: 129.5560),
    itemJson(3, 3, '구룡포 해수욕장 산책', '14:00', '15:00', travel: 12, lat: 35.9990, lng: 129.5640),
  ], violations: [
    {'code': 'end_before', 'severity': 'warning', 'message': '15:00 전에 야외 일정을 끝내야 해요', 'day': 1, 'item_id': 3, 'constraint_id': 7},
  ]);

  final after = planJson([
    itemJson(3, 1, '구룡포 해수욕장 산책', '09:00', '10:00', lat: 35.9990, lng: 129.5640),
    itemJson(1, 2, '구룡포 시장', '10:10', '11:40', stay: 90, travel: 10, lat: 35.9862, lng: 129.5553),
    itemJson(2, 3, '일본인 가옥거리', '12:00', '13:30', stay: 90, travel: 8, lat: 35.9905, lng: 129.5560),
  ]);

  late Map<String, dynamic> current = before;

  Map<String, dynamic> get proposal => {
        'id': 11,
        'plan_id': 1,
        'status': 'pending',
        'summary': '비 오기 전에 야외 산책을 오전으로 옮겼어요.',
        'source': 'llm',
        'origin': 's1',
        'ops': [
          {'op': 'update_item', 'item_id': 3, 'order': 1},
        ],
        'created_at': '2026-10-04T10:00:00Z',
        'decided_at': null,
        'changes': [
          {'kind': 'moved', 'message': "이동: '구룡포 해수욕장 산책' 1일차 3번째 → 1일차 1번째", 'item_id': 3, 'day': 1},
          {'kind': 'day_end', 'message': '1일차 종료: 15:00 → 13:30', 'item_id': null, 'day': 1},
        ],
        'before': before,
        'after': after,
        'applicable': true,
        'error': null,
      };

  static String sse(String event, Object data) => 'event: $event\ndata: ${jsonEncode(data)}\n\n';

  Future<http.StreamedResponse> handle(http.BaseRequest req, http.ByteStream body) async {
    final path = req.url.path;
    final key = '${req.method} $path';
    requests.add(key);
    final text = await body.bytesToString();
    bodies[key] = text.isEmpty ? null : jsonDecode(text);
    expect(req.headers['X-User-Id'], 'seoyun');

    http.StreamedResponse json(Object? data, [int status = 200]) => http.StreamedResponse(
          Stream.value(utf8.encode(jsonEncode(data))),
          status,
          headers: {'content-type': 'application/json'},
        );

    switch (key) {
      case 'GET /planner/plans':
        return json([Map.of(current)..removeWhere((k, _) => ['days', 'constraints', 'violations'].contains(k))]);
      case 'GET /planner/plans/1':
        return json(current);
      case 'GET /planner/plans/1/proposals':
        return json(<Object>[]);
      case 'GET /agent/sessions':
        return json(<Object>[]);
      case 'POST /agent/sessions':
        return json({'id': 's1', 'plan_id': 1, 'created_at': '2026-10-04T10:00:00Z', 'updated_at': '2026-10-04T10:00:00Z'}, 201);
      case 'GET /agent/sessions/s1':
        return json({
          'id': 's1',
          'plan_id': 1,
          'created_at': '2026-10-04T10:00:00Z',
          'updated_at': '2026-10-04T10:00:00Z',
          'messages': <Object>[],
          'pending_proposal': null,
        });
      case 'POST /agent/sessions/s1/chat':
        // 실제 서버처럼 조각조각 흘려보낸다
        final events = sse('start', {'session_id': 's1'}) +
            sse('tool_call', {'id': 'c1', 'name': 'view_plan', 'arguments': '{}'}) +
            sse('tool_result', {'id': 'c1', 'name': 'view_plan', 'ok': true, 'summary': '1일차 3곳'}) +
            sse('tool_call', {'id': 'c2', 'name': 'update_item', 'arguments': '{"item_id": 3, "order": 1}'}) +
            sse('tool_result', {'id': 'c2', 'name': 'update_item', 'ok': true, 'summary': '위반 0건'}) +
            sse('text', {'delta': '야외 산책을 '}) +
            sse('text', {'delta': '오전으로 옮겼어요.'}) +
            sse('proposal', proposal) +
            sse('done', {'text': '야외 산책을 오전으로 옮겼어요.', 'proposal_id': 11});
        final bytes = utf8.encode(events);
        final chunks = <List<int>>[for (var i = 0; i < bytes.length; i += 37) bytes.sublist(i, i + 37 > bytes.length ? bytes.length : i + 37)];
        return http.StreamedResponse(Stream.fromIterable(chunks), 200, headers: {'content-type': 'text/event-stream'});
      case 'POST /agent/sessions/s1/proposals/11/accept':
        current = after;
        return json({'proposal': {...proposal, 'status': 'accepted'}, 'plan': after});
      case 'POST /planner/plans/1/ops':
        current = before;
        return json(before);
    }
    return json({'detail': 'not found: $key'}, 404);
  }
}

Future<void> settle(WidgetTester tester, [int frames = 20]) async {
  for (var i = 0; i < frames; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  setUpAll(() {
    GoogleFonts.config.allowRuntimeFetching = false;
    // 지도 타일 캐시가 path_provider 로 캐시 폴더를 묻는다. 테스트에선 플러그인이 없어 임시 폴더로 답한다.
    final tmp = Directory.systemTemp.createTempSync('guryong_test').path;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(const MethodChannel('plugins.flutter.io/path_provider'), (_) async => tmp);
  });

  testWidgets('계획 열기 → AI 제안 스트림 → 전체 적용 → AI 배지 → 실행 취소', (tester) async {
    await tester.binding.setSurfaceSize(const Size(390, 844));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    SharedPreferences.setMockInitialValues({'user_id': 'seoyun', 'base_url': 'http://gateway.test'});
    final fake = FakeGateway();
    final session = await AppSession.load(clientFactory: () => MockClient.streaming(fake.handle));

    await tester.pumpWidget(ChangeNotifierProvider.value(value: session, child: const GuryongApp()));
    await settle(tester);

    // 02 Plan List
    expect(find.text('My Plans'), findsOneWidget);
    expect(find.text('구룡포 여행'), findsOneWidget);
    await tester.tap(find.text('구룡포 여행'));
    await settle(tester);

    // 04 Planner: 타임라인 + 위반 배너
    expect(find.text('구룡포 시장'), findsOneWidget);
    expect(find.text('일본인 가옥거리'), findsOneWidget);
    expect(find.textContaining('15:00 전에 야외 일정을 끝내야 해요'), findsOneWidget);
    expect(find.text('AI로 조정'), findsOneWidget);

    // 05 AI 시트 → 빠른 실행
    await tester.tap(find.byTooltip('AI Planner 열기').first);
    await settle(tester);
    expect(find.text('AI Planner'), findsOneWidget);
    await tester.tap(find.text('경로 최적화'));
    await settle(tester, 30);

    expect(fake.bodies['POST /agent/sessions/s1/chat'], {'message': 'Day 1 동선을 이동 시간이 짧아지도록 최적화해줘'});
    // 06 제안 카드
    expect(find.text('야외 산책을 오전으로 옮겼어요.'), findsOneWidget);
    expect(find.text('일정 변경 제안'), findsOneWidget);
    expect(find.text('변경 1건'), findsOneWidget);
    expect(find.text('이동'), findsWidgets);
    expect(find.textContaining('2단계 완료'), findsOneWidget);

    await tester.ensureVisible(find.text('전체 적용'));
    // 응답 스트림 종료(onDone)는 실제 이벤트 루프에서 처리되므로 runAsync 로 흘려보낸다
    final chat = Provider.of<ChatController>(tester.element(find.text('AI Planner')), listen: false);
    for (var i = 0; i < 50 && chat.busy; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
      await tester.pump();
    }
    expect(chat.busy, isFalse);
    await settle(tester, 5);
    await tester.tap(find.text('전체 적용'));
    await settle(tester, 30);
    expect(fake.requests, contains('POST /agent/sessions/s1/proposals/11/accept'));

    // 08 적용 후: 시트가 내려가고 AI 배지 + 스낵바
    expect(find.text('AI Planner'), findsNothing);
    expect(find.text('AI updated 14:00 → 09:00'), findsOneWidget);
    expect(find.text('AI가 일정 1개를 변경했어요'), findsOneWidget);

    await tester.tap(find.text('실행 취소'));
    await settle(tester, 30);
    final ops = (fake.bodies['POST /planner/plans/1/ops'] as Map)['ops'] as List;
    expect(ops.where((o) => o['op'] == 'update_item').map((o) => (o['item_id'], o['order'])), [(1, 1), (2, 2), (3, 3)]);
    expect(find.text('AI updated 14:00 → 09:00'), findsNothing);
  });

  testWidgets('"AI로 계획 만들기"로 들어오면 첫 요청을 자동으로 보낸다', (tester) async {
    await tester.binding.setSurfaceSize(const Size(390, 844));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    SharedPreferences.setMockInitialValues({'user_id': 'seoyun', 'base_url': 'http://gateway.test'});
    final fake = FakeGateway();
    final session = await AppSession.load(clientFactory: () => MockClient.streaming(fake.handle));
    await tester.pumpWidget(ChangeNotifierProvider.value(
      value: session,
      child: const MaterialApp(home: PlannerShell(planId: 1, initialAiPrompt: '구룡포읍에서 여행 일정을 짜줘.')),
    ));
    await settle(tester, 30);
    expect(fake.bodies['POST /agent/sessions/s1/chat'], {'message': '구룡포읍에서 여행 일정을 짜줘.'});
    expect(find.text('AI Planner'), findsOneWidget);
  });
}
