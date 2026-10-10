
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/main.dart';
import 'package:provider/provider.dart';

import 'fixtures.dart';
import 'support/fixture_gateway.dart';
import 'support/harness.dart';

/// 계획 목록만 다루는 가짜 서버: 목록 · 상세 · 생성 · ops · 삭제.
class ListGateway {
  ListGateway() {
    gw.on('GET /planner/plans', (_) => Res.json(200, plans.values.map((p) => Map.of(p)..remove('days')).toList()));
    gw.on('POST /planner/plans', (r) {
      final id = nextId++;
      plans[id] = planJson([], id: id, title: (r.body as Map)['title'] as String);
      return Res.json(201, plans[id]);
    });
    gw.on('GET /planner/plans/{id}/proposals', (_) => Res.json(200, []));
    gw.on('POST /planner/plans/{id}/ops', (r) => Res.json(200, plans[int.parse(r.path.split('/')[3])]));
    gw.on('DELETE /planner/plans/{id}', (r) {
      plans.remove(int.parse(r.path.split('/')[3]));
      return Res.json(204, null);
    });
    gw.on('GET /planner/plans/{id}', (r) => Res.json(200, plans[int.parse(r.path.split('/')[3])]));
    gw.on('GET /planner/plans/{id}/safety', (r) => Res.error(503, 'safety_unavailable'));
    gw.on('GET /planner/plans/{id}/safety/alerts', (_) => Res.json(200, []));
    gw.on('GET /trip/plans/{id}/state', (_) => Res.json(200, {'active': false}));
    gw.on('GET /agent/sessions', (_) => Res.json(200, []));
    gw.on('POST /agent/sessions', (_) => Res.json(201, {'id': 's1', 'plan_id': 1, 'created_at': '2026-10-04T10:00:00Z', 'updated_at': '2026-10-04T10:00:00Z'}));
  }
  final gw = FixtureGateway([]);
  final plans = <int, Map<String, dynamic>>{1: planJson([itemJson(1, 1, '구룡포 시장', '10:00', '11:00')], title: '엄마랑 구룡포', start: '2026-12-20', end: '2026-12-21')};
  var nextId = 2;
}

void main() {
  setUpHarness();

  testWidgets('길게 눌러 복제 → 복사본 삭제 → 원본 카드가 계속 눌린다', (tester) async {
    await tester.binding.setSurfaceSize(const Size(390, 844));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final fake = ListGateway();
    final session = await signedInSession(fake.gw);
    await tester.pumpWidget(ChangeNotifierProvider.value(value: session, child: const GuryongApp()));
    await tester.pumpAndSettle();

    await tester.longPress(find.text('엄마랑 구룡포'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('복제'));
    await tester.pumpAndSettle();
    expect(find.text('엄마랑 구룡포 (복사본)'), findsOneWidget);

    await tester.longPress(find.text('엄마랑 구룡포 (복사본)'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('삭제'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('삭제').last);
    await tester.pumpAndSettle();
    expect(find.text('엄마랑 구룡포 (복사본)'), findsNothing);

    // 원본: 길게 누르면 메뉴, 탭하면 Planner
    await tester.longPress(find.text('엄마랑 구룡포'));
    await tester.pumpAndSettle();
    expect(find.text('복제'), findsOneWidget, reason: '삭제 후 원본 카드 길게 누르기');
    await tester.tapAt(const Offset(195, 40));
    await tester.pumpAndSettle();
    expect(find.text('복제'), findsNothing, reason: '메뉴 시트 닫힘');
    await tester.tap(find.text('엄마랑 구룡포'));
    for (var i = 0; i < 10; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
    expect(find.text('Planner'), findsOneWidget, reason: '삭제 후 원본 카드 탭');
  });
  testWidgets('데모 가상 시각: 기기 시계가 아니라 서비스 기준 오늘로 나눈다 (가상 시각의 오늘 일정은 지난 여행이 아니다)', (tester) async {
    await tester.binding.setSurfaceSize(const Size(390, 844));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final fake = ListGateway();
    // 서비스 시각은 기기보다 40일 전. 그날 하루 일정은 기기 기준으론 지난 여행이지만 서비스 기준으론 오늘 여행
    final sim = DateTime.now().toUtc().add(const Duration(hours: 9)).subtract(const Duration(days: 40));
    String ymd(DateTime d) => '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
    fake.plans[1] = planJson([itemJson(1, 1, '구룡포 시장', '10:00', '11:00')], title: '엄마랑 구룡포', start: ymd(sim), end: ymd(sim));
    fake.gw.on('GET /safety/sim', (_) => Res.json(200, {'mode': 'sim', 'scenario': 'calm', 'now': '${ymd(sim)}T14:43:00+09:00', 'scenarios': {}}));
    final session = await signedInSession(fake.gw);
    await tester.pumpWidget(ChangeNotifierProvider.value(value: session, child: const GuryongApp()));
    await tester.pumpAndSettle();

    expect(find.text('엄마랑 구룡포'), findsOneWidget);
    expect(find.text('지난 여행'), findsNothing);
    expect(find.text('오늘 여행'), findsOneWidget);
    expect(session.serverToday(), DateTime(sim.year, sim.month, sim.day));
  });
}
