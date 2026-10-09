import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/api/api_client.dart';
import '../../core/models/account.dart';
import '../../core/models/agent.dart';
import '../../core/models/plan.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../planner/plan_controller.dart';

/// 32_Guide_Prep (Figma 73:5746, 흐름 F10). 여행 전에 장소별 맞춤 해설을 미리 만든다.
class GuidePrepScreen extends StatefulWidget {
  const GuidePrepScreen({super.key, required this.plan});
  final PlanController plan;

  /// 작업 조회 주기 (agent §12, 3초). 테스트에서 줄인다.
  static Duration pollInterval = const Duration(seconds: 3);

  @override
  State<GuidePrepScreen> createState() => _GuidePrepScreenState();
}

class _Place {
  _Place(this.placeId, this.title);
  final String placeId;
  final String title;
}

class _GuidePrepScreenState extends State<GuidePrepScreen> {
  Map<String, String> _status = {}; // place_id → pending | preparing | ready | failed | stale
  final _sources = <String, int>{};
  GuideJob? _job;
  Object? _error;
  bool _loading = true;
  Timer? _poll;

  PlanController get c => widget.plan;

  /// 일정에 있는 장소만 (빠진 장소 노트는 서버에 남지만 숨긴다). 날짜·순서대로, 같은 장소는 한 번.
  List<_Place> get _places {
    final seen = <String>{};
    return [
      for (final i in c.plan?.allItems ?? const <PlanItem>[])
        if (!i.skipped && seen.add(i.placeId)) _Place(i.placeId, i.title),
    ];
  }

  @override
  void initState() {
    super.initState();
    _loadNotes();
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _loadNotes() async {
    try {
      final notes = await c.api.guideNotes(c.planId);
      _status = {for (final GuideNoteSummary n in notes) n.placeId: n.status};
      _error = null;
    } catch (e) {
      _error = e;
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _prepare({List<String>? placeIds}) async {
    try {
      final job = await c.api.guidePrepare(c.planId, placeIds: placeIds);
      _applyJob(job);
      if (job.running) _startPolling(job.jobId);
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  void _startPolling(String jobId) {
    _poll?.cancel();
    _poll = Timer.periodic(GuidePrepScreen.pollInterval, (_) async {
      try {
        final job = await c.api.guideJob(c.planId, jobId);
        _applyJob(job);
        if (!job.running) {
          _poll?.cancel();
          await _loadNotes();
        }
      } on ApiException catch (e) {
        // 작업이 사라졌으면(서버 재시작, 404 job_not_found) 노트 목록으로
        _poll?.cancel();
        if (e.code == 'job_not_found') {
          _job = null;
          await _loadNotes();
        }
      }
    });
  }

  void _applyJob(GuideJob job) {
    _job = job;
    for (final p in job.places) {
      _status[p.placeId] = p.status;
      if (p.sources > 0) _sources[p.placeId] = p.sources;
    }
    if (mounted) setState(() {});
  }

  Future<void> _preview(_Place p) async {
    Map<String, dynamic>? note;
    try {
      note = await c.api.guideNote(c.planId, p.placeId);
    } catch (e) {
      if (mounted) showError(context, e);
      return;
    }
    if (!mounted) return;
    final sources = [for (final s in (note['sources'] as List?) ?? const []) s is Map ? '${s['title'] ?? s['url'] ?? ''}' : '$s'];
    final highlights = [for (final h in (note['highlights'] as List?) ?? const []) '$h'];
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (ctx) => FractionallySizedBox(
        heightFactor: 0.7,
        child: ListView(padding: const EdgeInsets.fromLTRB(20, 0, 20, 24), children: [
          Text(p.title, style: AppText.headline),
          const SizedBox(height: 10),
          Text('${note!['short'] ?? ''}', style: AppText.body),
          if (highlights.isNotEmpty) ...[
            const SizedBox(height: 14),
            Text('볼거리', style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
            for (final h in highlights) Padding(padding: const EdgeInsets.only(top: 4), child: Text('· $h', style: AppText.meta)),
          ],
          if (sources.isNotEmpty) ...[
            const SizedBox(height: 14),
            Text('출처', style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
            for (final s in sources) Padding(padding: const EdgeInsets.only(top: 4), child: Text(s, style: AppText.caption)),
          ],
        ]),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final places = _places;
    final readyCount = places.where((p) => _status[p.placeId] == 'ready').length;
    final running = _job?.running ?? false;
    final needs = places.where((p) => _status[p.placeId] != 'ready').isNotEmpty;
    final stale = places.where((p) => _status[p.placeId] == 'stale').isNotEmpty;
    final eta = _job?.etaSec ?? (places.length - readyCount) * 20;
    return Scaffold(
      appBar: AppBar(
        centerTitle: true,
        title: Column(children: [
          Text('현장 가이드 준비', style: AppText.headline),
          Text(c.plan?.title ?? '', style: AppText.caption),
        ]),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null && _status.isEmpty
              ? StateView(icon: Icons.dns_outlined, title: '서버에 연결하지 못했어요', message: '잠시 후 다시 시도해 주세요.', actionLabel: '다시 시도', onAction: _loadNotes)
              : ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 24), children: [
                  AppCard(
                    padding: const EdgeInsets.all(16),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('${places.length}곳 중 $readyCount곳 준비됐어요', style: AppText.section),
                      const SizedBox(height: 10),
                      ClipRRect(
                        borderRadius: BorderRadius.circular(4),
                        child: LinearProgressIndicator(
                          value: places.isEmpty ? 0 : readyCount / places.length,
                          minHeight: 8,
                          color: AppColors.ai,
                          backgroundColor: AppColors.aiLine,
                        ),
                      ),
                      const SizedBox(height: 10),
                      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        const Icon(Icons.auto_awesome, size: 14, color: AppColors.ai),
                        const SizedBox(width: 6),
                        Expanded(child: Text('관심사와 동행 정보를 반영해 장소마다 해설을 미리 써 둬요. 현장에서는 기다리지 않고 바로 들을 수 있어요.', style: AppText.caption.copyWith(color: AppColors.text2))),
                      ]),
                      if (running) ...[
                        const SizedBox(height: 8),
                        Text('준비 중 · 약 ${(eta / 60).ceil()}분 남음', style: AppText.caption),
                      ],
                    ]),
                  ),
                  const SizedBox(height: 16),
                  for (final (i, p) in places.indexed) ...[
                    _PlaceRow(
                      index: i + 1,
                      place: p,
                      status: _status[p.placeId] ?? 'pending',
                      sources: _sources[p.placeId],
                      onRetry: running ? null : () => _prepare(placeIds: [p.placeId]),
                      onOpen: () => _preview(p),
                    ),
                    const SizedBox(height: 8),
                  ],
                  const SizedBox(height: 4),
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Icon(Icons.info_outline, size: 14, color: AppColors.text3),
                    const SizedBox(width: 6),
                    Expanded(child: Text('출처를 찾지 못한 곳은 지어내지 않아요. 현장에서 물어보시면 그때 찾아볼게요.', style: AppText.caption)),
                  ]),
                ]),
      bottomNavigationBar: _loading
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
                child: running
                    ? const AppButton(label: '준비 중…', kind: ButtonKind.ai, onPressed: null)
                    : needs && !stale
                        ? AppButton(label: '가이드 준비', icon: Icons.auto_awesome, kind: ButtonKind.ai, onPressed: () => _prepare())
                        : stale
                            ? AppButton(label: '다시 준비', kind: ButtonKind.secondary, onPressed: () => _prepare())
                            : const AppButton(label: '모두 준비됐어요', kind: ButtonKind.secondary, onPressed: null),
              ),
            ),
    );
  }
}

class _PlaceRow extends StatelessWidget {
  const _PlaceRow({required this.index, required this.place, required this.status, required this.sources, required this.onRetry, required this.onOpen});
  final int index;
  final _Place place;
  final String status;
  final int? sources;
  final VoidCallback? onRetry;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final (chip, fg, bg, icon, text) = switch (status) {
      'preparing' => ('준비 중', AppColors.ai, AppColors.aiSoft, Icons.autorenew, '자료를 찾는 중'),
      'ready' => ('준비됨', AppColors.success, AppColors.successSoft, Icons.check, sources == null ? '준비됨' : '준비됨 · 출처 $sources개'),
      'failed' => ('다시 시도', AppColors.danger, AppColors.dangerSoft, Icons.warning_amber_rounded, '자료를 찾지 못했어요'),
      'stale' => ('다시 준비', AppColors.warning, AppColors.warningSoft, Icons.refresh, '관심사가 바뀌었어요'),
      _ => ('대기', AppColors.text2, AppColors.field, null, '대기'),
    };
    final tappable = status == 'failed' || status == 'stale';
    return AppCard(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      onTap: status == 'ready' ? onOpen : null,
      child: Row(children: [
        Container(
          width: 32,
          height: 32,
          alignment: Alignment.center,
          decoration: const BoxDecoration(color: AppColors.background, shape: BoxShape.circle),
          child: Text('$index', style: AppText.label.copyWith(color: AppColors.text2)),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(place.title, style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
            Text(text, style: AppText.caption.copyWith(color: AppColors.text2)),
          ]),
        ),
        Material(
          color: bg,
          borderRadius: BorderRadius.circular(12),
          child: InkWell(
            borderRadius: BorderRadius.circular(12),
            onTap: tappable ? onRetry : null,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                if (icon != null) ...[Icon(icon, size: 14, color: fg), const SizedBox(width: 4)],
                Text(chip, style: AppText.caption.copyWith(color: fg, fontWeight: FontWeight.w700)),
              ]),
            ),
          ),
        ),
      ]),
    );
  }
}
