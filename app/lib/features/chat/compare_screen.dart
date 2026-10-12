import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart' hide Path;
import 'package:provider/provider.dart';

import '../../core/api/api_client.dart';
import '../../core/models/plan.dart';
import '../../core/models/safety.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../../core/widgets/safety_widgets.dart';
import '../map/map_layers.dart';
import '../planner/plan_controller.dart';
import 'chat_controller.dart';
import 'proposal_actions.dart';
import 'proposal_card.dart';

/// 21 비교 보기 (풀스크린 모달). 06 카드의 "비교 보기 ›".
Future<void> openCompare(BuildContext context, {required ChatController chat, required PlanController plan, required ProposalEntry entry}) =>
    Navigator.of(context).push(MaterialPageRoute(
      fullscreenDialog: true,
      builder: (_) => MultiProvider(
        providers: [ChangeNotifierProvider.value(value: chat), ChangeNotifierProvider.value(value: plan)],
        child: CompareScreen(entry: entry),
      ),
    ));

/// 21_Compare (Figma 73:3360, 지연 21-S1 73:7842). 기존안/수정안을 타임라인 + 지도로 나란히.
class CompareScreen extends StatefulWidget {
  const CompareScreen({super.key, required this.entry});
  final ProposalEntry entry;

  @override
  State<CompareScreen> createState() => _CompareScreenState();
}

class _CompareScreenState extends State<CompareScreen> {
  SafetyCheck? _beforeSafety;
  SafetyCheck? _afterSafety;
  bool _safetyFailed = false;
  bool _loading = true;
  List<AvoidZone> _zones = const [];
  List<List<LatLng>> _beforeLines = const [];
  List<List<LatLng>> _afterLines = const [];
  int? _highlight; // 하이라이트한 장소 (place_id 해시)
  final _lines = <int, (List<List<LatLng>>, List<List<LatLng>>)>{}; // 날별 경로선 (기존안, 수정안)

  ProposalDetail get p => widget.entry.proposal;

  /// 고른 날 (여러 날 제안이면 날짜 탭). 처음엔 위반 있는 날 → 바뀐 첫날.
  late int _day = p.changedDays.where((d) => p.errorsOn(d) > 0).firstOrNull ?? p.changedDays.first;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final plan = context.read<PlanController>();
    final api = plan.api;
    final pending = widget.entry.state == ProposalState.pending;
    try {
      // 기존안 위험 = 지금 일정 점검, 수정안 위험 = ops 를 적용했다고 치고 점검 (승인 뒤면 지금 일정이 수정안)
      final cur = await api.safety(plan.planId);
      if (pending) {
        _beforeSafety = cur;
        _afterSafety = await api.safetyPreview(plan.planId, p.ops);
      } else {
        _afterSafety = cur;
      }
    } on ApiException {
      _safetyFailed = true;
    } catch (_) {
      _safetyFailed = true;
    }
    try {
      _zones = await api.avoidZones();
    } catch (_) {}
    await _loadLines(_day);
    if (mounted) setState(() => _loading = false);
  }

  /// 그날의 경로선 (한 번 그린 날은 다시 묻지 않는다).
  Future<void> _loadLines(int day) async {
    final cached = _lines[day];
    if (cached == null) {
      if (!mounted) return;
      final plan = context.read<PlanController>();
      final b = await _routeLines(plan, _remaining(p.before?.dayOf(day)));
      final a = await _routeLines(plan, _remaining(p.after?.dayOf(day)));
      _lines[day] = (b, a);
    }
    if (!mounted || day != _day) return;
    final (b, a) = _lines[day]!;
    setState(() {
      _beforeLines = b;
      _afterLines = a;
    });
  }

  void _pickDay(int day) {
    if (day == _day) return;
    setState(() {
      _day = day;
      _highlight = null;
      _beforeLines = const [];
      _afterLines = const [];
    });
    _loadLines(day);
  }

  List<PlanItem> _remaining(PlanDay? d) => (d?.items ?? const <PlanItem>[]).where((i) => !i.skipped && i.status != 'done' && i.place != null).toList();

  Future<List<List<LatLng>>> _routeLines(PlanController c, List<PlanItem> items) async {
    final out = <List<LatLng>>[];
    for (var i = 1; i < items.length; i++) {
      final a = LatLng(items[i - 1].place!.lat, items[i - 1].place!.lng), b = LatLng(items[i].place!.lat, items[i].place!.lng);
      final r = await c.routes.leg(items[i].legMode ?? c.plan?.travelMode ?? 'walk', a, b, hazards: 'report');
      out.add(r == null || r.points.length < 2 ? [a, b] : r.points);
    }
    return out;
  }

  /// 수정안 항목의 안전 판정. 승인한 뒤에는 수정안 = 지금 일정이라 새로 들어간 항목(임시 음수 id)을
  /// 지금 일정에서 같은 날·같은 장소로 찾아 맞춘다 (audit-2026-10-12 F11).
  SafetyItem? _afterVisit(PlanItem a) {
    final s = _afterSafety;
    if (s == null) return null;
    return s.visit(a.id) ??
        (widget.entry.state == ProposalState.pending ? null : s.visit(appliedItemId(a, context.read<PlanController>().plan) ?? a.id));
  }

  Future<void> _accept() async {
    final chat = context.read<ChatController>();
    final nav = Navigator.of(context);
    final reproposed = await acceptWithFlow(context, chat, widget.entry);
    if (!mounted) return;
    if (reproposed || widget.entry.state == ProposalState.applied) nav.pop();
  }

  Future<void> _reject() async {
    final chat = context.read<ChatController>();
    await rejectWithFlow(context, chat, widget.entry);
    if (mounted && widget.entry.state == ProposalState.rejected) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    context.watch<ChatController>();
    final day = _day;
    final days = p.changedDays;
    final before = p.before?.dayOf(day), after = p.after?.dayOf(day);
    final doneCount = (after?.items ?? const <PlanItem>[]).where((i) => i.status == 'done').length;
    final rows = _pairRows(_remaining(before), _remaining(after));
    final changeRows = p.before != null ? buildChangeRows(p) : null;
    final changeCount = changeRows?.length ?? p.changes.length;
    // 여러 날: 고른 날의 변경 수도 (날이 없는 조건 줄은 전체에만)
    final dayCount = changeRows?.where((r) => r.day == day).length ?? p.changes.where((c) => c.day == day && c.kind != 'day_end').length;
    return Scaffold(
      backgroundColor: AppColors.surface,
      body: Column(children: [
        SizedBox(
          height: 300 + MediaQuery.paddingOf(context).top,
          child: Stack(children: [
            _CompareMap(
              key: ValueKey('compare-map-$day'), // 날을 바꾸면 카메라를 그날 장소에 다시 맞춘다
              before: _remaining(before),
              after: _remaining(after),
              beforeLines: _beforeLines,
              afterLines: _afterLines,
              zones: _zones,
              onPin: (id) => setState(() => _highlight = id),
            ),
            SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  _TitlePill(
                    count: changeCount,
                    dayCount: days.length > 1 ? dayCount : null,
                    day: day,
                    onClose: () => Navigator.of(context).pop(),
                  ),
                  if (days.length > 1) ...[
                    const SizedBox(height: 8),
                    _DayTabs(proposal: p, days: days, selected: day, onPick: _pickDay),
                  ],
                  if (p.baseChanged && widget.entry.state == ProposalState.pending) ...[
                    const SizedBox(height: 8),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      decoration: BoxDecoration(color: AppColors.warningSoft, borderRadius: BorderRadius.circular(12)),
                      child: Text('제안 이후 일정이 바뀌었어요 · 적용 전에 확인해요', style: AppText.caption.copyWith(color: AppColors.warning, fontWeight: FontWeight.w700)),
                    ),
                  ],
                ]),
              ),
            ),
            const Positioned(left: 16, bottom: 12, child: _Legend()),
            if (_loading) const Positioned(left: 0, right: 0, bottom: 0, child: LinearProgressIndicator(minHeight: 2)),
          ]),
        ),
        Expanded(
          child: ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 16), children: [
            _Metrics(proposal: p, day: day, before: before, after: after, beforeSafety: _beforeSafety, afterSafety: _afterSafety, safetyFailed: _safetyFailed),
            const SizedBox(height: 16),
            if (doneCount > 0) Padding(padding: const EdgeInsets.only(bottom: 8), child: Text('다녀온 곳 $doneCount곳', style: AppText.caption)),
            Row(children: [
              Expanded(child: Text('기존안 · 지금 일정', style: AppText.label.copyWith(color: AppColors.text2, fontWeight: FontWeight.w700))),
              const SizedBox(width: 8),
              Expanded(child: Text('수정안', style: AppText.label.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700))),
            ]),
            const SizedBox(height: 8),
            for (final r in rows)
              _CompareRow(
                pair: r,
                beforeSafety: _beforeSafety,
                afterSafety: _afterSafety,
                afterVisit: _afterVisit,
                safetyFailed: _safetyFailed,
                highlight: _highlight,
                proposal: p,
                onTap: (id) => setState(() => _highlight = id),
              ),
          ]),
        ),
        SafeArea(
          top: false,
          child: Container(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
            decoration: const BoxDecoration(border: Border(top: BorderSide(color: AppColors.line))),
            child: _BottomBar(entry: widget.entry, onAccept: _accept, onReject: _reject),
          ),
        ),
      ]),
    );
  }
}

/// 승인한 제안의 수정안 항목 [a] 가 지금 일정 [current] 에서 가진 id. 새로 들어간 항목은 수정안에서 임시 음수 id 라
/// 같은 날·같은 장소로 찾는다 (없으면 null). 이미 있던 항목(양수 id)은 그대로.
int? appliedItemId(PlanItem a, PlanDetail? current) {
  if (a.id >= 0) return a.id;
  return current?.dayOf(a.day)?.items.where((i) => i.placeId == a.placeId).firstOrNull?.id;
}

class _Pair {
  _Pair(this.before, this.after);
  final PlanItem? before;
  final PlanItem? after;
  bool get changed => before == null || after == null || before!.placeId != after!.placeId;
}

/// 행 맞추기: 둘 다 있는 항목(같은 id)은 한 줄, 빠진 항목은 같은 자리의 새 항목과 한 줄(교체), 남은 새 항목은 오른쪽만.
List<_Pair> _pairRows(List<PlanItem> before, List<PlanItem> after) {
  final afterIds = {for (final a in after) a.id};
  final used = <int>{};
  final out = <_Pair>[];
  for (final b in before) {
    if (afterIds.contains(b.id)) {
      final a = after.firstWhere((x) => x.id == b.id);
      used.add(a.id);
      out.add(_Pair(b, a));
    } else {
      final rep = after.where((a) => a.id < 0 && !used.contains(a.id) && a.order == b.order).firstOrNull;
      if (rep != null) used.add(rep.id);
      out.add(_Pair(b, rep));
    }
  }
  for (final a in after) {
    if (used.contains(a.id) || before.any((b) => b.id == a.id)) continue;
    final idx = out.indexWhere((r) => (r.after?.order ?? r.before?.order ?? 0) > a.order);
    out.insert(idx < 0 ? out.length : idx, _Pair(null, a));
  }
  return out;
}

class _TitlePill extends StatelessWidget {
  const _TitlePill({required this.count, required this.onClose, this.dayCount, this.day});
  final int count;
  final VoidCallback onClose;

  /// 여러 날 제안: 고른 날의 변경 수 ("Day 2 변경 1건 · 전체 3건").
  final int? dayCount;
  final int? day;

  @override
  Widget build(BuildContext context) => Container(
        height: 48,
        padding: const EdgeInsets.only(right: 16),
        decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(24), boxShadow: AppShadow.floating),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          IconButton(tooltip: '닫기', icon: const Icon(Icons.close, size: 20), onPressed: onClose),
          Flexible(
            child: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('기존안 · 수정안 비교', style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
              Text(dayCount == null ? '구룡메이트 제안 · 변경 $count건' : 'Day $day 변경 $dayCount건 · 전체 $count건',
                  style: AppText.caption.copyWith(color: AppColors.text2), overflow: TextOverflow.ellipsis),
            ]),
          ),
        ]),
      );
}

/// 바뀐 날만 날짜 탭(칩). 위반 있는 날은 Red 점.
class _DayTabs extends StatelessWidget {
  const _DayTabs({required this.proposal, required this.days, required this.selected, required this.onPick});
  final ProposalDetail proposal;
  final List<int> days;
  final int selected;
  final ValueChanged<int> onPick;

  @override
  Widget build(BuildContext context) => SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(children: [
          for (final d in days) ...[
            if (d != days.first) const SizedBox(width: 6),
            Material(
              key: ValueKey('compare-day-$d'),
              color: d == selected ? AppColors.text : AppColors.surface,
              borderRadius: BorderRadius.circular(16),
              elevation: 1,
              child: InkWell(
                borderRadius: BorderRadius.circular(16),
                onTap: () => onPick(d),
                child: Container(
                  height: 32,
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  alignment: Alignment.center,
                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                    Text(dayLabel(proposal, d),
                        style: AppText.caption.copyWith(color: d == selected ? Colors.white : AppColors.text, fontWeight: FontWeight.w700)),
                    if (proposal.errorsOn(d) > 0) ...[
                      const SizedBox(width: 6),
                      Container(width: 6, height: 6, decoration: const BoxDecoration(color: AppColors.danger, shape: BoxShape.circle)),
                    ],
                  ]),
                ),
              ),
            ),
          ],
        ]),
      );
}

class _Legend extends StatelessWidget {
  const _Legend();

  @override
  Widget build(BuildContext context) {
    Widget chip(String text, Color color) => Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(12), boxShadow: AppShadow.card),
          child: Text(text, style: AppText.caption.copyWith(color: color, fontWeight: FontWeight.w700)),
        );
    return Row(children: [chip('- - 기존안', AppColors.text2), const SizedBox(width: 6), chip('━ 수정안', AppColors.primary)]);
  }
}

class _CompareMap extends StatelessWidget {
  const _CompareMap({super.key, required this.before, required this.after, required this.beforeLines, required this.afterLines, required this.zones, required this.onPin});
  final List<PlanItem> before;
  final List<PlanItem> after;
  final List<List<LatLng>> beforeLines;
  final List<List<LatLng>> afterLines;
  final List<AvoidZone> zones;
  final ValueChanged<int> onPin;

  @override
  Widget build(BuildContext context) {
    final pts = [...before, ...after].map((i) => LatLng(i.place!.lat, i.place!.lng)).toList();
    final afterPlaces = {for (final a in after) a.placeId};
    final beforePlaces = {for (final b in before) b.placeId};
    return FlutterMap(
      options: MapOptions(
        initialCameraFit: pts.length > 1
            ? CameraFit.coordinates(coordinates: pts, padding: const EdgeInsets.fromLTRB(40, 110, 40, 50), maxZoom: 16.5)
            : null,
        initialCenter: pts.isEmpty ? guryongpo : pts.first,
        initialZoom: 15,
      ),
      children: [
        osmTiles(),
        ZoneLayer(zones: zones),
        PolylineLayer(polylines: [
          for (final l in beforeLines) Polyline(points: l, strokeWidth: 4, color: AppColors.text3, pattern: StrokePattern.dashed(segments: const [8, 6])),
          for (final l in afterLines) Polyline(points: l, strokeWidth: 5, color: AppColors.primary),
        ]),
        MarkerLayer(markers: [
          for (final b in before)
            if (!afterPlaces.contains(b.placeId))
              Marker(
                point: LatLng(b.place!.lat, b.place!.lng),
                width: 30,
                height: 30,
                child: GestureDetector(onTap: () => onPin(b.placeId.hashCode), child: const MapDot(color: AppColors.danger, icon: Icons.close)),
              ),
          for (final (i, a) in after.indexed)
            Marker(
              point: LatLng(a.place!.lat, a.place!.lng),
              width: beforePlaces.contains(a.placeId) ? 30 : 150,
              height: beforePlaces.contains(a.placeId) ? 30 : 36,
              child: GestureDetector(
                onTap: () => onPin(a.placeId.hashCode),
                child: beforePlaces.contains(a.placeId)
                    ? MapDot(color: AppColors.primary, label: '${i + 1}')
                    : Row(mainAxisSize: MainAxisSize.min, children: [
                        MapDot(color: AppColors.ai, label: '${i + 1}', size: 36),
                        const SizedBox(width: 4),
                        Flexible(child: MapLabel(text: a.title, color: AppColors.ai)),
                      ]),
              ),
            ),
        ]),
      ],
    );
  }
}

/// 지표 3개: 위험한 곳(또는 규칙 위반) · 종료 · 이동. 좋아지면 Green, 나빠지면 Amber.
class _Metrics extends StatelessWidget {
  const _Metrics({required this.proposal, required this.day, required this.before, required this.after, required this.beforeSafety, required this.afterSafety, required this.safetyFailed});
  final ProposalDetail proposal;
  final int day;
  final PlanDay? before;
  final PlanDay? after;
  final SafetyCheck? beforeSafety;
  final SafetyCheck? afterSafety;
  final bool safetyFailed;

  @override
  Widget build(BuildContext context) {
    int errors(PlanDetail? p) => (p?.violations ?? const <Violation>[]).where((v) => v.isError && (v.day == null || v.day == day)).length;
    final rb = beforeSafety?.riskyVisits, ra = afterSafety?.riskyVisits;
    final eb = errors(proposal.before), ea = errors(proposal.after);
    final useSafety = !safetyFailed && rb != null && ra != null && (rb != ra || eb == ea);
    final (endB, endA) = dayEndChange(proposal, day: day);
    final first = useSafety ? _Metric('위험한 곳', '$rb곳', '$ra곳', ra.compareTo(rb)) : _Metric('규칙 위반', '$eb건', '$ea건', ea.compareTo(eb));
    final eFrom = endB ?? before?.endAt, eTo = endA ?? after?.endAt;
    final end = _Metric('종료', eFrom ?? '-', eTo ?? '-', eFrom == null || eTo == null ? 0 : eTo.compareTo(eFrom)); // 일찍 끝나면 좋아짐
    final tb = before?.travelMinutes ?? 0, ta = after?.travelMinutes ?? 0;
    final move = _Metric('이동', '$tb분', '$ta분', ta.compareTo(tb));
    return Row(children: [
      for (final (i, m) in [first, end, move].indexed) ...[
        if (i > 0) const SizedBox(width: 8),
        Expanded(child: _MetricBox(metric: m)),
      ],
    ]);
  }
}

class _Metric {
  _Metric(this.label, this.from, this.to, this.trend);
  final String label;
  final String from;
  final String to;
  final int trend; // <0 좋아짐(Green), >0 나빠짐(Amber), 0 이면서 값이 바뀌면 Amber
}

class _MetricBox extends StatelessWidget {
  const _MetricBox({required this.metric});
  final _Metric metric;

  @override
  Widget build(BuildContext context) {
    final m = metric;
    final color = m.trend < 0 ? AppColors.success : (m.trend > 0 ? AppColors.warning : (m.from == m.to ? AppColors.text : AppColors.warning));
    return Container(
      height: 64,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(color: AppColors.background, borderRadius: BorderRadius.circular(12)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Text(m.label, style: AppText.caption.copyWith(color: AppColors.text2)),
        FittedBox(
          fit: BoxFit.scaleDown,
          alignment: Alignment.centerLeft,
          child: Row(children: [
            if (m.from != m.to) ...[
              Text(m.from, style: AppText.meta.copyWith(color: AppColors.text3, decoration: TextDecoration.lineThrough)),
              const Text(' › ', style: TextStyle(color: AppColors.text3)),
            ],
            Text(m.to, style: AppText.section.copyWith(color: color)),
          ]),
        ),
      ]),
    );
  }
}

class _CompareRow extends StatelessWidget {
  const _CompareRow({required this.pair, required this.beforeSafety, required this.afterSafety, required this.afterVisit, required this.safetyFailed, required this.highlight, required this.proposal, required this.onTap});
  final _Pair pair;
  final SafetyCheck? beforeSafety;
  final SafetyCheck? afterSafety;
  final SafetyItem? Function(PlanItem a) afterVisit;
  final bool safetyFailed;
  final int? highlight;
  final ProposalDetail proposal;
  final ValueChanged<int> onTap;

  @override
  Widget build(BuildContext context) {
    final b = pair.before, a = pair.after;
    final bs = b == null ? null : beforeSafety?.visit(b.id);
    final as = a == null ? null : afterVisit(a);
    final changed = pair.changed;
    final hazard = bs?.topHazard;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Column(children: [
        IntrinsicHeight(
          child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Expanded(child: b == null ? const SizedBox() : _Cell(item: b, safety: bs, safetyFailed: safetyFailed, kind: changed ? _CellKind.removed : _CellKind.same, highlight: highlight == b.placeId.hashCode, onTap: () => onTap(b.placeId.hashCode))),
            const SizedBox(width: 8),
            Expanded(
              child: a == null
                  ? const _Gone()
                  : _Cell(item: a, safety: as, safetyFailed: safetyFailed, kind: changed ? _CellKind.added : _CellKind.same, highlight: highlight == a.placeId.hashCode, onTap: () => onTap(a.placeId.hashCode)),
            ),
          ]),
        ),
        if (changed && (hazard != null || a != null))
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Expanded(
                child: hazard == null
                    ? const SizedBox()
                    : Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Icon(hazardIcon(hazard.type), size: 14, color: AppColors.danger),
                        const SizedBox(width: 4),
                        Expanded(child: Text('${hazard.reason} (${hazard.source})', style: AppText.caption.copyWith(color: AppColors.danger))),
                      ]),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: a == null
                    ? const SizedBox()
                    : Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        const Icon(Icons.info_outline, size: 14, color: AppColors.text3),
                        const SizedBox(width: 4),
                        Expanded(
                          child: Text(
                            [if (a.memo.isNotEmpty) a.memo, if (as?.indoor == true) '실내', if (a.travelMinutes != null) '${a.legMode == 'car' ? '차량' : '도보'} ${a.travelMinutes}분'].join(' · '),
                            style: AppText.caption,
                          ),
                        ),
                      ]),
              ),
            ]),
          ),
      ]),
    );
  }
}

enum _CellKind { same, removed, added }

class _Cell extends StatelessWidget {
  const _Cell({required this.item, required this.safety, required this.safetyFailed, required this.kind, required this.highlight, required this.onTap});
  final PlanItem item;
  final SafetyItem? safety;
  final bool safetyFailed;
  final _CellKind kind;
  final bool highlight;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final (bg, border) = switch (kind) {
      _CellKind.removed => (const Color(0xFFFFF8F8), AppColors.dangerLine),
      _CellKind.added => (const Color(0xFFFAF9FF), AppColors.aiLine),
      _ => (AppColors.surface, AppColors.line),
    };
    return Material(
      color: bg,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14), side: BorderSide(color: highlight ? AppColors.primary : border, width: highlight ? 2 : 1)),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(item.startAt ?? '--:--', style: AppText.caption.copyWith(color: AppColors.text2, fontWeight: FontWeight.w700)),
            const SizedBox(height: 2),
            Text(item.title, style: AppText.label.copyWith(fontWeight: FontWeight.w700), maxLines: 2, overflow: TextOverflow.ellipsis),
            const SizedBox(height: 8),
            if (safety != null)
              RiskBadge(item: safety!)
            else if (safetyFailed)
              const RiskChip(risk: null, label: '확인 못 함', unassessed: true),
          ]),
        ),
      ),
    );
  }
}

class _Gone extends StatelessWidget {
  const _Gone();

  @override
  Widget build(BuildContext context) => CustomPaint(
        painter: _DashedBorder(),
        child: Center(child: Padding(padding: const EdgeInsets.all(12), child: Text('빠짐', style: AppText.label.copyWith(color: AppColors.text3)))),
      );
}

class _DashedBorder extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = AppColors.text3
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;
    final path = Path()..addRRect(RRect.fromRectAndRadius(Offset.zero & size, const Radius.circular(14)));
    for (final m in path.computeMetrics()) {
      for (var d = 0.0; d < m.length; d += 10) {
        canvas.drawPath(m.extractPath(d, d + 5), paint);
      }
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class _BottomBar extends StatelessWidget {
  const _BottomBar({required this.entry, required this.onAccept, required this.onReject});
  final ProposalEntry entry;
  final VoidCallback onAccept;
  final VoidCallback onReject;

  @override
  Widget build(BuildContext context) {
    final chat = context.watch<ChatController>();
    final state = entry.state;
    if (state != ProposalState.pending && state != ProposalState.applying) {
      final (text, color) = switch (state) {
        ProposalState.applied => ('승인함 · 일정에 반영했어요', AppColors.success),
        ProposalState.rejected => ('거절함 · 일정은 그대로예요', AppColors.text2),
        _ => ('새 제안으로 대체됨', AppColors.text2),
      };
      return SizedBox(height: 52, child: Center(child: Text(text, style: AppText.label.copyWith(color: color, fontWeight: FontWeight.w700))));
    }
    return Row(children: [
      SizedBox(width: 110, child: AppButton(label: '거절', kind: ButtonKind.secondary, onPressed: chat.busy || state == ProposalState.applying ? null : onReject)),
      const SizedBox(width: 8),
      Expanded(child: AppButton(label: '전체 적용', icon: Icons.check, loading: state == ProposalState.applying, onPressed: chat.busy ? null : onAccept)),
    ]);
  }
}
