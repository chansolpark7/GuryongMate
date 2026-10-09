import 'package:flutter/material.dart';

import '../../core/api/api_client.dart';
import '../../core/models/trip.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import 'trip_controller.dart';

/// 17 Guide Note (Figma 73:2587): 여행 전에 준비한 맞춤 해설을 기다림 없이. 빈 배열 섹션은 숨긴다.
class GuideNoteScreen extends StatefulWidget {
  const GuideNoteScreen({super.key, required this.t, required this.placeId, this.initialLength});
  final TripController t;
  final String placeId;
  final String? initialLength; // short | long

  @override
  State<GuideNoteScreen> createState() => _GuideNoteScreenState();
}

class _GuideNoteScreenState extends State<GuideNoteScreen> {
  GuideNote? _note;
  Object? _error;
  bool _missing = false;
  bool _loading = true;
  late String _length;
  String? _companion;
  final _input = TextEditingController();

  TripController get t => widget.t;

  String get _title => t.plan.plan?.allItems.where((i) => i.placeId == widget.placeId).firstOrNull?.title ?? '';

  @override
  void initState() {
    super.initState();
    _length = widget.initialLength ?? t.preferredLength ?? 'short';
    t.pushed(true);
    _load();
  }

  @override
  void dispose() {
    t.pushed(false);
    _input.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final note = await t.api.guideNoteDetail(t.planId, widget.placeId);
      _note = note;
      _missing = note.status == 'failed';
      if (note.ready) t.heard[widget.placeId] = _length;
    } on ApiException catch (e) {
      if (e.status == 404) {
        _missing = true;
      } else {
        _error = e;
      }
    }
    try {
      // 동행자 팁 제목: 메모리 companion 에서 사람 이름만 ("어머니와 함께" → 어머니)
      final mem = await t.api.memory(planId: t.planId);
      final c = mem.items.where((m) => m.category == 'companion').firstOrNull?.text;
      final m = c == null ? null : RegExp(r'(어머니|아버지|엄마|아빠|부모님|아이|딸|아들|친구|남편|아내|할머니|할아버지)').firstMatch(c);
      _companion = m?.group(1);
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  void _setLength(String l) {
    setState(() => _length = l);
    t.preferredLength = l; // 다음 장소 기본값 (메모리 저장은 대화로 말했을 때만, docent §1.2)
    t.heard[widget.placeId] = l;
  }

  /// qa 칩 · 입력 → AI 시트(장소 컨텍스트)로 대화 (18). 오프라인이면 노트의 답을 펼친다.
  void _ask(String q, [String? answer]) {
    if (q.trim().isEmpty) return;
    if (t.offline && answer != null) {
      showModalBottomSheet<void>(
        context: context,
        showDragHandle: true,
        builder: (_) => SafeArea(child: Padding(padding: const EdgeInsets.fromLTRB(20, 0, 20, 20), child: Text(answer, style: AppText.body))),
      );
      return;
    }
    _input.clear();
    Navigator.of(context).pop();
    t.onOpenAi?.call(prefill: q, send: true, placeTitle: _title);
  }

  @override
  Widget build(BuildContext context) {
    final n = _note;
    return Scaffold(
      backgroundColor: AppColors.surface,
      appBar: AppBar(
        backgroundColor: AppColors.surface,
        centerTitle: true,
        title: Column(children: [
          Text('현장 가이드', style: AppText.headline),
          Text(_title, style: AppText.caption),
        ]),
        actions: [
          IconButton(tooltip: '음성으로 듣기', icon: const Icon(Icons.volume_up_outlined), onPressed: () => showInfo(context, '음성은 준비 중이에요')),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? StateView(icon: Icons.cloud_off_outlined, title: '해설을 불러오지 못했어요', message: userMessage(_error!), actionLabel: '다시 시도', onAction: _load)
              : _missing || n == null
                  ? _NoNote(onListen: () => _ask('$_title 짧게 설명해줘'))
                  : ListView(padding: const EdgeInsets.fromLTRB(20, 4, 20, 24), children: [
                      Wrap(spacing: 6, runSpacing: 6, children: [
                        if (t.plan.plan?.allItems.where((i) => i.placeId == widget.placeId).firstOrNull?.place?.contentType case final ct? when ct.isNotEmpty) InfoChip(label: ct),
                        InfoChip(
                          label: '내 관심사 반영${n.preparedAt == null ? '' : ' · ${n.preparedAt!.month}/${n.preparedAt!.day} 준비'}',
                          icon: Icons.auto_awesome,
                          fg: AppColors.ai,
                          bg: AppColors.aiSoft,
                        ),
                        if (n.status == 'stale') InfoChip.warning('관심사가 바뀌어 다시 준비할 수 있어요'),
                      ]),
                      const SizedBox(height: 14),
                      if (n.preparing)
                        _Preparing(onListen: () => _ask('$_title 짧게 설명해줘'))
                      else ...[
                        _Segment(value: _length, onChanged: _setLength),
                        const SizedBox(height: 16),
                        Text(_length == 'long' && n.long.isNotEmpty ? n.long : n.short, key: const ValueKey('note-body'), style: AppText.body.copyWith(fontSize: 16, height: 26 / 16)),
                      ],
                      if (n.forYou.isNotEmpty) ...[
                        const SizedBox(height: 20),
                        Container(
                          padding: const EdgeInsets.all(16),
                          decoration: BoxDecoration(color: AppColors.aiSoft, borderRadius: BorderRadius.circular(14)),
                          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Row(children: [
                              const Icon(Icons.auto_awesome, size: 16, color: AppColors.ai),
                              const SizedBox(width: 6),
                              Text('당신에게', style: AppText.label.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700)),
                            ]),
                            const SizedBox(height: 6),
                            for (final s in n.forYou) _Bullet(text: s, color: AppColors.ai),
                          ]),
                        ),
                      ],
                      _Section(icon: Icons.photo_camera_outlined, title: '사진 포인트', items: n.highlights),
                      _Section(icon: Icons.people_outline, title: _companion == null ? '동행자 팁' : '동행자 팁 · $_companion와 함께', items: n.companionTips),
                      _Section(icon: Icons.warning_amber_rounded, title: '알아두세요', items: n.cautions, color: AppColors.warning),
                      if (n.qa.isNotEmpty) ...[
                        const SizedBox(height: 20),
                        Text('이런 것도 물어보세요', style: AppText.label.copyWith(fontWeight: FontWeight.w700, color: AppColors.text)),
                        const SizedBox(height: 8),
                        Wrap(spacing: 8, runSpacing: 8, children: [
                          for (final (q, a) in n.qa)
                            ActionChip(
                              label: Text(q, style: AppText.meta.copyWith(color: AppColors.ai)),
                              side: const BorderSide(color: AppColors.aiLine),
                              backgroundColor: AppColors.surface,
                              shape: const StadiumBorder(),
                              onPressed: () => _ask(q, a),
                            ),
                        ]),
                      ],
                      if (n.sources.isNotEmpty) ...[
                        const SizedBox(height: 20),
                        Text('출처', style: AppText.caption.copyWith(fontWeight: FontWeight.w700)),
                        const SizedBox(height: 6),
                        Wrap(spacing: 6, runSpacing: 6, children: [for (final s in n.sources) _SourceChip(source: s)]),
                      ],
                    ]),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: EdgeInsets.fromLTRB(16, 8, 16, 12 + MediaQuery.viewInsetsOf(context).bottom),
          child: Container(
            height: 48,
            padding: const EdgeInsets.only(left: 16, right: 6),
            decoration: BoxDecoration(color: AppColors.field, borderRadius: BorderRadius.circular(24)),
            child: Row(children: [
              Expanded(
                child: TextField(
                  controller: _input,
                  textInputAction: TextInputAction.send,
                  onSubmitted: _ask,
                  decoration: const InputDecoration(hintText: '이 장소에 대해 물어보기…', border: InputBorder.none, filled: false, isCollapsed: true),
                ),
              ),
              IconButton(icon: const Icon(Icons.mic_none, size: 20, color: AppColors.text2), onPressed: () => showInfo(context, '음성 대화는 준비 중이에요')),
              IconButton(icon: const Icon(Icons.arrow_upward, color: AppColors.primary), tooltip: '보내기', onPressed: () => _ask(_input.text)),
            ]),
          ),
        ),
      ),
    );
  }
}

class _Segment extends StatelessWidget {
  const _Segment({required this.value, required this.onChanged});
  final String value;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    Widget seg(String v, String label) {
      final on = v == value;
      return Expanded(
        child: GestureDetector(
          onTap: () => onChanged(v),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 150),
            margin: const EdgeInsets.all(4),
            alignment: Alignment.center,
            decoration: BoxDecoration(color: on ? AppColors.surface : Colors.transparent, borderRadius: BorderRadius.circular(9), boxShadow: on ? AppShadow.card : null),
            child: Text(label, style: AppText.label.copyWith(color: on ? AppColors.text : AppColors.text2, fontWeight: on ? FontWeight.w700 : FontWeight.w500)),
          ),
        ),
      );
    }

    return Container(
      height: 40,
      decoration: BoxDecoration(color: AppColors.field, borderRadius: BorderRadius.circular(12)),
      child: Row(children: [seg('short', '짧게 · 30초'), seg('long', '자세히 · 2~3분')]),
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.icon, required this.title, required this.items, this.color = AppColors.text});
  final IconData icon;
  final String title;
  final List<String> items;
  final Color color;

  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 20),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(icon, size: 18, color: color),
          const SizedBox(width: 6),
          Text(title, style: AppText.label.copyWith(fontWeight: FontWeight.w700, color: color)),
        ]),
        const SizedBox(height: 6),
        for (final s in items) _Bullet(text: s, color: color == AppColors.text ? AppColors.text3 : color),
      ]),
    );
  }
}

class _Bullet extends StatelessWidget {
  const _Bullet({required this.text, required this.color});
  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 4),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Padding(padding: const EdgeInsets.only(top: 8, right: 8), child: Container(width: 4, height: 4, decoration: BoxDecoration(color: color, shape: BoxShape.circle))),
          Expanded(child: Text(text, style: AppText.label.copyWith(fontWeight: FontWeight.w400, color: AppColors.text))),
        ]),
      );
}

class _SourceChip extends StatelessWidget {
  const _SourceChip({required this.source});
  final NoteSource source;

  @override
  Widget build(BuildContext context) {
    final tier = switch (source.tier) { 'official' => '공식', 'press' => '언론', 'community' => '방문 후기', _ => null };
    final title = source.title.split(' · ').first;
    return InfoChip(label: tier == null ? title : '$title($tier)', bg: source.tier == 'community' ? AppColors.surface : AppColors.field);
  }
}

class _Preparing extends StatelessWidget {
  const _Preparing({required this.onListen});
  final VoidCallback onListen;

  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        for (final w in const [1.0, 0.9, 0.7]) FractionallySizedBox(alignment: Alignment.centerLeft, widthFactor: w, child: Container(height: 14, margin: const EdgeInsets.only(bottom: 10), color: AppColors.line)),
        Text('해설을 준비하고 있어요', style: AppText.meta),
        const SizedBox(height: 12),
        AppButton(label: '지금 설명 듣기', icon: Icons.auto_awesome, kind: ButtonKind.aiSoft, onPressed: onListen),
      ]);
}

class _NoNote extends StatelessWidget {
  const _NoNote({required this.onListen});
  final VoidCallback onListen;

  @override
  Widget build(BuildContext context) => StateView(
        icon: Icons.menu_book_outlined,
        title: '준비된 해설이 없어요',
        message: '지금 구룡메이트에게 물어보면 10초쯤 걸려요',
        actionLabel: '지금 설명 듣기',
        onAction: onListen,
      );
}
