import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// 대화 답변·가이드 해설의 가벼운 마크다운 (2026-10-11).
/// 지원: 문단(빈 줄·줄바꿈), **굵게**, `- ` / `1. ` 목록. 제목(#)·인용(>)은 기호만 떼고 글자로, 표·이미지·HTML 은 글자 그대로.
/// 스트리밍 중 덜 닫힌 `**` 는 기호만 숨긴다. 마크다운이 없는 예전 줄글은 줄마다 문단으로 보인다.
///
/// 패키지(flutter_markdown 은 유지보수 중단, 대체 패키지는 이 범위에 비해 크다) 대신 작은 파서로 둔다.
class RichAnswer extends StatelessWidget {
  const RichAnswer(this.text, {super.key, this.style, this.paragraphGap = 10});
  final String text;
  final TextStyle? style;
  final double paragraphGap;

  @override
  Widget build(BuildContext context) {
    final base = style ?? AppText.body;
    final blocks = parseRichBlocks(text);
    if (blocks.isEmpty) return const SizedBox.shrink();
    final children = <Widget>[];
    for (final (i, b) in blocks.indexed) {
      if (i > 0) children.add(SizedBox(height: b.marker != null && blocks[i - 1].marker != null ? 4 : paragraphGap));
      final body = Text.rich(TextSpan(children: inlineSpans(b.text, base)), style: base);
      children.add(b.marker == null
          ? body
          : Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              SizedBox(
                width: b.marker == '•' ? 16 : 22,
                child: Text(b.marker!, style: base.copyWith(color: b.marker == '•' ? AppColors.text3 : base.color, fontWeight: FontWeight.w700)),
              ),
              Expanded(child: body),
            ]));
    }
    if (children.length == 1) return children.first;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: children);
  }
}

/// 문단 하나 또는 목록 항목 하나. [marker] 는 '•' 또는 '1.' (문단이면 null).
class RichBlock {
  const RichBlock(this.text, [this.marker]);
  final String text;
  final String? marker;

  @override
  String toString() => marker == null ? text : '$marker $text';
}

final _bullet = RegExp(r'^\s*[-*•]\s+(.*)$');
final _number = RegExp(r'^\s*(\d{1,3})[.)]\s+(.*)$');
final _heading = RegExp(r'^\s*#{1,6}\s+');
final _quote = RegExp(r'^\s*>\s?');

List<RichBlock> parseRichBlocks(String text) {
  final out = <RichBlock>[];
  for (final raw in text.replaceAll('\r\n', '\n').split('\n')) {
    final line = raw.trimRight();
    if (line.trim().isEmpty) continue;
    final b = _bullet.firstMatch(line);
    if (b != null && b.group(1)!.trim().isNotEmpty) {
      out.add(RichBlock(b.group(1)!.trim(), '•'));
      continue;
    }
    final n = _number.firstMatch(line);
    if (n != null && n.group(2)!.trim().isNotEmpty) {
      out.add(RichBlock(n.group(2)!.trim(), '${n.group(1)}.'));
      continue;
    }
    out.add(RichBlock(line.replaceFirst(_heading, '').replaceFirst(_quote, '').trim()));
  }
  return out;
}

/// `**굵게**` → 굵은 span. 짝이 없는 마지막 `**`(스트리밍 중)는 기호만 뺀다.
List<InlineSpan> inlineSpans(String text, TextStyle base) {
  final parts = text.split('**');
  final spans = <InlineSpan>[];
  final closed = parts.length.isOdd; // 짝수 개 조각이면 마지막 ** 가 안 닫힘
  for (final (i, p) in parts.indexed) {
    if (p.isEmpty) continue;
    final bold = i.isOdd && (closed || i < parts.length - 1);
    spans.add(TextSpan(text: p, style: bold ? base.copyWith(fontWeight: FontWeight.w700) : null));
  }
  return spans;
}

/// 한 줄 요약용: 기호(** · 목록 · #)만 떼고 줄을 이어 붙인다 (도착·미리보기 카드).
String plainSummary(String text) => parseRichBlocks(text).map((b) => b.text.replaceAll('**', '')).join(' ');
