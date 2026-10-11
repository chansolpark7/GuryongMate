// 현장 가이드(17)를 Today 에서 바로 쓰기 (2026-10-11, docs/app/today-map-redesign.md §7):
// 17 안에서 질문(place_id · 그 장소 메시지만 · 제안은 한 줄) · [다시 준비]/[가이드 준비] · 노트가 바뀌면 새 노트 · Today 가이드 줄 → 32.
// 여행 재생 fixture(trip/scene2)와 f4·f1 로 그린다 (LLM 호출 없음). 백엔드 place_id 는 대역 응답.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/widgets/common.dart';
import 'package:guryong_mate/features/chat/proposal_card.dart';
import 'package:guryong_mate/features/trip/guide_note_screen.dart';

import 'support/fixture_gateway.dart';
import 'support/harness.dart';
import 'support/trip_replay.dart';
import 'trip_screens_test.dart' show chatOf, openShell, tripOf;

const _house = 'kto-1909659'; // 구룡포 일본인 가옥거리
const _port = 'kto-2610501'; // 구룡포항

Map<String, dynamic> _session(TripReplay r, List<Map<String, dynamic>> messages) =>
    {'id': r.sessionId, 'plan_id': r.planId, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z', 'messages': messages};

Map<String, dynamic> _job(String status) => {
      'job_id': 'job-1', 'plan_id': 243, 'status': status, 'total': 1, 'finished': status == 'done' ? 1 : 0,
      'ready': status == 'done' ? 1 : 0, 'failed': 0, 'current': null, 'eta_sec': 20, 'places': [],
    };

Future<void> openGuide(WidgetTester tester, String placeId) async {
  tripOf(tester).onOpenGuide!(placeId);
  await settle(tester, 20);
  expect(find.text('현장 가이드'), findsOneWidget);
}

/// [cond] 가 될 때까지 짧은 주기(테스트용 pollInterval)를 흘린다.
Future<void> pumpUntil(WidgetTester tester, Finder f, {int tries = 40}) async {
  for (var i = 0; i < tries && f.evaluate().isEmpty; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
  await settle(tester, 4);
}

void main() {
  setUpHarness();

  setUp(() => GuideNoteScreen.pollInterval = const Duration(milliseconds: 100));
  tearDown(() => GuideNoteScreen.pollInterval = const Duration(seconds: 3));

  testWidgets('17 질문: 이 장소 메시지만 · 칩은 화면 안에서 바로(place_id) · 제안 카드는 한 줄 → AI 시트의 그 카드', (tester) async {
    final r = TripReplay('scene2_arrival', flows: ['f4_arrival_docent.json']);
    final f1 = FixtureGateway(['f1_plan_with_ai.json']);
    final withProposal = Map<String, dynamic>.of(((f1.response('session_detail') as Map)['messages'] as List).last as Map<String, dynamic>);
    r.session = _session(r, [
      {'id': 1, 'role': 'user', 'content': '구룡포항 주차는 어디예요?', 'place_id': _port},
      {'id': 2, 'role': 'assistant', 'content': '항구 앞 공영주차장이에요.', 'place_id': _port},
      {'id': 3, 'role': 'user', 'content': '점심은 어디서 먹을까요?'},
      {'id': 4, 'role': 'assistant', 'content': '대게 골목을 추천해요.'},
      {'id': 5, 'role': 'user', 'content': '계단이 많아요?', 'place_id': _house},
      {...withProposal, 'place_id': _house},
    ]);
    r.advanceTo('12:30:30');
    await openShell(tester, r.gw, r.planId);
    await openGuide(tester, _house);

    await tester.scrollUntilVisible(find.text('계단이 많아요?'), 300, scrollable: find.byType(Scrollable).first);
    await settle(tester, 4);
    expect(find.text('이 장소에서 나눈 이야기'), findsOneWidget);
    expect(find.text('계단이 많아요?'), findsOneWidget, reason: '이 장소(place_id) 메시지');
    expect(find.text('구룡포항 주차는 어디예요?'), findsNothing, reason: '다른 장소 메시지는 숨김');
    expect(find.text('점심은 어디서 먹을까요?'), findsNothing, reason: 'place_id 없는 대화도 숨김 (AI 시트에만)');
    expect(find.byType(ProposalCard), findsNothing, reason: '17 에는 카드 대신 한 줄');
    expect(find.text('일정 변경 제안을 만들었어요 ›'), findsOneWidget);

    // '이런 것도 물어보세요' 칩 → AI 시트로 넘기지 않고 이 화면에서 질문
    await tester.scrollUntilVisible(find.text('집들은 언제 지어졌어요?'), 200, scrollable: find.byType(Scrollable).first);
    await tester.tap(find.text('집들은 언제 지어졌어요?'));
    await settle(tester, 10);
    await until(tester, () => !chatOf(tester).busy);
    await settle(tester);
    final body = r.gw.sent('POST', '/agent/sessions/{sid}/chat').last.body as Map;
    expect(body['place_id'], _house);
    expect(body['message'], '집들은 언제 지어졌어요?');
    expect(find.text('현장 가이드'), findsOneWidget, reason: '17 그대로');
    expect(find.text('구룡메이트에게 요청하기…'), findsNothing, reason: 'AI 시트 안 열림');
    expect(find.textContaining('2단계 완료'), findsOneWidget, reason: '작업 단계 표시 = AI 시트와 같은 위젯');
    expect(chatOf(tester).turns.last.placeId, _house, reason: '같은 세션 — AI 시트에도 남는다');

    // 한 줄 → AI 시트의 그 제안 카드
    await tester.ensureVisible(find.byKey(const ValueKey('guide-card-link')));
    await tester.tap(find.byKey(const ValueKey('guide-card-link')));
    await settle(tester, 30);
    expect(find.text('현장 가이드'), findsNothing);
    expect(find.text('구룡메이트에게 요청하기…'), findsOneWidget);
    expect(find.byType(ProposalCard), findsOneWidget);
    expect(r.gw.unmatched, isEmpty);
  });

  testWidgets('17 stale: "관심사가 바뀌었어요 · [다시 준비]" → force 준비 → "다시 준비하고 있어요" → 작업이 끝나면 새 노트', (tester) async {
    final r = TripReplay('scene2_arrival', flows: ['f4_arrival_docent.json']);
    final note = r.flowResponse('note')!;
    var current = {...note, 'status': 'stale'};
    r.noteOverride = (_) => Res.json(200, current);
    Object? prepareBody;
    var polls = 0;
    r.gw.on('POST /agent/plans/{id}/guide/prepare', (q) {
      prepareBody = q.body;
      return Res.json(202, _job('running'));
    });
    r.gw.on('GET /agent/plans/{id}/guide/jobs/{jid}', (_) {
      if (++polls < 3) return Res.json(200, _job('running'));
      current = {...note, 'status': 'ready', 'short': '새로 준비한 짧은 해설이에요.'};
      return Res.json(200, _job('done'));
    });
    r.advanceTo('12:30:30');
    await openShell(tester, r.gw, r.planId);
    await openGuide(tester, _house);

    expect(find.byKey(const ValueKey('guide-stale')), findsOneWidget);
    expect(find.text('관심사가 바뀌었어요'), findsOneWidget);
    expect(find.text('관심사가 바뀌어 다시 준비할 수 있어요'), findsNothing, reason: '칩 대신 줄 + 버튼 (2026-10-11)');
    await tester.tap(find.text('다시 준비'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 20));
    expect(prepareBody, {'place_ids': [_house], 'force': true});
    expect(find.text('다시 준비하고 있어요'), findsOneWidget);
    expect(find.textContaining('일제강점기 구룡포에 정착한'), findsOneWidget, reason: '준비하는 동안 예전 노트는 그대로');

    await pumpUntil(tester, find.text('새로 준비한 짧은 해설이에요.'));
    expect(find.text('새로 준비한 짧은 해설이에요.'), findsOneWidget);
    expect(find.text('다시 준비하고 있어요'), findsNothing);
    expect(find.byKey(const ValueKey('guide-stale')), findsNothing);
    expect(polls, 3);
  });

  testWidgets('17 노트 없음: [가이드 준비] → 준비 중 → 새 노트', (tester) async {
    final r = TripReplay('scene2_arrival', flows: ['f4_arrival_docent.json']);
    final note = r.flowResponse('note')!;
    Map<String, dynamic>? current;
    r.noteOverride = (_) => current == null ? Res.error(404, 'note_not_found') : Res.json(200, current);
    r.gw.on('POST /agent/plans/{id}/guide/prepare', (_) => Res.json(202, _job('running')));
    r.gw.on('GET /agent/plans/{id}/guide/jobs/{jid}', (_) {
      current = note;
      return Res.json(200, _job('done'));
    });
    r.advanceTo('12:30:30');
    await openShell(tester, r.gw, r.planId);
    await openGuide(tester, _house);
    expect(find.text('준비된 해설이 없어요'), findsOneWidget);
    expect(find.text('지금 설명 듣기'), findsOneWidget);
    await tester.tap(find.text('가이드 준비'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 20));
    expect(find.text('해설을 준비하고 있어요'), findsOneWidget);
    await pumpUntil(tester, find.textContaining('일제강점기 구룡포에 정착한'));
    expect(find.textContaining('일제강점기 구룡포에 정착한'), findsOneWidget);
    expect(find.text('준비된 해설이 없어요'), findsNothing);
  });

  testWidgets('17 이 열린 채 서버가 자동 재준비(stale → pending → ready) 하면 새 노트로', (tester) async {
    final r = TripReplay('scene2_arrival', flows: ['f4_arrival_docent.json']);
    final note = r.flowResponse('note')!;
    var current = {...note, 'status': 'stale'};
    var summary = {'plan_id': r.planId, 'place_id': _house, 'status': 'stale', 'memory_version': 1, 'prepared_at': null, 'updated_at': '2026-10-10T03:00:00Z'};
    r.noteOverride = (_) => Res.json(200, current);
    r.gw.on('GET /docent/plans/{id}/notes', (_) => Res.json(200, [summary]));
    r.advanceTo('12:30:30');
    await openShell(tester, r.gw, r.planId);
    await openGuide(tester, _house);
    expect(find.text('관심사가 바뀌었어요'), findsOneWidget);

    summary = {...summary, 'status': 'pending', 'updated_at': '2026-10-10T03:31:00Z'}; // 도착 → 자동 준비
    await pumpUntil(tester, find.text('다시 준비하고 있어요'));
    expect(find.text('다시 준비하고 있어요'), findsOneWidget);

    current = {...note, 'status': 'ready', 'short': '자동으로 다시 준비한 해설이에요.'};
    summary = {...summary, 'status': 'ready', 'updated_at': '2026-10-10T03:31:40Z'};
    await pumpUntil(tester, find.text('자동으로 다시 준비한 해설이에요.'));
    expect(find.text('자동으로 다시 준비한 해설이에요.'), findsOneWidget);
    expect(find.text('다시 준비하고 있어요'), findsNothing);
    expect(find.text('관심사가 바뀌었어요'), findsNothing);
  });

  testWidgets('Today 남은 일정 아래 "가이드 노트 n/m 준비됨 · k곳 다시 준비 필요" / "준비 중" → 32', (tester) async {
    final r = TripReplay('scene2_arrival', flows: ['f4_arrival_docent.json']);
    Map<String, dynamic> s(String place, String status) => {'plan_id': r.planId, 'place_id': place, 'status': status, 'memory_version': 1, 'prepared_at': null, 'updated_at': '2026-10-10T03:00:00Z'};
    var notes = [s(_house, 'ready'), s(_port, 'stale'), s('kto-2660046', 'failed')];
    r.gw.on('GET /docent/plans/{id}/notes', (_) => Res.json(200, notes));
    r.advanceTo('12:30:30');
    await openShell(tester, r.gw, r.planId);

    final line = find.byKey(const ValueKey('today-guide-line'));
    final panel = find.descendant(of: find.byType(DraggableScrollableSheet).first, matching: find.byType(Scrollable)).first;
    await tester.drag(find.byType(Grabber).first, const Offset(0, -500)); // 패널을 끝까지 펼친다
    await settle(tester);
    await tester.scrollUntilVisible(line, 120, scrollable: panel);
    await settle(tester);
    expect(find.descendant(of: line, matching: find.text('가이드 노트 2/5 준비됨 · 1곳 다시 준비 필요', findRichText: true)), findsOneWidget);

    notes = [s(_house, 'ready'), s(_port, 'pending'), s('kto-2660046', 'failed')];
    await tripOf(tester).loadGuideNotes();
    await tester.pump();
    expect(find.descendant(of: line, matching: find.text('가이드 노트 준비 중 1/5', findRichText: true)), findsOneWidget);

    await tester.tap(line);
    await settle(tester, 20);
    expect(find.text('현장 가이드 준비'), findsOneWidget, reason: '32');
    expect(find.text('5곳 중 1곳 준비됐어요'), findsOneWidget);
    await tester.tap(find.byType(BackButton));
    await settle(tester, 30); // 화면 전환이 끝날 때까지
    expect(find.text('현장 가이드 준비'), findsNothing);
    expect(r.gw.sent('GET', '/docent/plans/{id}/notes').length, greaterThanOrEqualTo(3), reason: '동행 시작 · 직접 · 32 에서 돌아올 때');
    expect(r.gw.unmatched, isEmpty);
  });
}
