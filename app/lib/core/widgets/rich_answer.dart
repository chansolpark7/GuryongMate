import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// 대화 답변·가이드 해설의 가벼운 마크다운 (2026-10-11).
/// 지원: 문단(빈 줄·줄바꿈), **굵게**, `- ` / `1. ` 목록, `[글](대상)` 링크(글만, [onLink] 가 있으면 누를 수 있게).
/// 제목(#)·인용(>)은 기호만 떼고 글자로, 표·이미지·HTML 은 글자 그대로. 스트리밍 중 덜 닫힌 `**` 는 기호만 숨긴다.
/// 마크다운이 없는 예전 줄글은 줄마다 문단으로 보인다.
///
/// [document] (약관·방침 화면): 제목 #·##·### 은 제목 글꼴로, 표는 행마다 작은 카드("머리: 값"), 인용은 회색 상자, `---` 는 구분선.
///
/// 패키지(flutter_markdown 은 유지보수 중단, 대체 패키지는 이 범위에 비해 크다) 대신 작은 파서로 둔다.
class RichAnswer extends StatefulWidget {
  const RichAnswer(this.text, {super.key, this.style, this.paragraphGap = 10, this.document = false, this.onLink});
  final String text;
  final TextStyle? style;
  final double paragraphGap;
  final bool document;

  /// 링크를 눌렀을 때 (대상 그대로, 예: "privacy.md"). null 이면 링크는 글만 보인다.
  final ValueChanged<String>? onLink;

  @override
  State<RichAnswer> createState() => _RichAnswerState();
}

class _RichAnswerState extends State<RichAnswer> {
  final _recognizers = <TapGestureRecognizer>[];

  void _clear() {
    for (final r in _recognizers) {
      r.dispose();
    }
    _recognizers.clear();
  }

  @override
  void dispose() {
    _clear();
    super.dispose();
  }

  GestureRecognizer? _tap(String target) {
    final on = widget.onLink;
    if (on == null) return null;
    final r = TapGestureRecognizer()..onTap = () => on(target);
    _recognizers.add(r);
    return r;
  }

  Widget _rich(String text, TextStyle style) => Text.rich(TextSpan(children: inlineSpans(text, style, onLink: _tap)), style: style);

  @override
  Widget build(BuildContext context) {
    _clear();
    final base = widget.style ?? AppText.body;
    final blocks = parseRichBlocks(widget.text, document: widget.document);
    if (blocks.isEmpty) return const SizedBox.shrink();
    final children = <Widget>[];
    for (final (i, b) in blocks.indexed) {
      if (i > 0) {
        final prev = blocks[i - 1];
        final tight = (b.isListItem && prev.isListItem) || (b.kind == RichKind.table && prev.kind == RichKind.table);
        children.add(SizedBox(height: tight ? 4 : (b.kind == RichKind.heading ? widget.paragraphGap + 8 : widget.paragraphGap)));
      }
      children.add(switch (b.kind) {
        RichKind.heading => _rich(
            b.text,
            switch (b.level) {
              1 => AppText.headline.copyWith(fontSize: 20),
              2 => AppText.section,
              _ => AppText.label.copyWith(fontWeight: FontWeight.w700),
            }),
        RichKind.rule => const Divider(height: 1, color: AppColors.line),
        RichKind.quote => Container(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
            decoration: BoxDecoration(color: AppColors.field, borderRadius: BorderRadius.circular(10)),
            child: _rich(b.text, AppText.meta.copyWith(color: AppColors.text2)),
          ),
        RichKind.table => _TableRow(cells: b.cells, header: b.header, cell: _rich),
        RichKind.item => Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SizedBox(
              width: b.marker == '•' ? 16 : 22,
              child: Text(b.marker!, style: base.copyWith(color: b.marker == '•' ? AppColors.text3 : base.color, fontWeight: FontWeight.w700)),
            ),
            Expanded(child: _rich(b.text, base)),
          ]),
        RichKind.paragraph => _rich(b.text, base),
      });
    }
    if (children.length == 1) return children.first;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: children);
  }
}

/// 표 한 행 = 작은 카드: 첫 칸 굵게, 나머지는 "머리: 값" 줄 (휴대폰 폭에서 읽기 쉽게).
class _TableRow extends StatelessWidget {
  const _TableRow({required this.cells, required this.header, required this.cell});
  final List<String> cells;
  final List<String> header;
  final Widget Function(String text, TextStyle style) cell;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: AppColors.background, borderRadius: BorderRadius.circular(12)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (cells.isNotEmpty) cell(cells.first, AppText.label.copyWith(fontWeight: FontWeight.w700)),
          for (var i = 1; i < cells.length; i++)
            if (cells[i].isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: cell(i < header.length && header[i].isNotEmpty ? '**${header[i]}** · ${cells[i]}' : cells[i], AppText.meta.copyWith(color: AppColors.text)),
              ),
        ]),
      );
}

enum RichKind { paragraph, item, heading, quote, table, rule }

/// 문단 하나 · 목록 항목 하나 · (문서 모드) 제목 · 인용 · 표 한 행 · 구분선.
class RichBlock {
  const RichBlock(this.text, [this.marker]) : kind = marker == null ? RichKind.paragraph : RichKind.item, level = 0, cells = const [], header = const [];
  const RichBlock._(this.kind, this.text, {this.level = 0, this.cells = const [], this.header = const []}) : marker = null;
  final RichKind kind;
  final String text;

  /// 목록 기호 '•' 또는 '1.'.
  final String? marker;
  final int level;
  final List<String> cells;
  final List<String> header;

  bool get isListItem => kind == RichKind.item;

  @override
  String toString() => marker == null ? text : '$marker $text';
}

final _bullet = RegExp(r'^\s*[-*•]\s+(.*)$');
final _number = RegExp(r'^\s*(\d{1,3})[.)]\s+(.*)$');
final _heading = RegExp(r'^\s*(#{1,6})\s+');
final _quote = RegExp(r'^\s*>\s?');
final _tableSep = RegExp(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$');
final _rule = RegExp(r'^\s*(-{3,}|\*{3,}|_{3,})\s*$');

List<String> _cells(String line) {
  var s = line.trim();
  if (s.startsWith('|')) s = s.substring(1);
  if (s.endsWith('|')) s = s.substring(0, s.length - 1);
  return [for (final c in s.split('|')) c.trim()];
}

List<RichBlock> parseRichBlocks(String text, {bool document = false}) {
  final out = <RichBlock>[];
  final lines = text.replaceAll('\r\n', '\n').split('\n');
  List<String>? header;
  for (var i = 0; i < lines.length; i++) {
    final line = lines[i].trimRight();
    if (line.trim().isEmpty) {
      header = null;
      continue;
    }
    if (document) {
      final isRow = line.trimLeft().startsWith('|');
      if (isRow && i + 1 < lines.length && _tableSep.hasMatch(lines[i + 1]) && header == null) {
        header = _cells(line); // 표 머리: 행 카드의 "머리: 값" 에 쓴다
        i++;
        continue;
      }
      if (isRow && header != null) {
        out.add(RichBlock._(RichKind.table, line, cells: _cells(line), header: header));
        continue;
      }
      header = null;
      if (_rule.hasMatch(line)) {
        out.add(const RichBlock._(RichKind.rule, ''));
        continue;
      }
      final h = _heading.firstMatch(line);
      if (h != null) {
        out.add(RichBlock._(RichKind.heading, line.substring(h.end).trim(), level: h.group(1)!.length));
        continue;
      }
      if (_quote.hasMatch(line)) {
        out.add(RichBlock._(RichKind.quote, line.replaceFirst(_quote, '').trim()));
        continue;
      }
    }
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

final _link = RegExp(r'\[([^\]]+)\]\(([^)\s]+)\)');

/// `**굵게**` → 굵은 span, `[글](대상)` → 글 (onLink 가 recognizer 를 주면 밑줄 링크).
/// 짝이 없는 마지막 `**`(스트리밍 중)는 기호만 뺀다.
List<InlineSpan> inlineSpans(String text, TextStyle base, {GestureRecognizer? Function(String target)? onLink}) {
  final parts = text.split('**');
  final spans = <InlineSpan>[];
  final closed = parts.length.isOdd; // 짝수 개 조각이면 마지막 ** 가 안 닫힘
  for (final (i, p) in parts.indexed) {
    if (p.isEmpty) continue;
    final bold = i.isOdd && (closed || i < parts.length - 1);
    final style = bold ? base.copyWith(fontWeight: FontWeight.w700) : null;
    var at = 0;
    for (final m in _link.allMatches(p)) {
      if (m.start > at) spans.add(TextSpan(text: p.substring(at, m.start), style: style));
      final r = onLink?.call(m.group(2)!);
      spans.add(TextSpan(
        text: m.group(1),
        style: r == null ? style : (style ?? base).copyWith(color: AppColors.primary, decoration: TextDecoration.underline, decorationColor: AppColors.primary),
        recognizer: r,
      ));
      at = m.end;
    }
    if (at < p.length) spans.add(TextSpan(text: p.substring(at), style: style));
  }
  return spans;
}

/// 한 줄 요약용: 기호(** · 목록 · # · 링크)만 떼고 줄을 이어 붙인다 (도착·미리보기 카드).
String plainSummary(String text) =>
    parseRichBlocks(text).map((b) => b.text.replaceAll('**', '').replaceAllMapped(_link, (m) => m.group(1)!)).join(' ');
