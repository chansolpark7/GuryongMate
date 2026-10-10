// SSE 재생 (LLM 호출 없이 녹화 fixture): 진행 단계가 순서대로 갱신·접힘, 카드가 나타남, 오류 이벤트.
import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/api/api_client.dart';
import 'package:guryong_mate/core/models/agent.dart';
import 'package:guryong_mate/core/models/plan.dart';
import 'package:guryong_mate/features/chat/chat_controller.dart';
import 'package:guryong_mate/features/planner/plan_controller.dart';

import 'support/fixture_gateway.dart';
import 'support/harness.dart';

Future<ChatController> openChat(FixtureGateway gw, {int? planId}) async {
  final s = await signedInSession(gw);
  final id = planId ?? (gw.response('create_plan') as Map)['id'] as int;
  final plan = PlanController(s.api, id);
  final chat = ChatController(s.api, plan);
  await chat.init();
  return chat;
}

Future<void> waitIdle(ChatController chat) async {
  for (var i = 0; i < 200 && chat.busy; i++) {
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }
}

void main() {
  test('F1 "엄마랑 구룡포 일정 짜줘": status 한 줄 교체, 같은 id 단계 갱신, steps_done 접힘, 제안 카드', () async {
    final gw = FixtureGateway(['f1_plan_with_ai.json']);
    final chat = await openChat(gw);
    expect(chat.sessionId, gw.param('chat_make_plan', 'session_id'));
    expect(gw.sent('GET', '/agent/sessions/{id}'), isEmpty, reason: '새로 만든 세션은 기록을 다시 읽지 않는다');

    final statuses = <String>[];
    final stepIdsSeen = <String>[];
    var collapsedBeforeText = false;
    chat.addListener(() {
      final t = chat.turns.last;
      if (t.status != null && (statuses.isEmpty || statuses.last != t.status)) statuses.add(t.status!);
      for (final s in t.steps) {
        if (!stepIdsSeen.contains(s.id)) stepIdsSeen.add(s.id);
      }
      if (t.collapsed && t.text.isEmpty) collapsedBeforeText = true;
    });
    await chat.send('엄마랑 구룡포 일정 짜줘');
    await waitIdle(chat);

    final t = chat.turns.single;
    expect(gw.sent('POST', '/agent/sessions/{id}/chat').single.body, {'message': '엄마랑 구룡포 일정 짜줘'});
    expect(statuses.first, '기억해 두고 있어요.');
    expect(statuses, containsAllInOrder(['장소를 찾고 있어요.', '조건을 반영하고 있어요.', '일정을 조정하고 있어요.', '일정을 검토하고 있어요.', '변경안을 만들고 있어요.']));
    // 같은 id(s2 장소 찾기는 5번 running→done)는 한 줄로 갱신된다
    expect(stepIdsSeen, ['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8']);
    expect(t.steps.map((s) => s.title), ['기억하기', '장소 찾기', '조건 반영', '일정 조정', '일정 검토', '일정 조정', '일정 검토', '변경안 만들기']);
    expect(t.steps.firstWhere((s) => s.id == 's2').detail, '까멜리아인구룡포 외 4곳', reason: '마지막 결과');
    expect(t.steps.every((s) => s.status == 'done'), isTrue);
    expect(collapsedBeforeText, isFalse, reason: 'text 가 steps_done 보다 먼저 온다');
    expect(t.collapsed, isTrue);
    expect(t.stepsSummary, startsWith('8단계 완료'));
    expect(t.remembered.map((s) => s.detail), ['어머니와 동행']);
    expect(t.text.toString(), contains('17:38에 마쳐요'));
    final card = t.cards.single as ProposalEntry;
    expect(card.state, ProposalState.pending);
    expect(card.proposal.changes.where((c) => c.kind == 'added'), hasLength(5));
    expect(t.done, isTrue);
    expect(t.error, isNull);
  });

  test('추천 카드 SSE: recommendation 이 텍스트보다 먼저 와도 카드로 남는다', () async {
    final gw = FixtureGateway(['f1_plan_with_ai.json']);
    gw.queues['POST /agent/sessions/{session_id}/chat']!.removeAt(0); // 일정 짜기 턴 건너뜀
    final chat = await openChat(gw);
    await chat.send('갈 만한 카페 추천해줘');
    await waitIdle(chat);
    final rec = (chat.turns.single.cards.single as RecommendationEntry).recommendation;
    expect(rec.candidates.map((c) => (c.title, c.state)), [('카페파도', 'in_plan'), ('까멜리아인구룡포', 'not_in_plan')]);
    expect(chat.turns.single.stepsSummary, isNotNull);
  });

  test('error 이벤트 llm_unavailable → 고정 문구 + 다시 시도', () async {
    final gw = FixtureGateway(['errors.json']);
    gw.on('GET /agent/sessions', (_) => Res.json(200, []));
    gw.on('POST /agent/sessions', (_) => Res.json(201, {'id': gw.param('chat_llm_unavailable', 'session_id'), 'plan_id': 1, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z'}));
    final chat = await openChat(gw, planId: 1);
    await chat.send('엄마랑 구룡포 일정 짜줘');
    await waitIdle(chat);
    final t = chat.turns.single;
    expect(t.error, '지금은 답할 수 없어요. 잠시 후 다시 시도해 주세요.');
    expect(t.errorCode, 'llm_unavailable');
    expect(t.retryable, isTrue);
    // 각본에 없는 말 → 고정 안내 (text 만, 카드 없음)
    chat.retry(t);
    await waitIdle(chat);
    expect(chat.turns.single.text.toString(), contains('각본'));
    expect(chat.turns.single.cards, isEmpty);
  });

  test('409 agent_busy (스트림 시작 전) → 말풍선을 거두고 알림', () async {
    final gw = FixtureGateway(['f2_proposal_actions.json']);
    final sid = gw.param('chat_agent_busy', 'session_id');
    gw.on('GET /agent/sessions', (_) => Res.json(200, [
          {'id': sid, 'plan_id': 1, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z'}
        ]));
    gw.on('GET /agent/sessions/{sid}', (_) => Res.json(200, {'id': sid, 'plan_id': 1, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z', 'messages': []}));
    gw.on('POST /agent/sessions/{sid}/chat', (_) => Res.json(409, gw.response('chat_agent_busy')));
    final chat = await openChat(gw, planId: 1);
    Object? notice;
    chat.onNotice = (e) => notice = e;
    await chat.send('카페 추천해줘');
    await waitIdle(chat);
    expect(chat.turns, isEmpty);
    expect((notice as ApiException).code, 'agent_busy');
  });

  test('다시 제안(repropose) SSE → 새 카드, 이전 제안은 superseded', () async {
    final gw = FixtureGateway(['f2_proposal_actions.json']);
    final sid = gw.param('chat_longer_lunch', 'session_id');
    gw.on('GET /agent/sessions', (_) => Res.json(200, [
          {'id': sid, 'plan_id': 1, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z'}
        ]));
    gw.on('GET /agent/sessions/{sid}', (_) => Res.json(200, {'id': sid, 'plan_id': 1, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z', 'messages': []}));
    final chat = await openChat(gw, planId: 1);
    await chat.send('점심을 더 길게');
    await waitIdle(chat);
    final first = chat.turns.single.cards.single as ProposalEntry;
    expect(() => chat.accept(first), throwsA(isA<ApiException>().having((e) => e.code, 'code', 'base_changed')));
    await Future<void>.delayed(const Duration(milliseconds: 50));
    expect(first.state, ProposalState.pending);
    chat.repropose(first);
    await waitIdle(chat);
    expect(chat.turns.last.user, '이전 제안을 현재 일정에 맞춰 다시 만들어줘.');
    final second = chat.turns.last.cards.single as ProposalEntry;
    expect(second.proposal.id, isNot(first.proposal.id));
    expect(first.state, ProposalState.superseded);
    expect(second.state, ProposalState.pending);
  });

  test('대화 기록 → 턴: 선제 메시지(사용자 말 없음)도 턴, 카드는 서버 상태로', () {
    final f6 = loadFixture('f6_safety.json');
    final s = (f6['exchanges'] as List).firstWhere((e) => e['name'] == 'session_with_proactive')['response'] as Map<String, dynamic>;
    final d = AgentSessionDetail.fromJson(s);
    final turns = ChatController.turnsFromHistory(d.messages);
    final last = turns.last;
    expect(last.user, isEmpty, reason: '선제 메시지');
    expect(last.text.toString(), contains('근대문화역사관'));
    final p = (last.cards.single as ProposalEntry).proposal;
    expect(p, isA<ProposalDetail>());
    expect((last.cards.single as ProposalEntry).state, ProposalState.pending);
  });
  test('AI 변경 표시(배지·초록 배너·지도 변경 내역)는 마지막 AI 변경에서 aiMarkTtl 이 지나면 사라진다', () async {
    final gw = FixtureGateway(['f1_plan_with_ai.json']);
    final s = await signedInSession(gw);
    final accept = gw.response('accept') as Map<String, dynamic>;
    final proposal = ProposalDetail.fromJson(accept['proposal'] as Map<String, dynamic>);
    final after = PlanDetail.fromJson(accept['plan'] as Map<String, dynamic>);
    fakeAsync((fa) {
      final plan = PlanController(s.api, after.id, aiMarkTtl: const Duration(minutes: 10));
      var notified = 0;
      plan.addListener(() => notified++);
      plan.plan = PlanDetail.fromJson(gw.response('create_plan') as Map<String, dynamic>);
      plan.applyAccepted(proposal, after);
      expect(plan.lastApplied, isNotNull);
      expect(plan.aiUpdates, isNotEmpty);

      fa.elapse(const Duration(minutes: 9));
      expect(plan.aiUpdates, isNotEmpty);
      // 추천 [추가]도 AI 변경 → 그때부터 다시 10분
      plan.markAiAdded(after.allItems.first.id, '구룡메이트 추천');
      fa.elapse(const Duration(minutes: 9));
      expect(plan.lastApplied, isNotNull);
      final before = notified;
      fa.elapse(const Duration(minutes: 1, seconds: 1));
      expect(plan.lastApplied, isNull);
      expect(plan.aiUpdates, isEmpty);
      expect(notified, greaterThan(before), reason: '화면이 다시 그려지도록 알린다');
      plan.dispose();
    });
  });
}
