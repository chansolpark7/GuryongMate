// 대화 답변·가이드 해설의 가벼운 마크다운 (lib/core/widgets/rich_answer.dart, 2026-10-11).
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guryong_mate/core/theme/app_theme.dart';
import 'package:guryong_mate/core/widgets/rich_answer.dart';

import 'support/harness.dart';

Future<void> _pump(WidgetTester tester, String text) => tester.pumpWidget(MaterialApp(theme: buildTheme(), home: Scaffold(body: RichAnswer(text))));

/// 굵게 칠한 조각들.
List<String> _bold(WidgetTester tester) {
  final out = <String>[];
  for (final t in tester.widgetList<Text>(find.byType(Text))) {
    t.textSpan?.visitChildren((s) {
      if (s is TextSpan && s.text != null && s.style?.fontWeight == FontWeight.w700) out.add(s.text!);
      return true;
    });
  }
  return out;
}

void main() {
  setUpHarness();

  test('문단 · 목록 · 제목/인용 기호 · 평문 줄바꿈', () {
    expect(parseRichBlocks('첫 문단이에요.\n\n둘째 문단.').map((b) => '$b'), ['첫 문단이에요.', '둘째 문단.']);
    expect(parseRichBlocks('예전 노트 한 줄\n다음 줄').length, 2, reason: '줄바꿈도 문단으로');
    expect(parseRichBlocks('- 계단 길\n* 쉼터\n1. 먼저 공원\n2) 다음 항구').map((b) => '$b'), ['• 계단 길', '• 쉼터', '1. 먼저 공원', '2. 다음 항구']);
    expect(parseRichBlocks('## 가옥거리\n> 전해지는 이야기').map((b) => '$b'), ['가옥거리', '전해지는 이야기']);
    expect(parseRichBlocks('| a | b |\n<b>x</b>').map((b) => '$b'), ['| a | b |', '<b>x</b>'], reason: '표·HTML 은 글자 그대로');
    expect(parseRichBlocks('-5도 정도예요').single.marker, isNull, reason: '"- " 가 아니면 목록 아님');
    expect(plainSummary('**구룡포항**에 늦게 도착해요.\n- 점심을 줄일까요?'), '구룡포항에 늦게 도착해요. 점심을 줄일까요?');
  });

  testWidgets('**굵게** 는 굵은 조각, 덜 닫힌 ** 는 기호만 숨긴다 (스트리밍 중)', (tester) async {
    await _pump(tester, '여기는 **1883년** 이후 생긴 거리예요.');
    expect(find.text('여기는 1883년 이후 생긴 거리예요.'), findsOneWidget);
    expect(_bold(tester), ['1883년']);

    await _pump(tester, '여기는 **1883년 이후');
    expect(find.text('여기는 1883년 이후'), findsOneWidget, reason: '** 가 보이지 않는다');
    expect(_bold(tester), isEmpty);
    expect(find.textContaining('**'), findsNothing);
  });

  testWidgets('목록은 기호 칸 + 내용, 문단은 따로 · 마크다운 없는 평문은 그대로', (tester) async {
    await _pump(tester, '이렇게 걸어 보세요.\n\n- 계단 대신 **오른쪽 길**\n- 쉼터에서 쉬기\n\n1. 공원\n2. 항구');
    expect(find.text('이렇게 걸어 보세요.'), findsOneWidget);
    expect(find.text('•'), findsNWidgets(2));
    expect(find.text('계단 대신 오른쪽 길'), findsOneWidget);
    expect(_bold(tester), contains('오른쪽 길'));
    expect(find.text('1.'), findsOneWidget);
    expect(find.text('항구'), findsOneWidget);

    await _pump(tester, '일제강점기 구룡포에 정착한 일본인들의 거리예요.');
    expect(find.text('일제강점기 구룡포에 정착한 일본인들의 거리예요.'), findsOneWidget);
    expect(find.byType(Column), findsNothing, reason: '한 문단이면 Text 하나 (예전과 같음)');
  });
}
