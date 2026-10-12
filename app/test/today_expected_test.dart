// Today 남은 일정: 늦으면 '기준 → 예상' (docs/trip/audit-2026-10-12.md D2, TripState.expected).
// 제시간·이르면, 또는 서버가 expected 를 주지 않으면(재계산 전·옛 서버) 지금처럼 기준 시각만.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/format.dart';
import 'package:guryong_mate/core/models/trip.dart';
import 'package:guryong_mate/features/planner/planner_shell.dart';

import 'support/harness.dart';
import 'support/trip_replay.dart';

/// 장면 ② 12:35 (첫 장소에 머무는 중). 남은 일정 = 머무는 곳·다음 뒤의 항목들.
TripReplay scene() => TripReplay('scene2_arrival', flows: ['f4_arrival_docent.json'])..advanceTo('12:35:00');

List<Map<String, dynamic>> itemsOf(TripReplay r) => ((r.plan['days'] as List).first['items'] as List).cast<Map<String, dynamic>>();

String shift(String hhmm, int min) => fromMinutes(toMinutes(hhmm)! + min);

/// 모든 항목을 [min]분 밀린 expected (그날 종료도).
Map<String, dynamic> expectedBy(TripReplay r, int min) => {
      'end': shift((r.plan['days'] as List).first['end_at'] as String, min),
      'items': {
        for (final i in itemsOf(r)) '${i['id']}': {'start': shift(i['start_at'] as String, min), 'end': shift(i['end_at'] as String, min)},
      },
    };

Future<void> pumpToday(WidgetTester tester, TripReplay r) async {
  await phone(tester); // 390 × 844: 늦은 행도 줄바꿈·넘침 없이
  final s = await signedInSession(r.gw, prefs: {'demo_location_mode': true});
  await tester.pumpWidget(app(s, PlannerShell(planId: r.planId, locationSource: FakeLocationSource())));
  await settle(tester, 30);
}

/// 남은 일정 행 (지금·다음 뒤). 목록에 있는 항목만.
List<Map<String, dynamic>> remainingRows(TripReplay r) =>
    [for (final i in itemsOf(r)) if (find.byKey(ValueKey('remaining-${i['id']}')).evaluate().isNotEmpty) i];

void main() {
  setUpHarness();

  test('TripState.expected 파싱: 없거나 null 이면 null', () {
    expect(TripState.fromJson({'active': true}).expected, isNull);
    expect(TripState.fromJson({'active': true, 'expected': null}).expected, isNull);
    final e = TripState.fromJson({
      'active': true,
      'expected': {
        'end': '18:18',
        'items': {'13': {'start': '15:27', 'end': '16:27'}},
      },
    }).expected!;
    expect((e.end, e.items[13]?.start, e.items[13]?.end), ('18:18', '15:27', '16:27'));
  });

  testWidgets('늦으면 남은 일정 행과 머리에 기준 → 예상', (tester) async {
    final r = scene();
    r.state = {...r.state, 'delay_minutes': 40, 'expected': expectedBy(r, 40)};
    await pumpToday(tester, r);
    final rows = remainingRows(r);
    expect(rows, isNotEmpty);
    final end = (r.plan['days'] as List).first['end_at'] as String;
    expect(find.textContaining('곳 · $end → ${shift(end, 40)} 종료 예상'), findsOneWidget);
    for (final i in rows) {
      final time = find.byKey(ValueKey('remaining-time-${i['id']}'));
      expect(time, findsOneWidget);
      final text = (tester.widget<Text>(time).textSpan!).toPlainText();
      expect(text, '${i['start_at']} → ${shift(i['start_at'] as String, 40)}');
      // 한 줄 (390px 에서 줄바꿈 없음)
      expect(tester.getSize(time).height, lessThan(24));
    }
    expect(tester.takeException(), isNull);
  });

  testWidgets('제시간·조금(5분 미만) 늦거나 이르면 지금처럼 기준 시각만', (tester) async {
    for (final min in [0, 4, -10]) {
      final r = scene();
      r.state = {...r.state, 'expected': expectedBy(r, min)};
      await pumpToday(tester, r);
      final end = (r.plan['days'] as List).first['end_at'] as String;
      expect(find.textContaining('곳 · $end 종료 예정'), findsOneWidget, reason: '$min분');
      expect(find.textContaining('종료 예상'), findsNothing);
      for (final i in remainingRows(r)) {
        expect(find.byKey(ValueKey('remaining-time-${i['id']}')), findsNothing);
        expect(find.descendant(of: find.byKey(ValueKey('remaining-${i['id']}')), matching: find.text(i['start_at'] as String)), findsOneWidget);
      }
      await tester.pumpWidget(const SizedBox());
    }
  });

  testWidgets('expected 가 없으면(재계산 전·옛 서버) 지금처럼', (tester) async {
    final r = scene();
    r.state = {...r.state, 'expected': null};
    await pumpToday(tester, r);
    final end = (r.plan['days'] as List).first['end_at'] as String;
    expect(find.textContaining('곳 · $end 종료 예정'), findsOneWidget);
    expect(find.textContaining('→'), findsNothing);
  });
}
