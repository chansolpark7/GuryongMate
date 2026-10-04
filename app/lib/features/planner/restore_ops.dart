import '../../core/format.dart';
import '../../core/models/plan.dart';

/// [current] 계획을 [before] 상태로 되돌리는 planner ops 목록.
/// "실행 취소"에 쓴다. 백엔드에 undo API 가 없어서 차이를 ops 로 만들어 /ops 로 원자 적용한다.
///
/// 순서가 중요하다.
/// 1. before 에 없는 항목·규칙을 먼저 지운다.
/// 2. before 항목을 (day, order) 오름차순으로 하나씩 제자리에 넣는다. 앞 순서가 이미 맞춰져 있으므로
///    k 번째 항목을 k 번째에 넣으면 앞쪽은 밀리지 않고 결국 before 순서와 같아진다.
///    삭제됐던 항목은 같은 자리에 add_item 으로 다시 만든다(새 id).
/// 3. 기간이 늘어난 경우엔 항목 이동 전에, 줄어든 경우엔 이동 후에 계획 정보를 되돌린다.
List<Map<String, dynamic>> buildRestoreOps(PlanDetail before, PlanDetail current) {
  final ops = <Map<String, dynamic>>[];
  final beforeIds = {for (final i in before.allItems) i.id};
  final currentIds = {for (final i in current.allItems) i.id};

  for (final i in current.allItems) {
    if (!beforeIds.contains(i.id)) ops.add({'op': 'remove_item', 'item_id': i.id});
  }
  final beforeRules = {for (final c in before.constraints) c.id};
  final currentRules = {for (final c in current.constraints) c.id};
  for (final c in current.constraints) {
    if (!beforeRules.contains(c.id)) ops.add({'op': 'remove_constraint', 'constraint_id': c.id});
  }

  final planOp = <String, dynamic>{'op': 'update_plan'};
  void diff(String key, Object a, Object b) {
    if (a != b) planOp[key] = a;
  }

  diff('title', before.title, current.title);
  diff('start_date', isoDate(before.startDate), isoDate(current.startDate));
  diff('end_date', isoDate(before.endDate), isoDate(current.endDate));
  diff('day_start', before.dayStart, current.dayStart);
  diff('day_end', before.dayEnd, current.dayEnd);
  diff('travel_mode', before.travelMode, current.travelMode);
  diff('memo', before.memo, current.memo);
  final hasPlanOp = planOp.length > 1;
  final shrinking = before.numDays < current.numDays;
  if (hasPlanOp && !shrinking) ops.add(planOp);

  final items = before.allItems.toList()
    ..sort((a, b) => a.day != b.day ? a.day.compareTo(b.day) : a.order.compareTo(b.order));
  for (final i in items) {
    final fields = {
      'day': i.day,
      'order': i.order,
      'stay_minutes': i.stayMinutes,
      'start_time': i.startTime,
      'memo': i.memo,
    };
    if (currentIds.contains(i.id)) {
      ops.add({'op': 'update_item', 'item_id': i.id, ...fields, 'status': i.status});
    } else {
      fields.removeWhere((k, v) => v == null);
      ops.add({'op': 'add_item', 'place_id': i.placeId, ...fields});
      // add_item 은 status 를 받지 않는다. 새로 만든 항목은 음수 임시 id 로 이어서 바꾼다.
      if (i.status != 'planned') {
        final temp = -ops.where((o) => o['op'] == 'add_item').length;
        ops.add({'op': 'update_item', 'item_id': temp, 'status': i.status});
      }
    }
  }
  if (hasPlanOp && shrinking) ops.add(planOp);

  for (final c in before.constraints) {
    if (!currentRules.contains(c.id)) {
      ops.add({'op': 'add_constraint', 'type': c.type, 'params': c.params, 'hard': c.hard, 'source': c.source, 'note': c.note});
    }
  }
  return ops;
}
