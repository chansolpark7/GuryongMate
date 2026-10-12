import 'package:flutter/material.dart';

import '../../core/format.dart';
import '../../core/models/safety.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../../core/widgets/safety_widgets.dart';
import '../planner/plan_controller.dart';

/// 27_Safety_Outlook (Figma 73:4751) — 그날 구룡포 날씨·바다를 장소 종류별 시간 띠로. Bottom Sheet (top 220).
Future<void> showOutlookSheet(BuildContext context, PlanController c, DateTime date) => showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) => OutlookSheet(
        date: date,
        load: () async {
          final r = await Future.wait([c.api.outlook(date), c.api.situation().then<Situation?>((s) => s).catchError((_) => null)]);
          return (r[0] as Outlook, r[1] as Situation?);
        },
        onAskAi: (text) {
          Navigator.pop(ctx);
          c.openAi(prefill: text, send: true);
        },
      ),
    );

const _kinds = [('breakwater', '방파제'), ('flood', '해안 저지대'), ('coast', '해안'), ('outdoor', '그 밖의 야외'), ('indoor', '실내')];
const _hours = [9, 10, 11, 12, 13, 14, 15, 16, 17, 18];

String kindName(String k) => switch (k) { 'breakwater' => '방파제', 'flood' => '해안 저지대', 'coast' => '해안', 'outdoor' => '야외', _ => '실내' };

Color cellColor(String? level, bool info) => switch (level) {
      'danger' => const Color(0xFFF6A6A9),
      'caution' => const Color(0xFFFCD9A3),
      'safe' => info ? const Color(0xFFE5E7EB) : const Color(0xFFCDEFD9),
      _ => AppColors.background,
    };

class OutlookSheet extends StatefulWidget {
  const OutlookSheet({super.key, required this.date, required this.load, required this.onAskAi});
  final DateTime date;
  final Future<(Outlook, Situation?)> Function() load;
  final ValueChanged<String> onAskAi;

  @override
  State<OutlookSheet> createState() => _OutlookSheetState();
}

class _OutlookSheetState extends State<OutlookSheet> {
  late Future<(Outlook, Situation?)> _future = widget.load();

  @override
  Widget build(BuildContext context) {
    final d = widget.date;
    return Container(
      constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.76),
      decoration: const BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      child: SafeArea(
        top: false,
        child: FutureBuilder<(Outlook, Situation?)>(
          future: _future,
          builder: (context, snap) {
            final header = [
              const Grabber(),
              Text('${d.month}/${two(d.day)} (${weekdayKo(d)}) 구룡포 전망', style: AppText.title.copyWith(fontSize: 20, height: 28 / 20)),
            ];
            if (snap.connectionState != ConnectionState.done) {
              return ListView(shrinkWrap: true, padding: const EdgeInsets.fromLTRB(20, 0, 20, 24), children: [
                ...header,
                const Padding(padding: EdgeInsets.all(32), child: Center(child: CircularProgressIndicator())),
              ]);
            }
            if (snap.hasError) {
              return ListView(shrinkWrap: true, padding: const EdgeInsets.fromLTRB(20, 0, 20, 24), children: [
                ...header,
                StateView(
                  icon: Icons.dns_outlined,
                  title: '전망을 불러오지 못했어요',
                  message: '잠시 후 다시 시도해 주세요. 일정은 그대로예요.',
                  actionLabel: '다시 시도',
                  onAction: () => setState(() => _future = widget.load()),
                ),
              ]);
            }
            final (o, s) = snap.data!;
            return ListView(shrinkWrap: true, padding: const EdgeInsets.fromLTRB(20, 0, 20, 24), children: [...header, ..._body(o, s)]);
          },
        ),
      ),
    );
  }

  List<Widget> _body(Outlook o, Situation? s) {
    final d = widget.date;
    final hours = (s?.hours ?? const <SituationHour>[]).where((h) {
      final t = kst(h.time);
      return t.year == d.year && t.month == d.month && t.day == d.day && t.hour >= 9 && t.hour <= 18;
    }).toList();
    final byHour = {for (final h in hours) kst(h.time).hour: h};
    final far = hours.isEmpty && o.safeWindows.isEmpty;
    final highs = o.tides.where((t) => t.kind == 'high').map((t) => hhmm(t.time)).toList();
    return [
      const SizedBox(height: 4),
      Text(_summary(o, hours, far), style: AppText.meta.copyWith(fontSize: 14, color: AppColors.text)),
      const SizedBox(height: 16),
      if (far)
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(color: AppColors.field, borderRadius: BorderRadius.circular(12)),
          child: Text(
            '아직 예보 전이에요${o.recheckFrom == null ? '' : ' · ${two(o.recheckFrom!.month)}/${two(o.recheckFrom!.day)}부터 확인할 수 있어요'}',
            style: AppText.label.copyWith(color: AppColors.text2),
          ),
        )
      else ...[
        _Grid(byHour: byHour),
        const SizedBox(height: 10),
        const _Legend(),
      ],
      if (o.safeWindows.isNotEmpty) ...[
        const SizedBox(height: 18),
        Text('야외에 괜찮은 시간', style: AppText.label.copyWith(color: AppColors.text2, fontWeight: FontWeight.w700)),
        const SizedBox(height: 8),
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final w in o.safeWindows)
            ActionChip(
              label: Text('${kindName(w.kind ?? '')} ${hhmm(w.start)}–${hhmm(w.end)}${w.hasInfo ? ' · 참고' : ''}',
                  style: AppText.caption.copyWith(color: AppColors.success, fontWeight: FontWeight.w700)),
              backgroundColor: AppColors.successSoft,
              side: BorderSide.none,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
              onPressed: () => widget.onAskAi('${kindName(w.kind ?? '')}는 ${hhmm(w.start)}–${hhmm(w.end)}에 가도록 조정해줘'),
            ),
        ]),
      ],
      const SizedBox(height: 16),
      _Row(icon: Icons.waves, title: highs.isEmpty ? '만조 정보 없음' : '만조 ${highs.join(' · ')}', sub: '조차가 작아 참고만 하세요'),
      const SizedBox(height: 8),
      _Row(icon: Icons.info_outline, title: o.warnings.isEmpty ? '발효 중인 특보 없음' : o.warnings.join('\n')),
      for (final n in far ? o.seasonalNotes : const <String>[]) ...[const SizedBox(height: 8), _Row(icon: Icons.eco_outlined, title: n)],
      const SizedBox(height: 14),
      Text('기상청 단기예보 · 국립해양조사원${s?.now == null ? '' : ' · ${kst(s!.now!).month}/${two(kst(s.now!).day)} ${hhmm(s.now!)} 기준'}', style: AppText.caption),
    ];
  }

  /// 요약 한 줄. 예보는 단정하지 않는다 (README §6.2).
  String _summary(Outlook o, List<SituationHour> hours, bool far) {
    if (far) return '날씨·바다 판단은 예보가 나오면 해요.';
    final parts = <String>[];
    for (final (k, name) in _kinds.take(3)) {
      int? first(String level) => hours.where((h) => h.levels[k] == level).map((h) => kst(h.time).hour).firstOrNull;
      final c = first('caution'), d = first('danger');
      if (c == null && d == null) continue;
      final seg = [if (c != null && (d == null || c < d)) '$c시부터 주의', if (d != null) '$d시부터 위험'];
      parts.add('$name ${seg.join(', ')}');
    }
    final m = o.summary;
    final num = [
      if (m?['max_wind_ms'] != null) '풍속 최대 ${m!['max_wind_ms']}m/s',
      if (m?['max_wave_m'] != null) '파고 최대 ${m!['max_wave_m']}m',
    ].join(' · ');
    if (parts.isEmpty) return '주의·위험 신호가 없어요${num.isEmpty ? '' : ' ($num 예보)'}. 예보는 바뀔 수 있어요.';
    return '${parts.join(', ')} 예보예요${num.isEmpty ? '' : ' ($num)'}.';
  }
}

class _Grid extends StatelessWidget {
  const _Grid({required this.byHour});
  final Map<int, SituationHour> byHour;

  @override
  Widget build(BuildContext context) => Column(children: [
        Row(children: [
          const SizedBox(width: 90),
          for (final h in _hours) Expanded(child: Text(two(h), textAlign: TextAlign.center, style: AppText.caption.copyWith(fontSize: 10))),
        ]),
        const SizedBox(height: 4),
        for (final (k, name) in _kinds)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 2),
            child: Row(children: [
              SizedBox(width: 90, child: Text(name, style: AppText.meta.copyWith(color: AppColors.text))),
              for (final h in _hours)
                Expanded(
                  child: Container(
                    key: ValueKey('cell-$k-$h'),
                    height: 24,
                    margin: const EdgeInsets.symmetric(horizontal: 1),
                    decoration: BoxDecoration(color: cellColor(byHour[h]?.levels[k], byHour[h]?.info.contains(k) ?? false), borderRadius: BorderRadius.circular(4)),
                  ),
                ),
            ]),
          ),
      ]);
}

class _Legend extends StatelessWidget {
  const _Legend();

  @override
  Widget build(BuildContext context) {
    Widget item(Color c, String t) => Row(mainAxisSize: MainAxisSize.min, children: [
          Container(width: 10, height: 10, decoration: BoxDecoration(color: c, borderRadius: BorderRadius.circular(3))),
          const SizedBox(width: 4),
          Text(t, style: AppText.caption),
          const SizedBox(width: 10),
        ]);
    return Row(children: [
      item(cellColor('safe', false), '안전'),
      item(cellColor('safe', true), '참고'),
      item(cellColor('caution', false), '주의'),
      item(cellColor('danger', false), '위험'),
    ]);
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.icon, required this.title, this.sub});
  final IconData icon;
  final String title;
  final String? sub;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(color: AppColors.background, borderRadius: BorderRadius.circular(12)),
        child: Row(children: [
          Icon(icon, size: 20, color: AppColors.text2),
          const SizedBox(width: 10),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
              if (sub != null) Text(sub!, style: AppText.caption),
            ]),
          ),
        ]),
      );
}
