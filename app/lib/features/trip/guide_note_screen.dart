import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/api/api_client.dart';
import '../../core/models/account.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../../core/widgets/rich_answer.dart';
import '../chat/ai_sheet.dart';
import '../chat/chat_controller.dart';
import 'trip_controller.dart';

/// 17 Guide Note (Figma 73:2587): 여행 전에 준비한 맞춤 해설을 기다림 없이. 빈 배열 섹션은 숨긴다.
/// 2026-10-11: 이 화면 안에서 질문(같은 대화 세션 + place_id) · [다시 준비]/[가이드 준비] · 노트가 바뀌면 새 노트로.
class GuideNoteScreen extends StatefulWidget {
  const GuideNoteScreen({super.key, required this.t, required this.placeId, this.initialLength});
  final TripController t;
  final String placeId;
  final String? initialLength; // short | long

  /// 다시 준비 작업·노트 목록 조회 주기 (32 와 같은 3초). 테스트에서 줄인다.
  static Duration pollInterval = const Duration(seconds: 3);

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
  bool _memoryRead = false;
  final _input = TextEditingController();
  final _scroll = ScrollController();

  /// [다시 준비]/[가이드 준비] 를 눌러 작업이 끝나기를 기다리는 중.
  bool _preparing = false;
  Timer? _jobTimer;
  bool _jobBusy = false;

  /// 노트 목록 요약(상태·updated_at) — 바뀌면 이 노트를 다시 읽는다 (stale → ready 자동 재준비).
  String? _summaryKey;
  Timer? _notesTimer;

  /// 보낸 질문의 답을 따라 내려간다 (사용자가 직접 스크롤하면 멈춤).
  bool _follow = false;

  TripController get t => widget.t;
  ChatController? get _chat => t.chat;

  String get _title => t.plan.plan?.allItems.where((i) => i.placeId == widget.placeId).firstOrNull?.title ?? '';

  GuideNoteSummary? get _summary => t.guideNotes?[widget.placeId];

  bool get _summaryPreparing => _summary?.status == 'pending' || _summary?.status == 'preparing';

  @override
  void initState() {
    super.initState();
    _length = widget.initialLength ?? t.preferredLength ?? 'short';
    t.pushed(true);
    t.addListener(_onTrip);
    _chat?.addListener(_onChat);
    _chat?.init();
    _load();
  }

  @override
  void dispose() {
    t.removeListener(_onTrip);
    _chat?.removeListener(_onChat);
    _jobTimer?.cancel();
    _notesTimer?.cancel();
    t.pushed(false);
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final note = await t.api.guideNoteDetail(t.planId, widget.placeId);
      _note = note;
      _missing = note.status == 'failed';
      _error = null;
      if (note.ready) t.heard[widget.placeId] = _length;
    } on ApiException catch (e) {
      if (e.status == 404) {
        _note = null;
        _missing = true;
      } else if (!silent) {
        _error = e;
      }
    }
    if (!_memoryRead) {
      _memoryRead = true;
      try {
        // 동행자 팁 제목: 메모리 companion 에서 사람 이름만 ("어머니와 함께" → 어머니)
        final mem = await t.api.memory(planId: t.planId);
        final c = mem.items.where((m) => m.category == 'companion').firstOrNull?.text;
        final m = c == null ? null : RegExp(r'(어머니|아버지|엄마|아빠|부모님|아이|딸|아들|친구|남편|아내|할머니|할아버지)').firstMatch(c);
        _companion = m?.group(1);
      } catch (_) {}
    }
    final s = _summary;
    _summaryKey = s == null ? null : '${s.status}|${s.updatedAt}';
    if (!mounted) return;
    setState(() => _loading = false);
    _watchNotes();
  }

  /// 노트 목록이 바뀌면(도착 자동 준비·32 의 준비): 준비가 끝났으면 이 노트를 다시 읽는다.
  void _onTrip() {
    final s = _summary;
    final key = s == null ? null : '${s.status}|${s.updatedAt}';
    if (key == _summaryKey || !mounted) return;
    final before = _summaryKey;
    _summaryKey = key;
    final done = s != null && (s.status == 'ready' || s.status == 'stale' || s.status == 'failed');
    if (done && !_preparing && !_loading && (before != null || s.status != _note?.status)) {
      _load(silent: true);
    } else {
      setState(() {});
      _watchNotes();
    }
  }

  /// 준비 중이거나 stale 이면(자동 재준비가 돌 수 있다) 노트 목록을 가볍게 다시 읽는다.
  void _watchNotes() {
    final want = !_preparing && (_summaryPreparing || (_note?.preparing ?? false) || _note?.status == 'stale');
    if (!want) {
      _notesTimer?.cancel();
      _notesTimer = null;
    } else {
      _notesTimer ??= Timer.periodic(GuideNoteScreen.pollInterval, (_) => t.loadGuideNotes());
    }
  }

  /// [다시 준비]/[가이드 준비]: 이 장소만 force 로 → 작업이 끝나면 새 노트 (32 와 같은 작업 API).
  Future<void> _prepare() async {
    if (_preparing) return;
    setState(() => _preparing = true);
    _watchNotes();
    try {
      final job = await t.api.guidePrepare(t.planId, placeIds: [widget.placeId], force: true);
      if (!job.running) {
        await _finishPrepare();
        return;
      }
      _jobTimer?.cancel();
      _jobTimer = Timer.periodic(GuideNoteScreen.pollInterval, (_) => _pollJob(job.jobId));
    } catch (e) {
      if (!mounted) return;
      setState(() => _preparing = false);
      showError(context, e);
    }
  }

  Future<void> _pollJob(String jobId) async {
    if (_jobBusy) return;
    _jobBusy = true;
    try {
      final job = await t.api.guideJob(t.planId, jobId);
      if (job.running) return;
    } on ApiException catch (e) {
      if (e.code != 'job_not_found') return; // 작업이 사라졌으면(서버 재시작) 노트를 다시 읽는다
    } catch (_) {
      return;
    } finally {
      _jobBusy = false;
    }
    _jobTimer?.cancel();
    await _finishPrepare();
  }

  Future<void> _finishPrepare() async {
    _preparing = false;
    await t.loadGuideNotes();
    if (mounted) await _load(silent: true);
  }

  void _setLength(String l) {
    setState(() => _length = l);
    t.preferredLength = l; // 다음 장소 기본값 (메모리 저장은 대화로 말했을 때만, docent §1.2)
    t.heard[widget.placeId] = l;
  }

  /// qa 칩 · 입력 → 이 화면 안에서 대화 (place_id, 같은 세션이라 AI 시트에도 남는다). 오프라인이면 노트의 답을 펼친다.
  void _ask(String q, [String? answer]) {
    final text = q.trim();
    if (text.isEmpty) return;
    if (t.offline && answer != null) {
      showModalBottomSheet<void>(
        context: context,
        showDragHandle: true,
        builder: (_) => SafeArea(child: Padding(padding: const EdgeInsets.fromLTRB(20, 0, 20, 20), child: RichAnswer(answer))),
      );
      return;
    }
    final chat = _chat;
    if (chat == null) return;
    if (chat.busy) {
      showInfo(context, '구룡메이트가 답하는 중이에요. 잠시 후 다시 눌러 주세요');
      return;
    }
    _input.clear();
    _follow = true;
    chat.send(text, placeId: widget.placeId);
    _toBottom();
  }

  void _onChat() {
    if (!mounted) return;
    setState(() {});
    if (_follow) _toBottom();
  }

  void _toBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && _scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
    });
  }

  /// 답에 제안·추천 카드가 생기면 이 화면에는 한 줄만 → AI 시트의 그 카드로.
  void _openCards(ChatTurn turn) => t.onOpenAi?.call(messageId: turn.messageId, placeTitle: _title);

  @override
  Widget build(BuildContext context) {
    final n = _note;
    final chat = _chat;
    final turns = chat == null ? const <ChatTurn>[] : chat.turns.where((x) => x.placeId == widget.placeId).toList();
    final busy = chat?.busy ?? false;
    // 다시 준비하는 중: 직접 누른 작업 또는 서버의 자동 재준비 (예전 노트는 그대로 보인다)
    final reprepare = _preparing || _summaryPreparing;
    final talk = [
      if (turns.isNotEmpty && chat != null) ...[
        const SizedBox(height: 24),
        Row(children: [
          const Icon(Icons.forum_outlined, size: 18, color: AppColors.ai),
          const SizedBox(width: 6),
          Text('이 장소에서 나눈 이야기', style: AppText.label.copyWith(fontWeight: FontWeight.w700, color: AppColors.text)),
        ]),
        const SizedBox(height: 4),
        ChangeNotifierProvider.value(
          value: chat,
          child: Column(key: const ValueKey('guide-talk'), crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            for (final (i, x) in turns.indexed) TurnView(turn: x, isLast: i == turns.length - 1, onFollowUp: _ask, onOpenCards: () => _openCards(x)),
          ]),
        ),
      ],
    ];
    void listen() => _ask('$_title 짧게 설명해줘');
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
              : NotificationListener<UserScrollNotification>(
                  onNotification: (_) {
                    _follow = false;
                    return false;
                  },
                  child: _missing || n == null
                      ? ListView(controller: _scroll, padding: const EdgeInsets.fromLTRB(20, 4, 20, 24), children: [
                          if (reprepare)
                            Padding(padding: const EdgeInsets.only(top: 24), child: _Preparing(onListen: listen))
                          else
                            _NoNote(onPrepare: _prepare, onListen: listen),
                          ...talk,
                        ])
                      : ListView(controller: _scroll, padding: const EdgeInsets.fromLTRB(20, 4, 20, 24), children: [
                          Wrap(spacing: 6, runSpacing: 6, children: [
                            if (t.plan.plan?.allItems.where((i) => i.placeId == widget.placeId).firstOrNull?.place?.contentType case final ct? when ct.isNotEmpty) InfoChip(label: ct),
                            InfoChip(
                              label: '내 관심사 반영${n.preparedAt == null ? '' : ' · ${n.preparedAt!.month}/${n.preparedAt!.day} 준비'}',
                              icon: Icons.auto_awesome,
                              fg: AppColors.ai,
                              bg: AppColors.aiSoft,
                            ),
                          ]),
                          // stale 칩 대신 "관심사가 바뀌었어요 · [다시 준비]" → 누르면 "다시 준비하고 있어요" (2026-10-11)
                          if (reprepare && !(n.preparing && n.short.isEmpty))
                            const _NoteLine(key: ValueKey('guide-preparing'), text: '다시 준비하고 있어요', busy: true)
                          else if (n.status == 'stale')
                            _NoteLine(key: const ValueKey('guide-stale'), text: '관심사가 바뀌었어요', action: '다시 준비', onAction: _prepare),
                          const SizedBox(height: 14),
                          if (n.preparing && n.short.isEmpty)
                            _Preparing(onListen: listen)
                          else ...[
                            _Segment(value: _length, onChanged: _setLength),
                            const SizedBox(height: 16),
                            RichAnswer(
                              _length == 'long' && n.long.isNotEmpty ? n.long : n.short,
                              key: const ValueKey('note-body'),
                              style: AppText.body.copyWith(fontSize: 16, height: 26 / 16),
                              paragraphGap: 14,
                            ),
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
                          ...talk,
                        ]),
                ),
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
              if (busy)
                IconButton(icon: const Icon(Icons.stop_rounded, color: AppColors.text), tooltip: '중단', onPressed: chat?.stop)
              else
                IconButton(icon: const Icon(Icons.arrow_upward, color: AppColors.primary), tooltip: '보내기', onPressed: () => _ask(_input.text)),
            ]),
          ),
        ),
      ),
    );
  }
}

/// 태그 줄 아래 한 줄: "관심사가 바뀌었어요 · [다시 준비]"(Amber) / "다시 준비하고 있어요"(Purple).
class _NoteLine extends StatelessWidget {
  const _NoteLine({super.key, required this.text, this.action, this.onAction, this.busy = false});
  final String text;
  final String? action;
  final VoidCallback? onAction;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final fg = busy ? AppColors.ai : AppColors.warning;
    return Container(
      margin: const EdgeInsets.only(top: 10),
      height: 40,
      padding: const EdgeInsets.only(left: 12, right: 4),
      decoration: BoxDecoration(color: busy ? AppColors.aiSoft : AppColors.warningSoft, borderRadius: BorderRadius.circular(12)),
      child: Row(children: [
        if (busy) SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: fg)) else Icon(Icons.refresh, size: 16, color: fg),
        const SizedBox(width: 8),
        Expanded(child: Text(text, style: AppText.meta.copyWith(color: fg, fontWeight: FontWeight.w700))),
        if (action != null)
          TextButton(
            onPressed: onAction,
            style: TextButton.styleFrom(foregroundColor: fg, visualDensity: VisualDensity.compact),
            child: Text(action!, style: AppText.meta.copyWith(color: fg, fontWeight: FontWeight.w700, decoration: TextDecoration.underline, decorationColor: fg)),
          ),
      ]),
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
  Widget build(BuildContext context) {
    final style = AppText.label.copyWith(fontWeight: FontWeight.w400, color: AppColors.text);
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(padding: const EdgeInsets.only(top: 8, right: 8), child: Container(width: 4, height: 4, decoration: BoxDecoration(color: color, shape: BoxShape.circle))),
        Expanded(child: Text.rich(TextSpan(children: inlineSpans(text, style)), style: style)),
      ]),
    );
  }
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

/// 노트가 없거나 실패한 곳 (16-S1): [지금 설명 듣기](이 화면 대화) + [가이드 준비](이 장소만, 2026-10-11).
class _NoNote extends StatelessWidget {
  const _NoNote({required this.onPrepare, required this.onListen});
  final VoidCallback onPrepare;
  final VoidCallback onListen;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(12, 40, 12, 8),
        child: Column(children: [
          const Icon(Icons.menu_book_outlined, size: 40, color: AppColors.text3),
          const SizedBox(height: 12),
          Text('준비된 해설이 없어요', style: AppText.section, textAlign: TextAlign.center),
          const SizedBox(height: 6),
          Text('지금 구룡메이트에게 물어보면 10초쯤 걸려요', style: AppText.meta, textAlign: TextAlign.center),
          const SizedBox(height: 18),
          AppButton(label: '지금 설명 듣기', icon: Icons.auto_awesome, kind: ButtonKind.aiSoft, height: 48, onPressed: onListen),
          const SizedBox(height: 8),
          AppButton(label: '가이드 준비', kind: ButtonKind.secondary, height: 48, onPressed: onPrepare),
        ]),
      );
}
