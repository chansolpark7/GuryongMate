// AI 시트 크게 보기/줄이기 아이콘 (시트 크기 리스너) · 일반 답 뒤 빠른 실행 칩 (2026-10-11).
//   캡처: flutter test test/chat_sheet_extras_test.dart --dart-define=CAPTURE=true → docs/app/review/chat-progress/*.png
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/features/chat/ai_sheet.dart';
import 'package:guryong_mate/features/chat/chat_controller.dart';
import 'package:guryong_mate/features/planner/plan_controller.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';
import 'package:provider/provider.dart';

import 'capture_test.dart' show loadFonts;
import 'support/fixture_gateway.dart';
import 'support/harness.dart';

const _capture = bool.fromEnvironment('CAPTURE');
final _root = GlobalKey();

Future<void> _snap(WidgetTester tester, String name) async {
  if (!_capture) return;
  await tester.pump(const Duration(milliseconds: 100));
  final boundary = _root.currentContext!.findRenderObject()! as RenderRepaintBoundary;
  await tester.runAsync(() async {
    final image = await boundary.toImage(pixelRatio: 1);
    final png = await image.toByteData(format: ui.ImageByteFormat.png);
    final file = File('../docs/app/review/chat-progress/$name.png');
    await file.parent.create(recursive: true);
    await file.writeAsBytes(png!.buffer.asUint8List());
  });
}

Map<String, dynamic> _safety(int id) => {
      'plan_id': id, 'plan_version': 1, 'checked_at': '2026-10-09T10:00:00+09:00', 'overall_risk': 'safe',
      'counts': {'safe': 0, 'caution': 0, 'danger': 0, 'unassessed': 0, 'info': 0}, 'items': [], 'data_ok': {},
    };

var _mid = 9000;

/// 카드 없는 일반 답 (단계 하나 → 답 글자 → done).
Res _plainAnswer(Req r) {
  final sid = r.path.split('/')[3];
  const answer = '구룡포항에서는 15시 무렵이 가장 한가해요. 지금 일정이면 그 시간에 지나가니 따로 바꿀 건 없어요.';
  return Res.sse([
    {'event': 'start', 'data': {'session_id': sid}},
    {'event': 'status', 'data': {'text': '일정을 확인하고 있어요.'}},
    {'event': 'step', 'data': {'id': 's1', 'kind': 'plan', 'status': 'done', 'title': '일정 확인', 'detail': '일정 5곳'}},
    {'event': 'text', 'data': {'delta': answer}},
    {'event': 'done', 'data': {'text': answer, 'cards': [], 'message_id': ++_mid}},
  ]);
}

/// f1 의 일정(240) · 빈 세션. 일반 답 대화는 [plain] 이면 _plainAnswer, 아니면 녹화(제안 카드).
Future<FixtureGateway> _open(WidgetTester tester, {bool plain = true}) async {
  await phone(tester);
  final gw = FixtureGateway(['f1_plan_with_ai.json']);
  final plan = (gw.response('accept') as Map)['plan'] as Map<String, dynamic>;
  gw.on('GET /planner/plans/{id}', (_) => Res.json(200, plan));
  gw.on('GET /planner/plans/{id}/safety', (_) => Res.json(200, _safety(240)));
  gw.on('GET /planner/plans/{id}/safety/alerts', (_) => Res.json(200, []));
  gw.on('POST /routing/route', (_) => Res.error(503, 'routing_unavailable'));
  gw.on('GET /safety/avoid', (_) => Res.json(200, {'zones': []}));
  gw.on('GET /agent/sessions/{sid}', (_) => Res.json(200, {...gw.response('create_session') as Map, 'messages': []}));
  if (plain) gw.on('POST /agent/sessions/{session_id}/chat', _plainAnswer);
  final session = await signedInSession(gw);
  await tester.pumpWidget(RepaintBoundary(key: _root, child: app(session, const PlannerShell(planId: 240))));
  await settle(tester);
  await tester.tap(find.byTooltip('구룡메이트 열기').first);
  await settle(tester, 15);
  return gw;
}

Future<void> _say(WidgetTester tester, String text) async {
  await tester.enterText(find.byType(TextField).last, text);
  await tester.pump();
  await tester.tap(find.byTooltip('보내기'));
  await tester.pump();
}

ChatController _chat(WidgetTester tester) => Provider.of<ChatController>(tester.element(find.text('구룡메이트').first), listen: false);
PlanController _plan(WidgetTester tester) => Provider.of<PlanController>(tester.element(find.text('구룡메이트').first), listen: false);

final _quick = find.byKey(const ValueKey('quick-actions'));

void _expectFull(WidgetTester tester, bool full) {
  expect(find.byTooltip(full ? '줄이기' : '크게 보기'), findsOneWidget);
  expect(find.byIcon(full ? Icons.close_fullscreen : Icons.open_in_full), findsOneWidget);
}

void main() {
  setUpHarness();
  setUpAll(() async {
    if (_capture) await loadFonts();
  });

  testWidgets('크게 보기/줄이기: 버튼 → 전체 → 다시 → 절반 · 끌어서 전체 · full 로 열기', (tester) async {
    await _open(tester);
    _expectFull(tester, false);

    await tester.tap(find.byTooltip('크게 보기'));
    await settle(tester, 10);
    _expectFull(tester, true);

    await tester.tap(find.byTooltip('줄이기'));
    await settle(tester, 10);
    _expectFull(tester, false);

    // 헤더를 손으로 위로 끌어 전체로
    await tester.drag(find.text('구룡메이트').first, const Offset(0, -400));
    await settle(tester, 10);
    _expectFull(tester, true);

    // 닫고 full 로 다시 열기 (선제 메시지에서 여는 경로)
    await tester.tap(find.byTooltip('닫기'));
    await settle(tester, 10);
    final ctx = tester.element(find.byTooltip('구룡메이트 열기').first);
    showAiSheet(ctx, plan: Provider.of<PlanController>(ctx, listen: false), chat: Provider.of<ChatController>(ctx, listen: false), full: true);
    await tester.pump();
    _expectFull(tester, true);
    await settle(tester, 15);
    _expectFull(tester, true);
  });

  testWidgets('일반 답 → 빠른 실행 칩 (방금 한 요청은 빼고) · 응답 중·입력 중 숨김 · 동행 중 문구', (tester) async {
    await _open(tester);
    await _say(tester, 'Day 1 날씨와 안전 확인해줘');
    await until(tester, () => !_chat(tester).busy);
    await settle(tester);

    expect(_quick, findsOneWidget);
    expect(find.descendant(of: _quick, matching: find.text('경로 최적화')), findsOneWidget);
    expect(find.descendant(of: _quick, matching: find.text('빈 시간 채우기')), findsOneWidget);
    expect(find.descendant(of: _quick, matching: find.text('날씨·안전 확인')), findsNothing, reason: '방금 한 요청과 같은 칩은 뺀다');
    await _snap(tester, '07-quick-actions-plan');

    // 입력 중이면 숨김
    await tester.enterText(find.byType(TextField).last, '음');
    await tester.pump();
    expect(_quick, findsNothing);
    await tester.enterText(find.byType(TextField).last, '');
    await tester.pump();
    expect(_quick, findsOneWidget);

    // 칩을 누르면 그 문구를 보낸다 → 응답 중엔 숨김 (이전 턴 아래에도 없다)
    await tester.tap(find.descendant(of: _quick, matching: find.text('경로 최적화')));
    await tester.pump();
    expect(_chat(tester).turns.last.user, 'Day 1 동선을 이동 시간이 짧아지도록 최적화해줘');
    expect(_chat(tester).busy, isTrue);
    expect(_quick, findsNothing);
    await until(tester, () => !_chat(tester).busy);
    await settle(tester);
    expect(_quick, findsOneWidget, reason: '마지막 턴 아래에만 하나');
    expect(find.descendant(of: _quick, matching: find.text('경로 최적화')), findsNothing);

    // 동행 중: 다음 일정 · 근처 볼거리
    _plan(tester).tripActive = true;
    await _say(tester, '여기 사람 많아?');
    await until(tester, () => !_chat(tester).busy);
    await settle(tester);
    expect(find.descendant(of: _quick, matching: find.text('다음 일정 알려줘')), findsOneWidget);
    expect(find.descendant(of: _quick, matching: find.text('근처 볼거리 추천해줘')), findsOneWidget);
    expect(find.descendant(of: _quick, matching: find.text('경로 최적화')), findsNothing);
    expect(find.descendant(of: _quick, matching: find.text('해설 더 짧게')), findsNothing, reason: '가이드 대화 맥락이 아니면');
    await _snap(tester, '08-quick-actions-trip');
  });

  testWidgets('제안 카드 답 → 빠른 실행 칩 없음 (후속 칩만)', (tester) async {
    final gw = await _open(tester, plain: false);
    gw.on('GET /planner/plans/{id}/safety', (_) => Res.json(200, gw.response('plan_safety')));
    await _say(tester, '엄마랑 구룡포 일정 짜줘');
    await until(tester, () => !_chat(tester).busy);
    await settle(tester);
    expect(find.text('일정 변경 제안'), findsOneWidget);
    expect(_quick, findsNothing);
    expect(find.text('이동 시간 더 줄여줘'), findsOneWidget, reason: '제안 카드 아래 후속 칩은 그대로');
  });

  test('가이드 대화 맥락(동행 중 · place_id)이면 "해설 더 짧게"', () {
    final t = ChatTurn('가옥거리 짧게 설명해줘', placeId: 'kto-1909659');
    expect(quickActions(t, day: 1, trip: true).map((a) => a.$1), ['다음 일정 알려줘', '근처 볼거리 추천해줘', '해설 더 짧게']);
    expect(quickActions(ChatTurn('다음 일정 알려줘'), day: 2, trip: true).map((a) => a.$1), ['근처 볼거리 추천해줘']);
    expect(quickActions(ChatTurn('안녕'), day: 2, trip: false).map((a) => a.$2), [
      'Day 2 동선을 이동 시간이 짧아지도록 최적화해줘',
      'Day 2 날씨와 안전 확인해줘',
      'Day 2 빈 시간에 갈 만한 곳 추천해줘',
    ]);
  });
}
