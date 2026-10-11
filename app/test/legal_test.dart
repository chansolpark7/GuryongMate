// 이용약관 · 개인정보처리방침: 원본 docs/legal/*.md 와 앱 assets/legal/*.md 가 같은지, 로그인(01)·설정(33)에서 문서 화면으로 (2026-10-11).
import 'dart:io';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/token_store.dart';
import 'package:guryong_mate/features/legal/legal_doc_screen.dart';
import 'package:guryong_mate/features/settings/settings_screen.dart';
import 'package:guryong_mate/main.dart';
import 'package:provider/provider.dart';

import 'account_screens_test.dart' show signedOutSession;
import 'support/fixture_gateway.dart';
import 'support/harness.dart';

String _read(String path) => File(path).readAsStringSync().replaceAll('\r\n', '\n');

/// 로그인 문구 안의 단어(밑줄 링크)를 누른다.
Future<void> _tapWord(WidgetTester tester, String word) async {
  await tester.tapOnText(find.textRange.ofSubstring(word).first);
  await settle(tester, 15);
  await loaded(tester);
}

/// rootBundle 은 실제 파일 읽기라 가짜 시계로는 끝나지 않는다 → 미리 읽어 캐시에 둔다 (화면은 같은 캐시를 쓴다).
Future<void> warmAssets(WidgetTester tester) => tester.runAsync(() async {
      for (final d in LegalDoc.values) {
        await rootBundle.loadString(d.asset);
      }
    });

/// 문서 화면이 asset 을 다 읽을 때까지.
Future<void> loaded(WidgetTester tester) async {
  await until(tester, () => find.byKey(const ValueKey('legal-body')).evaluate().isNotEmpty, tries: 400);
  await settle(tester, 4);
}

void main() {
  setUpHarness();

  for (final name in ['terms', 'privacy']) {
    test('assets/legal/$name.md 는 docs/legal/$name.md 와 같다 (문서가 원본 — 바꾸면 cp ../docs/legal/*.md assets/legal/)', () {
      expect(_read('assets/legal/$name.md'), _read('../docs/legal/$name.md'));
    });
  }

  testWidgets('01 로그인 문구의 [이용약관]·[개인정보처리방침] 밑줄 링크 → 문서 화면 · 문서 사이 링크는 앱 안 화면', (tester) async {
    await phone(tester);
    await warmAssets(tester);
    final gw = FixtureGateway([]);
    final s = await signedOutSession(gw, MemoryTokenStore());
    await tester.pumpWidget(ChangeNotifierProvider.value(value: s, child: const GuryongApp()));
    await settle(tester);
    expect(find.text('계속하면 이용약관 및 개인정보처리방침에 동의하게 됩니다', findRichText: true), findsOneWidget, reason: '문구는 그대로');

    await _tapWord(tester, '이용약관');
    expect(find.byType(LegalDocScreen), findsOneWidget);
    expect(find.descendant(of: find.byType(AppBar), matching: find.text('이용약관')), findsOneWidget);
    expect(find.text('구룡메이트 이용약관'), findsOneWidget, reason: '# 제목도 보인다 (문서 모드)');
    expect(find.text('1. 서비스'), findsOneWidget, reason: '## 제목');

    // 약관 7조 "[개인정보처리방침](privacy.md)" → 앱 안 방침 화면
    final link = find.textContaining('개인정보 처리는', findRichText: true);
    await tester.ensureVisible(link);
    await settle(tester, 4);
    expect(find.textContaining('[개인정보처리방침]', findRichText: true), findsNothing, reason: '링크 기호는 숨김');
    // 링크 span 의 recognizer 를 부른다 (같은 낱말이 문서 아래에도 있어 글자 범위로 누르기 어렵다)
    TapGestureRecognizer? tap;
    tester.widget<RichText>(link).text.visitChildren((sp) {
      if (sp is TextSpan && sp.text == '개인정보처리방침' && sp.recognizer is TapGestureRecognizer) tap = sp.recognizer! as TapGestureRecognizer;
      return true;
    });
    expect(tap, isNotNull, reason: '밑줄 링크');
    tap!.onTap!();
    await settle(tester, 15);
    await loaded(tester);
    expect(find.descendant(of: find.byType(AppBar), matching: find.text('개인정보처리방침')), findsOneWidget);
    expect(find.text('구룡메이트 개인정보처리방침'), findsOneWidget);
    // 표는 행마다 카드: 첫 칸 굵게 + "머리 · 값"
    expect(find.text('수집 항목 · 아이디, 비밀번호(암호화해 저장), 표시 이름, 가입·최근 로그인 시각', findRichText: true), findsOneWidget);
    expect(find.textContaining('|---', findRichText: true), findsNothing);

    await tester.tap(find.byType(BackButton));
    await settle(tester, 15);
    await tester.tap(find.byType(BackButton));
    await settle(tester, 15);
    await _tapWord(tester, '개인정보처리방침');
    expect(find.descendant(of: find.byType(AppBar), matching: find.text('개인정보처리방침')), findsOneWidget);
  });

  testWidgets('33 설정 [앱 정보] 이용약관 · 개인정보처리방침 → 각 문서', (tester) async {
    await phone(tester);
    await warmAssets(tester);
    final gw = FixtureGateway([]);
    gw.on('GET /docent/memory', (_) => Res.json(200, {'items': [], 'memory_version': 1, 'style_length': null}));
    gw.on('GET /health', (_) => Res.json(200, {'status': 'ok'}));
    gw.on('GET /agent/info', (_) => Res.json(200, {'provider': 'scripted:scripts'}));
    gw.on('GET /safety/sim', (_) => Res.error(404, 'sim_disabled'));
    final s = await signedInSession(gw);
    await tester.pumpWidget(app(s, const SettingsScreen()));
    await settle(tester);

    for (final (key, title, heading) in [('settings-terms', '이용약관', '구룡메이트 이용약관'), ('settings-privacy', '개인정보처리방침', '구룡메이트 개인정보처리방침')]) {
      final row = find.byKey(ValueKey(key));
      await tester.scrollUntilVisible(row, 200, scrollable: find.byType(Scrollable).first);
      expect(find.text('앱 정보'), findsOneWidget);
      await tester.tap(row);
      await settle(tester, 15);
      await loaded(tester);
      expect(find.descendant(of: find.byType(AppBar), matching: find.text(title)), findsOneWidget);
      expect(find.text(heading), findsOneWidget);
      await tester.tap(find.byType(BackButton));
      await settle(tester, 15);
    }
  });
}
