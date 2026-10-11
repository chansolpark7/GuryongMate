import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../../core/widgets/rich_answer.dart';

/// 이용약관 · 개인정보처리방침 (원본 docs/legal/*.md → assets/legal/, 오프라인에서도 보인다).
enum LegalDoc {
  terms('이용약관', 'assets/legal/terms.md'),
  privacy('개인정보처리방침', 'assets/legal/privacy.md');

  const LegalDoc(this.title, this.asset);
  final String title;
  final String asset;

  /// 문서 사이 링크 ("privacy.md", "./terms.md") → 앱 안 문서.
  static LegalDoc? fromLink(String target) {
    final name = target.split('/').last.split('#').first;
    return switch (name) { 'terms.md' => terms, 'privacy.md' => privacy, _ => null };
  }
}

Future<void> openLegalDoc(BuildContext context, LegalDoc doc) =>
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => LegalDocScreen(doc: doc)));

class LegalDocScreen extends StatefulWidget {
  const LegalDocScreen({super.key, required this.doc});
  final LegalDoc doc;

  @override
  State<LegalDocScreen> createState() => _LegalDocScreenState();
}

class _LegalDocScreenState extends State<LegalDocScreen> {
  // 한 번만 읽는다 (build 마다 새 Future 면 다시 그릴 때마다 처음부터 기다린다)
  late final Future<String> _text = rootBundle.loadString(widget.doc.asset);

  LegalDoc get doc => widget.doc;

  void _link(String target) {
    final d = LegalDoc.fromLink(target);
    if (d == null) {
      showInfo(context, target); // 외부 주소는 글로만 (브라우저 열기 없음)
    } else if (d != doc) {
      openLegalDoc(context, d);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        backgroundColor: AppColors.surface,
        appBar: AppBar(backgroundColor: AppColors.surface, centerTitle: true, title: Text(doc.title, style: AppText.headline)),
        body: FutureBuilder<String>(
          future: _text,
          builder: (context, snap) {
            if (snap.hasError) return StateView(icon: Icons.description_outlined, title: '문서를 열지 못했어요', message: '${snap.error}');
            if (!snap.hasData) return const Center(child: CircularProgressIndicator());
            return ListView(padding: const EdgeInsets.fromLTRB(20, 8, 20, 32), children: [
              RichAnswer(snap.data!, key: const ValueKey('legal-body'), document: true, style: AppText.body.copyWith(height: 1.6), onLink: _link),
            ]);
          },
        ),
      );
}
