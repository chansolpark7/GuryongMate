import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/api/api_client.dart';
import 'package:guryong_mate/core/models/plan.dart';
import 'package:guryong_mate/core/models/safety.dart';
import 'package:guryong_mate/core/widgets/safety_widgets.dart';
import 'package:guryong_mate/features/chat/proposal_actions.dart';
import 'package:guryong_mate/features/chat/proposal_card.dart';
import 'package:guryong_mate/features/planner/restore_ops.dart';

import 'fixtures.dart';

/// backend/app/planner/ops.py 의 Applier 와 같은 규칙으로 (day, order) 만 시뮬레이션한다.
Map<int, (int, int)> simulate(PlanDetail current, List<Map<String, dynamic>> ops) {
  final pos = {for (final i in current.allItems) i.id: (i.day, i.order)};
  var nextTemp = -1;
  final tempToId = <int, int>{};
  var newId = 1000;

  void insert(int id, int day, int? order) {
    final others = pos.entries.where((e) => e.value.$1 == day && e.key != id).toList()..sort((a, b) => a.value.$2.compareTo(b.value.$2));
    final idx = order == null ? others.length : (order - 1).clamp(0, others.length);
    final ids = others.map((e) => e.key).toList()..insert(idx, id);
    for (var n = 0; n < ids.length; n++) {
      pos[ids[n]] = (day, n + 1);
    }
  }

  void renumber(int day) {
    final ids = pos.entries.where((e) => e.value.$1 == day).toList()..sort((a, b) => a.value.$2.compareTo(b.value.$2));
    for (var n = 0; n < ids.length; n++) {
      pos[ids[n].key] = (day, n + 1);
    }
  }

  for (final op in ops) {
    switch (op['op']) {
      case 'remove_item':
        final day = pos.remove(op['item_id'])!.$1;
        renumber(day);
      case 'add_item':
        final id = newId++;
        tempToId[nextTemp--] = id;
        insert(id, op['day'] as int, op['order'] as int?);
      case 'update_item':
        var id = op['item_id'] as int;
        if (id < 0) id = tempToId[id]!;
        if (op.containsKey('day') || op.containsKey('order')) {
          final old = pos[id]!.$1;
          final day = (op['day'] as int?) ?? old;
          insert(id, day, op['order'] as int?);
          if (old != day) renumber(old);
        }
    }
  }
  return pos;
}

void main() {
  group('안전 문구 (25 근거 규칙)', () {
    Hazard h(String type, String level, String reason) => Hazard(type: type, level: level, reason: reason, source: '기상청 단기예보');
    test('근거 → 짧은 값, 숫자를 못 뽑으면 종류 이름', () {
      expect(shortReason(h('wind', 'danger', '10/10 15시 풍속 16m/s 예보')), '풍속 16m/s');
      expect(shortReason(h('wave', 'danger', '10/10 16시 파고 3.5m 예보')), '파고 3.5m');
      expect(shortReason(h('rain', 'caution', '10/05 13~15시 강수 최대 12mm/h 예보')), '시간당 12mm');
      expect(shortReason(h('rain', 'info', '10/10 16~18시 강수 최대 8mm/h 예보')), '약한 비');
      expect(shortReason(h('flood', 'danger', '구룡포수협_스마트맨홀 수위 등급 3 (위험)')), '수위 등급 3');
      expect(shortReason(h('warning', 'danger', '풍랑주의보 발효 중 (경북남부앞바다)')), '풍랑주의보');
      expect(shortReason(h('wind', 'caution', '바람 강함')), '바람');
    });
    SafetyItem item(Map<String, dynamic> j) => SafetyItem.fromJson({'item_id': 1, 'kind': 'visit', 'title': 'x', 'hazards': [], ...j});
    test('배지: null 은 판단 불가/예보 전, past 는 숨김, 실내는 "안전 · 실내"', () {
      expect(riskBadgeLabel(item({'risk': 'safe', 'sensitivity': ['실내']})), '안전 · 실내');
      expect(riskBadgeLabel(item({'risk': null, 'unassessed_reason': 'data_missing'})), '판단 불가 · 데이터 없음');
      expect(riskBadgeLabel(item({'risk': null, 'unassessed_reason': 'beyond_forecast', 'recheck_from': '2026-12-17'})), '예보 전 · 12/17부터');
      expect(riskBadgeLabel(item({'risk': null, 'unassessed_reason': 'past'})), isNull);
      expect(riskColors(null).$1, isNot(riskColors('safe').$1));
    });
    test('규칙 문구는 괄호 안 사람 말만 (코드 노출 금지)', () {
      expect(ruleText("규칙 추가: end_before {'time': '18:00'} (18시 전에 끝내기)"), '18시 전에 끝내기');
      expect(ruleText("규칙 추가: must_include {'place_id': 'kto-1'}"), '여행 조건');
    });
  });

  group('SseParser', () {
    test('조각이 줄 중간에서 끊겨도 이벤트를 복원한다', () {
      final p = SseParser();
      final events = [
        ...p.feed('event: text\ndata: {"del'),
        ...p.feed('ta": "안녕"}\n\nevent: tool_call\r\n'),
        ...p.feed('data: {"id": "c1", "name": "search_places", "arguments": "{}"}\r\n\r\n'),
        ...p.feed('event: done\ndata: {"text": "끝", "proposal_id": null}\n\n'),
      ];
      expect(events.map((e) => e.event), ['text', 'tool_call', 'done']);
      expect(events[0].data['delta'], '안녕');
      expect(events[1].data['name'], 'search_places');
      expect(events[2].data['text'], '끝');
    });
  });

  group('buildRestoreOps', () {
    final before = PlanDetail.fromJson(planJson([
      itemJson(1, 1, '구룡포 시장', '09:00', '10:30'),
      itemJson(2, 2, '일본인 가옥거리', '11:00', '12:30', travel: 8),
      itemJson(3, 3, '해수욕장 산책', '14:00', '15:00', travel: 12),
      itemJson(4, 1, '호미곶', '10:00', '11:00', day: 2),
    ]));

    test('순서 변경 · 삭제 · 추가 · 날짜 이동을 모두 되돌린다', () {
      // AI 가 한 일: 3 을 맨 앞으로, 2 를 Day 2 로, 4 삭제, 새 항목 9 추가
      final current = PlanDetail.fromJson(planJson([
        itemJson(3, 1, '해수욕장 산책', '09:00', '10:00'),
        itemJson(1, 2, '구룡포 시장', '10:10', '11:40', travel: 10),
        itemJson(9, 3, '과메기문화관', '12:00', '13:00', travel: 5),
        itemJson(2, 1, '일본인 가옥거리', '10:00', '11:30', day: 2),
      ]));
      final ops = buildRestoreOps(before, current);
      expect(ops.first, {'op': 'remove_item', 'item_id': 9});
      final readded = ops.where((o) => o['op'] == 'add_item').toList();
      expect(readded, hasLength(1));
      expect(readded.first['place_id'], 'kto-4');

      final pos = simulate(current, ops);
      expect(pos[1], (1, 1));
      expect(pos[2], (1, 2));
      expect(pos[3], (1, 3));
      expect(pos.containsKey(9), isFalse);
      // 다시 만든 호미곶(새 id) 은 Day 2 첫 번째
      expect(pos.entries.where((e) => e.key >= 1000).single.value, (2, 1));
    });

    test('바뀐 게 없으면 순서·체류만 같은 값으로 다시 쓴다 (계획 정보 op 없음)', () {
      final ops = buildRestoreOps(before, before);
      expect(ops.every((o) => o['op'] == 'update_item'), isTrue);
      expect(simulate(before, ops), {for (final i in before.allItems) i.id: (i.day, i.order)});
    });

    test('고정 시각이 없던 항목은 start_time: null 로 고정을 푼다', () {
      final current = PlanDetail.fromJson(planJson([
        itemJson(1, 1, '구룡포 시장', '09:00', '10:30', startTime: '09:00'),
        itemJson(2, 2, '일본인 가옥거리', '11:00', '12:30'),
        itemJson(3, 3, '해수욕장 산책', '14:00', '15:00'),
        itemJson(4, 1, '호미곶', '10:00', '11:00', day: 2),
      ]));
      final op = buildRestoreOps(before, current).firstWhere((o) => o['item_id'] == 1);
      expect(op.containsKey('start_time'), isTrue);
      expect(op['start_time'], isNull);
    });
  });

  group('buildChangeRows', () {
    test('같은 자리의 삭제+추가는 교체 한 줄로 묶는다', () {
      final before = planJson([
        itemJson(1, 1, '구룡포 시장', '09:00', '10:30'),
        itemJson(4, 2, '호미곶 해맞이광장', '15:30', '17:00'),
      ]);
      final after = planJson([
        itemJson(1, 1, '구룡포 시장', '09:00', '10:30'),
        itemJson(-1, 2, '과메기문화관', '16:00', '17:00'),
      ]);
      final p = ProposalDetail.fromJson({
        'id': 5,
        'plan_id': 1,
        'status': 'pending',
        'summary': '',
        'source': 'llm',
        'origin': 's1',
        'ops': [
          {'op': 'remove_item', 'item_id': 4},
          {'op': 'add_item', 'place_id': 'kto--1', 'day': 1, 'order': 2},
        ],
        'created_at': '2026-10-04T10:00:00Z',
        'decided_at': null,
        'changes': [
          {'kind': 'added', 'message': "추가: 1일차 2번째 '과메기문화관' (60분)", 'item_id': -1, 'day': 1},
          {'kind': 'removed', 'message': "삭제: 1일차 '호미곶 해맞이광장'", 'item_id': 4, 'day': 1},
        ],
        'before': before,
        'after': after,
        'applicable': true,
      });
      final rows = buildChangeRows(p);
      expect(rows, hasLength(1));
      expect(rows.single.badge, '교체');
      expect(rows.single.fromText, '호미곶 해맞이광장');
      expect(rows.single.toText, '과메기문화관');
      expect(rows.single.title, '16:00 과메기문화관', reason: '제목은 바뀐 뒤 시각·장소');
    });
  });
}
