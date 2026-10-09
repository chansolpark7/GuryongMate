import 'package:flutter/material.dart';

import '../../core/api/guryong_api.dart';
import '../../core/format.dart';
import '../../core/models/account.dart';
import '../../core/models/plan.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import 'trip_overlay.dart' show shortZoneName;

/// 30 Trip End (Figma 73:5458): 하루 요약. 요약 API 가 없어 앱이 계산한다 (gaps b8).
/// - 실제 동선·다녀온 곳: PlanDetail 그날 항목의 arrived_at·done_at
/// - 이동: done·visiting 항목의 travel_minutes 합 (수단별)
/// - 들은 해설: 앱 로컬 기록 (17 을 연 장소)
/// - 함께 바꾼 일정: accepted 제안 중 이번 여행일 이후 결정한 것
/// - 도운 순간: 이벤트 delay·safety_raised·near_hazard (+ 제안 결과)
/// - 기억한 것: 메모리 중 오늘 만든 것
class TripEndScreen extends StatefulWidget {
  const TripEndScreen({super.key, required this.api, required this.planId, required this.state, this.heard = const {}, this.onContinueChat});
  final GuryongApi api;
  final int planId;
  final TripState state;
  final Map<String, String> heard;
  final VoidCallback? onContinueChat;

  @override
  State<TripEndScreen> createState() => _TripEndScreenState();
}

class _Moment {
  _Moment(this.icon, this.color, this.text);
  final IconData icon;
  final Color color;
  final String text;
}

class _TripEndScreenState extends State<TripEndScreen> {
  PlanDetail? _plan;
  List<TripEvent> _events = const [];
  List<ProposalDetail> _accepted = const [];
  List<MemoryItem> _memory = const [];
  final _proposalById = <int, ProposalDetail>{};
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final api = widget.api;
    try {
      _plan = await api.plan(widget.planId);
    } catch (_) {}
    try {
      _events = await api.tripEvents(widget.planId, after: 0, limit: 200);
    } catch (_) {}
    try {
      final all = await api.proposals(widget.planId, status: 'accepted');
      // decided_at 은 실제 시각이다 (가상 시각과 다를 수 있다): 오늘 결정한 것
      final now = DateTime.now();
      _accepted = all.where((p) => p.decidedAt != null && now.difference(p.decidedAt!) < const Duration(hours: 18)).toList();
      for (final p in all) {
        _proposalById[p.id] = p;
      }
    } catch (_) {}
    try {
      final mem = await api.memory(planId: widget.planId);
      final today = DateTime.now();
      _memory = mem.items.where((m) => m.createdAt.year == today.year && m.createdAt.month == today.month && m.createdAt.day == today.day).toList();
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    final p = _plan;
    final day = p?.dayOf(widget.state.day ?? 1);
    final visited = (day?.items ?? const <PlanItem>[]).where((i) => !i.skipped && (i.status == 'done' || i.status == 'visiting') && i.arrivedAt != null).toList();
    final firstAt = visited.map((i) => i.arrivedAt!).firstOrNull;
    final lastAt = visited.map((i) => i.doneAt ?? i.arrivedAt!).lastOrNull;
    final walk = visited.where((i) => i.legMode != 'car').fold<int>(0, (s, i) => s + (i == visited.first ? 0 : i.travelMinutes ?? 0));
    final car = visited.where((i) => i.legMode == 'car').fold<int>(0, (s, i) => s + (i.travelMinutes ?? 0));
    final changedIds = <int>{};
    for (final pr in _accepted) {
      for (final ch in pr.changes) {
        if (ch.kind == 'added' && ch.itemId != null) changedIds.add(ch.itemId!);
        if (ch.kind == 'added') {
          final t = _placeName(ch.message);
          for (final i in visited.where((i) => i.title == t)) {
            changedIds.add(i.id);
          }
        }
      }
    }
    // 위험 구역 알림은 한 줄로 묶는다 ("16:33 침수 감시 구역 알림 · 구룡포수협 외 2곳")
    final hazards = _events.where((e) => !e.suppressed && e.type == 'near_hazard').toList();
    final moments = <_Moment>[
      for (final e in _events.where((e) => !e.suppressed))
        if (e.type == 'delay')
          _Moment(Icons.schedule, AppColors.warning, '${e.timeText} 늦어짐${_result(e)}')
        else if (e.type == 'safety_raised')
          _Moment(Icons.shield_outlined, AppColors.danger, '${e.timeText} 안전 신호${_result(e)}'),
      if (hazards.isNotEmpty)
        _Moment(Icons.warning_amber_rounded, AppColors.danger, [
          '${hazards.first.timeText} ${hazards.first.message.contains('방파제') ? '방파제' : '침수 감시 구역'} 알림',
          if (hazards.first.zones.isNotEmpty) shortZoneName(hazards.first.zones.first.name) + (hazards.length > 1 ? ' 외 ${hazards.length - 1}곳' : ''),
        ].join(' · ')),
    ];
    final date = day?.date ?? p?.startDate;
    final empty = visited.isEmpty;
    return Scaffold(
      backgroundColor: AppColors.surface,
      appBar: AppBar(
        backgroundColor: AppColors.surface,
        automaticallyImplyLeading: false,
        leading: IconButton(icon: const Icon(Icons.close), tooltip: '닫기', onPressed: () => Navigator.of(context).pop()),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(padding: const EdgeInsets.fromLTRB(20, 0, 20, 24), children: [
              Text('오늘 여행을 마쳤어요', style: AppText.title),
              const SizedBox(height: 4),
              Text(
                [
                  p?.title ?? '',
                  if (date != null) '${date.month}/${two(date.day)} (${weekdayKo(date)})${firstAt == null ? '' : ' $firstAt – ${lastAt ?? ''}'}',
                ].join(' · '),
                style: AppText.meta,
              ),
              const SizedBox(height: 16),
              if (empty)
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(color: AppColors.background, borderRadius: BorderRadius.circular(16)),
                  child: Text('기록된 방문이 없어요. 일정은 그대로예요.', style: AppText.body),
                )
              else ...[
                GridView.count(
                  crossAxisCount: 2,
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  mainAxisSpacing: 8,
                  crossAxisSpacing: 8,
                  childAspectRatio: 171 / 72,
                  children: [
                    _Metric('${visited.length}곳', '다녀온 곳'),
                    _Metric('${walk + car}분', car > 0 && walk > 0 ? '이동 (도보 $walk · 차량 $car)' : (car > 0 ? '이동 (차량)' : '이동 (도보)')),
                    _Metric('${widget.heard.length}번', '들은 해설'),
                    _Metric('${_accepted.length}번', '함께 바꾼 일정'),
                  ],
                ),
                const SizedBox(height: 20),
                Text('실제 동선', style: AppText.caption.copyWith(fontWeight: FontWeight.w700)),
                const SizedBox(height: 6),
                for (final i in visited)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 7),
                    child: Row(children: [
                      const Icon(Icons.check, size: 18, color: AppColors.success),
                      const SizedBox(width: 10),
                      SizedBox(width: 100, child: Text('${i.arrivedAt} – ${i.doneAt ?? ''}', style: AppText.meta.copyWith(fontWeight: FontWeight.w700, color: AppColors.text3))),
                      Expanded(child: Text(i.title, style: AppText.label.copyWith(fontWeight: FontWeight.w700, color: AppColors.text), overflow: TextOverflow.ellipsis)),
                      Text(_memo(i, changedIds), style: AppText.caption),
                    ]),
                  ),
                if (moments.isNotEmpty) ...[
                  const SizedBox(height: 16),
                  Text('구룡메이트가 도운 순간', style: AppText.caption.copyWith(fontWeight: FontWeight.w700)),
                  const SizedBox(height: 6),
                  for (final m in moments)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 5),
                      child: Row(children: [
                        Icon(m.icon, size: 18, color: m.color),
                        const SizedBox(width: 10),
                        Expanded(child: Text(m.text, style: AppText.label.copyWith(color: AppColors.text))),
                      ]),
                    ),
                ],
                for (final m in _memory)
                  Container(
                    margin: const EdgeInsets.only(top: 16),
                    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                    decoration: BoxDecoration(color: AppColors.aiSoft, borderRadius: BorderRadius.circular(12)),
                    child: Row(children: [
                      const Icon(Icons.bookmark_border, size: 18, color: AppColors.ai),
                      const SizedBox(width: 8),
                      Expanded(child: Text('기억한 것 · ${m.text}', style: AppText.label.copyWith(color: AppColors.ai))),
                    ]),
                  ),
              ],
            ]),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 12),
          child: Row(children: [
            if (!empty && widget.onContinueChat != null) ...[
              Expanded(
                child: AppButton(
                  label: '대화 이어가기',
                  kind: ButtonKind.secondary,
                  onPressed: () {
                    Navigator.of(context).pop();
                    widget.onContinueChat!();
                  },
                ),
              ),
              const SizedBox(width: 8),
            ],
            Expanded(child: AppButton(label: '계획 목록으로', onPressed: () => Navigator.of(context).popUntil((r) => r.isFirst))),
          ]),
        ),
      ),
    );
  }

  static String _placeName(String m) {
    final q = RegExp(r"'([^']+)'").firstMatch(m);
    if (q != null) return q.group(1)!;
    return m.contains(':') ? m.substring(m.indexOf(':') + 1).trim() : m;
  }

  /// 제안 결과 한 줄: 교체 "북방파제 → 근대문화역사관", 빼기 "과메기문화관 빼기", 그 밖 "HH:MM 종료".
  String _result(TripEvent e) {
    final p = _proposalById[e.agent?.proposalId ?? -1];
    if (p == null || p.status != 'accepted') return '';
    // "삭제: 1일차 '구룡포 북방파제'" · "추가: 1일차 4번째 '구룡포 근대문화역사관' (40분)" → 장소 이름만
    String strip(String m) => _placeName(m);
    final removed = p.changes.where((c) => c.kind == 'removed').map((c) => strip(c.message)).toList();
    final added = p.changes.where((c) => c.kind == 'added').map((c) => strip(c.message)).toList();
    final end = RegExp(r'→\s*([0-9:]+)').firstMatch(p.changes.where((c) => c.kind == 'day_end').map((c) => c.message).firstOrNull ?? '')?.group(1);
    if (removed.isNotEmpty && added.isNotEmpty) return ' → ${removed.first} 대신 ${added.first}';
    if (removed.isNotEmpty) return ' → ${removed.first} 빼기${end == null ? '' : ', $end 종료'}';
    return end == null ? ' → 일정을 바꿈' : ' → $end 종료';
  }

  /// "+n분"(예정보다 오래 머묾) / "바꿈"(제안으로 들어온 곳).
  String _memo(PlanItem i, Set<int> changed) {
    if (changed.contains(i.id)) return '바꿈';
    final a = toMinutes(i.arrivedAt), d = toMinutes(i.doneAt);
    if (a == null || d == null) return '';
    final over = (d - a) - i.stayMinutes;
    return over >= 10 ? '+$over분' : '';
  }
}

class _Metric extends StatelessWidget {
  const _Metric(this.value, this.label);
  final String value;
  final String label;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(color: AppColors.background, borderRadius: BorderRadius.circular(16)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisAlignment: MainAxisAlignment.center, children: [
          Text(value, style: AppText.title.copyWith(fontSize: 22, height: 1.2)),
          Text(label, style: AppText.caption, maxLines: 1, overflow: TextOverflow.ellipsis),
        ]),
      );
}
