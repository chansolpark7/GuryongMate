import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:guryong_mate/core/app_session.dart';
import 'package:guryong_mate/main.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'fixtures.dart';

/// 계획 목록만 다루는 가짜 서버: 목록 · 상세 · 생성 · ops · 삭제.
class ListGateway {
  final plans = <int, Map<String, dynamic>>{1: planJson([itemJson(1, 1, '구룡포 시장', '10:00', '11:00')], title: '엄마랑 구룡포')};
  var nextId = 2;

  Future<http.StreamedResponse> handle(http.BaseRequest req, http.ByteStream body) async {
    final text = await body.bytesToString();
    final b = text.isEmpty ? null : jsonDecode(text);
    final seg = req.url.pathSegments;
    http.StreamedResponse json(Object? data, [int status = 200]) =>
        http.StreamedResponse(Stream.value(utf8.encode(jsonEncode(data))), status);
    if (req.url.path == '/planner/plans' && req.method == 'GET') {
      return json(plans.values.map((p) => Map.of(p)..remove('days')).toList());
    }
    if (req.url.path == '/planner/plans' && req.method == 'POST') {
      final id = nextId++;
      plans[id] = planJson([], id: id, title: b['title'] as String);
      return json(plans[id], 201);
    }
    if (seg.length >= 3 && seg[0] == 'planner' && seg[1] == 'plans') {
      final id = int.parse(seg[2]);
      if (seg.length == 4 && seg[3] == 'proposals') return json(<Object>[]);
      if (seg.length == 4 && seg[3] == 'ops') return json(plans[id]);
      if (req.method == 'DELETE') {
        plans.remove(id);
        return http.StreamedResponse(const Stream.empty(), 204);
      }
      return json(plans[id]);
    }
    return json({'detail': 'nf'}, 404);
  }
}

void main() {
  setUpAll(() => GoogleFonts.config.allowRuntimeFetching = false);

  testWidgets('길게 눌러 복제 → 복사본 삭제 → 원본 카드가 계속 눌린다', (tester) async {
    await tester.binding.setSurfaceSize(const Size(390, 844));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    SharedPreferences.setMockInitialValues({'user_id': 'seoyun', 'base_url': 'http://gateway.test'});
    final fake = ListGateway();
    final session = await AppSession.load(clientFactory: () => MockClient.streaming(fake.handle));
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
}
