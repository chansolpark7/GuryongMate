// 05 AI 시트 스트리밍 · 06/06B 카드 동작 · 36 확인창 — 녹화 fixture 재생 (LLM 호출 없음).
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/models/agent.dart';
import 'package:guryong_mate/features/chat/chat_controller.dart';
import 'package:guryong_mate/features/planner/plan_controller.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';
import 'package:provider/provider.dart';

import 'support/fixture_gateway.dart';
import 'support/harness.dart';

/// 계획·세션 조회는 테스트가 가진 "지금 상태"로 답한다 (녹화의 조회는 흐름 중간 시점이라).
class PlanState {
  PlanState(this.gw, Map<String, dynamic> plan, {Map<String, dynamic>? session, Map<String, dynamic>? safety}) : current = plan {
    final id = plan['id'];
    gw.on('GET /planner/plans/{id}', (_) => Res.json(200, current));
    gw.on('GET /planner/plans/{id}/safety', (_) => Res.json(200, safety ?? emptySafety(id as int)));
    gw.on('GET /planner/plans/{id}/safety/alerts', (_) => Res.json(200, []));
    gw.on('POST /routing/route', (_) => Res.error(503, 'routing_unavailable'));
    gw.on('GET /safety/avoid', (_) => Res.json(200, {'zones': []}));
    if (session != null) {
      gw.on('GET /agent/sessions', (_) => Res.json(200, [session]));
      gw.on('GET /agent/sessions/{sid}', (_) => Res.json(200, sessionDetail ?? {...session, 'messages': []}));
    }
  }
  final FixtureGateway gw;
  Map<String, dynamic> current;
  Map<String, dynamic>? sessionDetail;
}

Map<String, dynamic> emptySafety(int id) => {
      'plan_id': id, 'plan_version': 1, 'checked_at': '2026-10-09T10:00:00+09:00', 'overall_risk': 'safe',
      'counts': {'safe': 0, 'caution': 0, 'danger': 0, 'unassessed': 0, 'info': 0}, 'items': [], 'data_ok': {},
    };

Future<void> openSheet(WidgetTester tester) async {
  await tester.tap(find.byTooltip('구룡메이트 열기').first);
  await settle(tester, 15);
}

Future<void> say(WidgetTester tester, String text) async {
  await tester.enterText(find.byType(TextField).last, text);
  await tester.pump();
  await tester.tap(find.byTooltip('보내기'));
  await tester.pump();
}

ChatController chatOf(WidgetTester tester) => Provider.of<ChatController>(tester.element(find.text('구룡메이트').first), listen: false);

void main() {
  setUpHarness();

  testWidgets('05 스트리밍: status 한 줄 + 단계(같은 id 갱신) → steps_done 접힘 → 답변 → 카드 → 승인', (tester) async {
    await phone(tester);
    final gw = FixtureGateway(['f1_plan_with_ai.json'], sseRealtime: true);
    final ps = PlanState(gw, gw.response('create_plan') as Map<String, dynamic>);
    gw.on('GET /agent/sessions/{sid}', (r) => Res.json(200, {...gw.response('create_session') as Map, 'messages': []}));
    gw.on('GET /planner/plans/{id}/safety', (_) => Res.json(200, ps.current['version'] == 1 ? emptySafety(240) : gw.response('plan_safety')));
    final session = await signedInSession(gw);
    await tester.pumpWidget(app(session, PlannerShell(planId: ps.current['id'] as int)));
    await settle(tester);

    expect(find.text('아직 일정이 없어요'), findsOneWidget, reason: '빈 상태 (States_Feedback)');
    await openSheet(tester);
    expect(find.text('구룡메이트'), findsOneWidget);
    expect(find.text('엄마랑 구룡포 · Day 1'), findsOneWidget);
    expect(find.text('날씨·안전 확인'), findsOneWidget, reason: '빠른 실행 c10');
    expect(find.text('AI Planner'), findsNothing);

    await say(tester, '엄마랑 구룡포 일정 짜줘');
    await tester.pump(const Duration(milliseconds: 1100));
    expect(find.text('기억해 두고 있어요'), findsOneWidget);
    expect(find.text('기억하기'), findsOneWidget);
    expect(find.byTooltip('중단'), findsOneWidget, reason: '응답 중에는 Stop');

    await tester.pump(const Duration(milliseconds: 600)); // 1700ms: 장소 찾기를 다시 하는 중 (같은 id 가 running 으로)
    expect(find.text('장소를 찾고 있어요'), findsOneWidget);
    expect(find.text('장소 찾기'), findsOneWidget, reason: '같은 id 는 한 줄');
    expect(find.text('잠시만요…'), findsOneWidget);

    await tester.pump(const Duration(milliseconds: 600)); // 2300ms: s2 마지막 결과 + s3 조건 반영
    expect(find.text('까멜리아인구룡포 외 4곳'), findsOneWidget);
    expect(find.text('구룡포 북방파제 외 2곳'), findsNothing, reason: '이전 결과는 갱신으로 사라진다');
    expect(find.text('18:00 전 종료'), findsOneWidget);

    await tester.pump(const Duration(milliseconds: 900)); // 3200ms: 검토 단계 결과
    expect(find.text('일정을 검토하고 있어요'), findsOneWidget);
    expect(find.text('문제 3건'), findsOneWidget);

    for (var i = 0; i < 40; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
    await until(tester, () => !chatOf(tester).busy);
    await settle(tester);
    expect(find.textContaining('8단계 완료'), findsOneWidget, reason: 'steps_done 으로 접힘');
    expect(find.text('기억하기'), findsNothing);
    expect(find.text('기억했어요 · 어머니와 동행'), findsOneWidget);
    expect(find.textContaining('17:38에 마쳐요'), findsOneWidget);
    expect(find.text('일정 변경 제안'), findsOneWidget);
    expect(find.text('변경 6건'), findsOneWidget);
    await reveal(tester, find.text('18시 전에 끝내기'));
    expect(find.textContaining('17:38 종료'), findsOneWidget);
    expect(find.text('적용 전까지 일정은 바뀌지 않아요 · '), findsOneWidget);

    // 펼치면 단계 목록
    await tapIn(tester, find.byIcon(Icons.expand_more), frames: 5);
    expect(find.text('변경안 만들기'), findsOneWidget);

    ps.current = (gw.response('accept') as Map)['plan'] as Map<String, dynamic>;
    await tapIn(tester, find.text('전체 적용'), frames: 30);
    expect(gw.sent('POST', '/agent/sessions/{s}/proposals/{p}/accept').single.body, {'mode': 'auto'});
    expect(find.text('구룡메이트'), findsNothing, reason: '승인하면 시트가 내려간다');
    expect(find.text('AI가 일정 5개를 변경했어요'), findsOneWidget);
    expect(find.text('실행 취소'), findsOneWidget);
    // 25: 안전 배지 + 구간 수단
    expect(find.text('구룡포 일본인 가옥거리'), findsOneWidget);
    expect(find.text('안전 · 실내'), findsWidgets);
    expect(find.text('차량 13분'), findsNothing, reason: '화면 밖');
    expect(gw.unmatched.where((u) => !u.contains('/routing')), isEmpty);

    // 26 지도 → [변경 내역]: 시트는 셸 Provider 밖이라 컨트롤러를 넘겨받아야 카드가 그려진다
    // (녹화는 빈 일정에서 시작해 지도에 이전 경로가 없다 → 이전 일정을 지금 일정으로 두고 버튼을 띄운다)
    final plan = Provider.of<PlanController>(tester.element(find.byType(IndexedStack).first), listen: false);
    final applied = plan.lastApplied!;
    plan.lastApplied = AppliedProposal(proposal: applied.proposal, before: plan.plan!, at: applied.at);
    plan.setTab(1);
    await settle(tester, 4);
    await tester.tap(find.text('변경 내역'));
    await settle(tester, 8);
    expect(tester.takeException(), isNull);
    expect(find.text('6건 적용됨'), findsOneWidget, reason: '읽기 전용 카드');
    expect(find.text('구룡포 일본인 가옥거리'), findsWidgets);
    await tester.tapAt(const Offset(195, 40)); // 바깥을 눌러 닫기
    await settle(tester, 8);
    plan.lastApplied = applied;
    plan.setTab(0);
    await settle(tester, 4);

    // 08 실행 취소 (승인 직후 스낵바에서만, c9): 승인 전 일정으로 되돌리는 ops
    gw.on('POST /planner/plans/{id}/ops', (r) => Res.json(200, gw.response('create_plan')));
    await tester.tap(find.text('실행 취소'));
    await settle(tester, 10);
    final ops = (gw.sent('POST', '/planner/plans/{id}/ops').single.body as Map)['ops'] as List;
    expect(ops.where((o) => o['op'] == 'remove_item'), hasLength(5));
    expect(find.text('변경 전 일정으로 되돌렸어요'), findsOneWidget);
  });

  testWidgets('06B 추천: 후보 state 로 버튼, 추가 → 409 already_in_plan(카드만 갱신) → 빼기', (tester) async {
    await phone(tester);
    final gw = FixtureGateway(['f1_plan_with_ai.json']);
    gw.queues['POST /agent/sessions/{session_id}/chat']!.removeAt(0);
    final sid = gw.param('chat_recommend_cafe', 'session_id');
    final ps = PlanState(gw, (gw.response('accept') as Map)['plan'] as Map<String, dynamic>,
        session: {'id': sid, 'plan_id': 240, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z'});
    final session = await signedInSession(gw);
    await tester.pumpWidget(app(session, const PlannerShell(planId: 240)));
    await settle(tester);
    await openSheet(tester);
    await say(tester, '갈 만한 카페 추천해줘');
    await until(tester, () => !chatOf(tester).busy);
    await settle(tester);

    expect(find.text('카페파도'), findsWidgets);
    expect(find.text('추가됨'), findsOneWidget, reason: '카페파도 in_plan');
    expect(find.text('일정에 추가'), findsOneWidget, reason: '까멜리아인구룡포 not_in_plan');
    expect(find.text('16:53 – 17:38'), findsOneWidget, reason: 'in_plan 은 일정 시각');

    await tapIn(tester, find.text('일정에 추가'), frames: 20);
    expect(gw.sent('POST', '/agent/sessions/{s}/recommendations/33/candidates/45/add'), hasLength(1));
    expect(find.text('추가됨'), findsNWidgets(2));
    expect(find.text('일정에 추가'), findsNothing);

    // 빼기
    await tapIn(tester, find.text('취소').last, frames: 20);
    expect(gw.sent('POST', '/agent/sessions/{s}/recommendations/33/candidates/45/remove'), hasLength(1));
    expect(find.text('일정에 추가'), findsOneWidget);

    // 오래된 카드에서 다시 추가 → 409 already_in_plan → GET 세션으로 카드만 서버 상태로 (스낵바 없음)
    final chat = chatOf(tester);
    final entry = chat.turns.last.cards.single as RecommendationEntry;
    final stale = RecommendationCandidate.fromJson(((gw.response('candidate_remove') as Map)['recommendation']['candidates'] as List)[1] as Map<String, dynamic>);
    ps.sessionDetail = gw.response('session_detail_end') as Map<String, dynamic>;
    final gets = gw.sent('GET', '/agent/sessions/{s}').length;
    await tester.runAsync(() => chat.toggleCandidate(entry, stale));
    await settle(tester, 10);
    expect(gw.sent('POST', '/agent/sessions/{s}/recommendations/33/candidates/45/add'), hasLength(2));
    expect(gw.sent('GET', '/agent/sessions/{s}').length, gets + 1);
    expect(find.text('일정에 추가'), findsOneWidget);
  });

  testWidgets('F2 · 36: base_changed 확인창 → 다시 제안 → 거절 → proposal_not_pending → base_changed → 그대로 반영 → agent_busy', (tester) async {
    await phone(tester);
    final gw = FixtureGateway(['f2_proposal_actions.json']);
    final f1 = loadFixture('f1_plan_with_ai.json');
    final start = ((f1['exchanges'] as List).firstWhere((e) => e['name'] == 'candidate_remove')['response'] as Map)['plan'] as Map<String, dynamic>;
    final sid = gw.param('chat_longer_lunch', 'session_id');
    final ps = PlanState(gw, start, session: {'id': sid, 'plan_id': 240, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z'});
    final session = await signedInSession(gw);
    await tester.pumpWidget(app(session, const PlannerShell(planId: 240)));
    await settle(tester);
    await openSheet(tester);
    await say(tester, '점심을 더 길게 해줘');
    await until(tester, () => !chatOf(tester).busy);
    await settle(tester);
    expect(find.text('일정 변경 제안'), findsOneWidget);

    // 1) 승인 → 409 base_changed → 36
    await tapIn(tester, find.text('전체 적용'), frames: 10);
    expect(find.text('이 제안 이후 일정이 바뀌었어요'), findsOneWidget);
    expect(find.text('사라지는 변경'), findsOneWidget);
    expect(find.text("'구룡포 일본인 가옥거리' 체류 60분 → 70분"), findsOneWidget);
    expect(find.text('현재 일정에 맞춰 다시 제안'), findsOneWidget);
    expect(find.text('카드에서 본 일정 그대로 반영'), findsOneWidget);

    // 2) 다시 제안 → repropose SSE → 새 카드, 이전 카드 대체됨
    await tester.tap(find.text('현재 일정에 맞춰 다시 제안'));
    await settle(tester, 5);
    await until(tester, () => !chatOf(tester).busy);
    await settle(tester);
    expect(find.text('이전 제안을 현재 일정에 맞춰 다시 만들어줘.'), findsOneWidget);
    final turns = chatOf(tester).turns;
    expect((turns.first.cards.single as ProposalEntry).state, ProposalState.superseded);
    await tester.drag(find.byType(ListView).last, const Offset(0, 2000));
    await settle(tester, 5);
    expect(find.text('새 제안으로 대체됨'), findsOneWidget, reason: '접힌 카드 (20)');
    await tester.drag(find.byType(ListView).last, const Offset(0, -4000));
    await settle(tester, 5);

    // 3) 거절 → 거절됨
    await tapIn(tester, find.text('거절').last, frames: 10);
    expect(find.text('거절했어요. 일정은 그대로예요.'), findsOneWidget);

    // 4) 오래된 카드(다른 기기에서 처리됨)를 다시 거절 → 409 proposal_not_pending → 세션 다시 읽고 스낵바
    final chat = chatOf(tester);
    final rejected = chat.turns.last.cards.single as ProposalEntry;
    rejected.state = ProposalState.pending;
    chat.notifyListeners();
    await settle(tester, 3);
    final before = gw.sent('GET', '/agent/sessions/{s}').length;
    await tapIn(tester, find.text('거절').last, frames: 10);
    expect(find.text('이미 처리된 제안이에요'), findsOneWidget);
    expect(gw.sent('GET', '/agent/sessions/{s}').length, greaterThan(before));

    // 5) 새 제안 → 409 base_changed → 그대로 반영(overwrite) → 200
    ScaffoldMessenger.of(tester.element(find.byType(Scaffold).first)).hideCurrentSnackBar();
    ps.current = (gw.response('accept_overwrite') as Map)['plan'] as Map<String, dynamic>;
    await say(tester, '점심을 더 길게 해줘');
    await until(tester, () => !chatOf(tester).busy);
    await settle(tester);
    await tapIn(tester, find.text('전체 적용'), frames: 10);
    await tester.tap(find.text('카드에서 본 일정 그대로 반영'));
    await settle(tester, 30);
    final accepts = gw.sent('POST', '/agent/sessions/{s}/proposals/119/accept');
    expect(accepts.map((r) => r.body), [
      {'mode': 'auto'},
      {'mode': 'overwrite'},
    ]);
    expect(find.textContaining('AI가 일정'), findsOneWidget, reason: '반영 + 실행 취소 스낵바');

    // 6) 다른 응답 중 → 409 agent_busy 스낵바
    await openSheet(tester);
    await say(tester, '카페 추천해줘');
    await until(tester, () => !chatOf(tester).busy);
    await settle(tester);
    await say(tester, '하나 더');
    await until(tester, () => !chatOf(tester).busy);
    await settle(tester, 5);
    await settle(tester, 20); // 앞 스낵바가 내려간 뒤
    expect(find.text('구룡메이트가 답하는 중이에요. 잠시 후 다시 눌러 주세요'), findsOneWidget);
    expect(find.text('하나 더'), findsNothing, reason: '시작되지 않은 말풍선은 거둔다');
  });
  testWidgets('05 시트 닫기: 헤더를 끝까지 끌어내리거나 X 를 두 번 눌러도 시트만 닫힌다 (계획 화면은 그대로)', (tester) async {
    await phone(tester);
    final gw = FixtureGateway(['f1_plan_with_ai.json']);
    PlanState(gw, (gw.response('accept') as Map)['plan'] as Map<String, dynamic>);
    final session = await signedInSession(gw);
    // 계획 목록 대신: PlannerShell 아래에 화면 하나를 깔아 두 번째 pop 을 잡는다
    await tester.pumpWidget(app(session, Builder(
      builder: (context) => Scaffold(body: Center(child: TextButton(
        onPressed: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => const PlannerShell(planId: 240))),
        child: const Text('목록'),
      ))),
    )));
    await tester.tap(find.text('목록'));
    await settle(tester);
    expect(find.byTooltip('구룡메이트 열기'), findsWidgets);

    // 헤더를 최소 높이 아래까지 끌면 시트가 스스로 닫히고, 손을 떼도 한 번 더 pop 하지 않는다
    await openSheet(tester);
    expect(find.byTooltip('닫기'), findsOneWidget);
    await tester.drag(find.text('구룡메이트').first, const Offset(0, 600));
    await settle(tester);
    expect(find.byTooltip('닫기'), findsNothing, reason: '시트는 닫힘');
    expect(find.byTooltip('구룡메이트 열기'), findsWidgets, reason: '계획 화면은 그대로');
    expect(find.text('목록'), findsNothing);

    // X 를 빠르게 두 번
    await openSheet(tester);
    await tester.tap(find.byTooltip('닫기'));
    await tester.pump(const Duration(milliseconds: 50));
    await tester.tap(find.byTooltip('닫기'), warnIfMissed: false);
    await settle(tester);
    expect(find.byTooltip('닫기'), findsNothing);
    expect(find.byTooltip('구룡메이트 열기'), findsWidgets);
    expect(find.text('목록'), findsNothing);
  });
  testWidgets('05 다시 열기: 이전 대화가 길면 최근 대화(맨 아래)부터 · 위로 올려 읽는 중엔 끌어내리지 않음', (tester) async {
    await phone(tester);
    final gw = FixtureGateway(['f1_plan_with_ai.json']);
    final ps = PlanState(gw, (gw.response('accept') as Map)['plan'] as Map<String, dynamic>,
        session: {'id': 'sess-long', 'plan_id': 240, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z'});
    ps.sessionDetail = {
      'id': 'sess-long', 'plan_id': 240, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z',
      'messages': [
        for (var n = 0; n < 15; n++) ...[
          {'id': n * 2 + 1, 'role': 'user', 'content': '질문 $n'},
          {'id': n * 2 + 2, 'role': 'assistant', 'content': '답변 $n입니다. 구룡포 일정에 대해 조금 길게 설명해 드릴게요.'},
        ],
      ],
    };
    final session = await signedInSession(gw);
    await tester.pumpWidget(app(session, const PlannerShell(planId: 240)));
    await settle(tester);

    await openSheet(tester);
    expect(find.textContaining('답변 14').hitTestable(), findsOneWidget, reason: '최근 대화가 보인다');
    expect(find.text('질문 0').hitTestable(), findsNothing);

    // 위로 올려 이전 대화를 읽는 중이면 그대로
    final list = find.descendant(of: find.byType(DraggableScrollableSheet).last, matching: find.byType(Scrollable)).first;
    for (var i = 0; i < 2; i++) {
      await tester.drag(list, const Offset(0, 300));
      await tester.pump(const Duration(milliseconds: 50));
    }
    await settle(tester, 10);
    expect(find.textContaining('답변 14').hitTestable(), findsNothing);

    // 닫았다가 다시 열면 다시 맨 아래
    await tester.tap(find.byTooltip('닫기'));
    await settle(tester, 10);
    await openSheet(tester);
    expect(find.textContaining('답변 14').hitTestable(), findsOneWidget);
  });
}
