// 현장 가이드 대화·다시 준비·Today 가이드 줄 + 가벼운 마크다운 (docs/app/today-map-redesign.md §7) 변경 전·후 캡처.
// 같은 fixture 장면을 docs/app/review/guide-chat/{before|after}/*.png (390×844) 로 저장한다. 변경 전 코드(dev)에서도 돌도록 공용 API 만 쓴다.
//   flutter test test/capture_guide_chat_test.dart --dart-define=CAPTURE=true --dart-define=CAPTURE_DIR=../docs/app/review/guide-chat/after
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'capture_test.dart' show loadFonts;
import 'capture_today_map_test.dart' show captureTest, capture, dragSheet, flush, open, scene, snap, tripOf;
import 'support/fixture_gateway.dart';
import 'support/harness.dart' show setUpHarness;
import 'support/trip_replay.dart';

const _house = 'kto-1909659';

/// 백엔드(feat/guide-chat-api)가 쓰는 모양: 문단 · **굵게** · 목록.
const _longMd = '구룡포는 일제강점기 동해안 어업의 **전진기지**였어요. 어장이 풍요로워 일본인 어부와 상인들이 정착했고, 백화점·약국·공원 같은 근대 시설이 이 일대에 들어섰지요.\n\n'
    '해방 뒤 일본인들이 떠나면서 집들은 그대로 남았고, 지금도 **일본식 목조 건물 47채**가 있어요. 포항시는 2010년 이 거리를 아픈 역사를 기억하는 교육장으로 정비했어요.\n\n'
    '꼭 볼 곳:\n'
    '- **구룡포근대역사관** — 하시모토 젠기치가 살던 집\n'
    '- 공원 계단 위에서 내려다보는 어촌 풍경\n'
    '- 골목 안 옛 상점 간판\n\n'
    '어머니와 걷기 좋은 순서:\n'
    '1. 역사관부터 천천히\n'
    '2. 계단 대신 오른쪽 완만한 길로 공원\n'
    '3. 내려와서 항구 쪽 카페에서 쉬기';

const _planMd = '어머니와 걷기 편한 순서로 **다섯 곳**을 넣었어요.\n\n'
    '1. **12:30** 일본인 가옥거리에서 시작\n'
    '2. **13:40** 구룡포항에서 점심\n'
    '3. 과메기문화관 → 북방파제\n'
    '4. **17:38** 카페파도에서 마무리\n\n'
    '북방파제에서 카페까지는 멀어서 **차로 이동**하게 했고, 18시 전에 끝나도록 조건도 걸어 두었어요.';

Map<String, dynamic> _session(TripReplay r, List<Map<String, dynamic>> messages) =>
    {'id': r.sessionId, 'plan_id': r.planId, 'created_at': '2026-10-09T00:00:00Z', 'updated_at': '2026-10-09T00:00:00Z', 'messages': messages};

Future<void> _guide(WidgetTester tester, String placeId) async {
  tripOf(tester).onOpenGuide!(placeId);
  await flush(tester, 10);
}

Future<void> _scrollTo(WidgetTester tester, Finder f) async {
  if (f.evaluate().isEmpty) await tester.scrollUntilVisible(f, 300, scrollable: find.byType(Scrollable).first, maxScrolls: 15);
  await tester.ensureVisible(f);
  await flush(tester);
}

void main() {
  setUpHarness();
  setUpAll(() async {
    if (capture) await loadFonts();
  });

  captureTest('m 마크다운: 긴 해설 · 계획 답변', (tester) async {
    final r = scene('scene2_arrival', flows: ['f4_arrival_docent.json']);
    final note = r.flowResponse('note')!;
    r.noteOverride = (_) => Res.json(200, {...note, 'long': _longMd});
    r.session = _session(r, [
      {'id': 1, 'role': 'user', 'content': '어머니랑 구룡포 하루 일정 짜줘'},
      {'id': 2, 'role': 'assistant', 'content': _planMd},
    ]);
    r.advanceTo('12:30:30');
    await open(tester, r);
    await _guide(tester, _house);
    await tester.tap(find.text('자세히 · 2~3분'));
    await flush(tester);
    await snap(tester, 'm1_note_long');
    await tester.drag(find.byType(Scrollable).first, const Offset(0, -260));
    await flush(tester);
    await snap(tester, 'm1b_note_long_list');
    Navigator.of(tester.element(find.text('현장 가이드'))).pop();
    await flush(tester, 10);
    tripOf(tester).onOpenAi!();
    await flush(tester, 12);
    await snap(tester, 'm2_plan_answer');
  });

  captureTest('g 가이드 화면 안 대화 (이 장소 메시지만, 제안은 한 줄)', (tester) async {
    final r = scene('scene2_arrival', flows: ['f4_arrival_docent.json', 'f1_plan_with_ai.json']);
    final withProposal = Map<String, dynamic>.of((((r.gw.response('session_detail') as Map)['messages']) as List).last as Map<String, dynamic>);
    r.session = _session(r, [
      {'id': 1, 'role': 'user', 'content': '점심은 어디서 먹을까요?'},
      {'id': 2, 'role': 'assistant', 'content': '구룡포항 대게 골목을 추천해요.'},
      {'id': 5, 'role': 'user', 'content': '어머니랑 계단 오르기 힘들까요?', 'place_id': _house},
      {
        'id': 6, 'role': 'assistant', 'place_id': _house,
        'content': '공원 계단은 **115칸**이라 조금 가팔라요.\n\n- 역사관 옆 **오른쪽 완만한 길**로 돌아가면 계단 없이 올라가요\n- 중간 쉼터 벤치에서 한 번 쉬어 가세요',
      },
      {'id': 7, 'role': 'user', 'content': '그럼 공원은 빼고 카페를 먼저 가 볼까요?', 'place_id': _house},
      {...withProposal, 'place_id': _house, 'content': '공원 대신 카페를 먼저 가는 순서로 바꿔 볼게요.'},
    ]);
    r.advanceTo('12:30:30');
    await open(tester, r);
    await _guide(tester, _house);
    // 변경 전(dev)에는 대화 목록이 없다 → 출처까지
    try {
      await _scrollTo(tester, find.text('일정 변경 제안을 만들었어요 ›'));
    } catch (_) {
      await _scrollTo(tester, find.text('출처'));
    }
    await snap(tester, 'g1_note_talk');
  });

  captureTest('s 다시 준비 · 노트 없음 · Today 가이드 줄', (tester) async {
    final r = scene('scene2_arrival', flows: ['f4_arrival_docent.json']);
    final note = r.flowResponse('note')!;
    var current = <String, dynamic>{...note, 'status': 'stale'};
    r.noteOverride = (q) => q.path.endsWith('kto-2610501') ? Res.error(404, 'note_not_found') : Res.json(200, current);
    r.gw.on('POST /agent/plans/{id}/guide/prepare', (_) => Res.json(202, {'job_id': 'job-1', 'status': 'running', 'total': 1, 'finished': 0, 'ready': 0, 'failed': 0, 'eta_sec': 20, 'places': []}));
    r.gw.on('GET /agent/plans/{id}/guide/jobs/{jid}', (_) => Res.json(200, {'job_id': 'job-1', 'status': 'running', 'total': 1, 'finished': 0, 'ready': 0, 'failed': 0, 'eta_sec': 20, 'places': []}));
    Map<String, dynamic> s(String place, String status) => {'plan_id': r.planId, 'place_id': place, 'status': status, 'memory_version': 1, 'prepared_at': null, 'updated_at': '2026-10-10T03:00:00Z'};
    r.gw.on('GET /docent/plans/{id}/notes', (_) => Res.json(200, [s(_house, 'stale'), s('kto-2660046', 'ready'), s('kto-2834344', 'ready')]));
    r.advanceTo('12:30:30');
    await open(tester, r);

    await _guide(tester, _house);
    await snap(tester, 's1_note_stale');
    if (find.text('다시 준비').evaluate().isNotEmpty) {
      await tester.tap(find.text('다시 준비'));
      await flush(tester, 3);
      await snap(tester, 's2_note_repreparing');
    }
    Navigator.of(tester.element(find.text('현장 가이드'))).pop();
    await flush(tester, 10);

    await _guide(tester, 'kto-2610501');
    await snap(tester, 's3_no_note');
    Navigator.of(tester.element(find.text('현장 가이드'))).pop();
    await flush(tester, 10);

    await dragSheet(tester, -500);
    await tester.scrollUntilVisible(find.text('여행 종료'), 200,
        scrollable: find.descendant(of: find.byType(DraggableScrollableSheet).first, matching: find.byType(Scrollable)).first);
    await flush(tester);
    await snap(tester, 's4_today_guide_line');
  });
}
