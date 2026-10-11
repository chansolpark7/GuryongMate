import 'package:flutter/material.dart';

import '../../core/models/plan.dart';
import '../../core/models/trip.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/rich_answer.dart';
import '../../core/widgets/common.dart';
import 'trip_controller.dart';
import 'trip_overlay.dart';

/// 24 Event Inbox (Figma 73:3974): 지나간 알림을 다시 보고 처리 결과를 본다. level 0·suppressed 는 숨김.
class EventInboxScreen extends StatefulWidget {
  const EventInboxScreen({super.key, required this.t});
  final TripController t;

  @override
  State<EventInboxScreen> createState() => _EventInboxScreenState();
}

class _EventInboxScreenState extends State<EventInboxScreen> {
  List<TripEvent>? _events;
  final _proposals = <int, ProposalDetail>{};
  late final Set<int> _fresh; // 들어올 때 seen 전이었던 것 → "새 알림"
  Object? _error;

  TripController get t => widget.t;

  @override
  void initState() {
    super.initState();
    t.pushed(true);
    _load();
  }

  @override
  void dispose() {
    t.pushed(false);
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final list = await t.api.tripEvents(t.planId, after: 0, limit: 50);
      final shown = list.where((e) => e.level >= 1 && !e.suppressed).toList().reversed.toList();
      _fresh = {for (final e in shown) if (e.seenAt == null) e.id};
      for (final e in shown.where((e) => e.proactive && e.agent?.proposalId != null)) {
        try {
          _proposals[e.id] = await t.api.proposal(t.planId, e.agent!.proposalId!);
        } catch (_) {}
      }
      if (mounted) setState(() => _events = shown);
      for (final e in shown) {
        t.markSeen(e); // 화면에 보인 행은 확인 표시
      }
    } catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  void _open(TripEvent e) {
    switch (e.type) {
      case 'delay' || 'safety_raised':
        Navigator.of(context).pop();
        t.openProactive(e);
      case 'arrived':
        final pid = e.card?.placeId ?? e.payload['place_id'] as String?;
        if (pid != null) {
          Navigator.of(context).pop();
          t.onOpenGuide?.call(pid);
        }
      case 'near_hazard':
        Navigator.of(context).pop();
        t.onShowDetour?.call(e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final list = _events;
    final fresh = list?.where((e) => _fresh.contains(e.id)).toList() ?? const <TripEvent>[];
    final old = list?.where((e) => !_fresh.contains(e.id)).toList() ?? const <TripEvent>[];
    return Scaffold(
      appBar: AppBar(
        centerTitle: true,
        title: Column(children: [Text('알림', style: AppText.headline), Text('오늘 · ${t.plan.plan?.title ?? ''}', style: AppText.caption)]),
      ),
      body: _error != null
          ? StateView(title: '알림을 불러오지 못했어요', message: userMessage(_error!), actionLabel: '다시 시도', onAction: () {
              setState(() => _error = null);
              _load();
            })
          : list == null
              ? const Center(child: CircularProgressIndicator())
              : list.isEmpty
                  ? const StateView(icon: Icons.notifications_none, title: '아직 알림이 없어요', message: '도착하거나 일정에 변화가 생기면 여기에 모아요.')
                  : ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 24), children: [
                      if (fresh.isNotEmpty) ...[_Group('새 알림'), for (final e in fresh) _Row(event: e, isNew: true, proposal: _proposals[e.id], heard: t.heard, onTap: () => _open(e))],
                      if (old.isNotEmpty) ...[_Group('오늘'), for (final e in old) _Row(event: e, isNew: false, proposal: _proposals[e.id], heard: t.heard, onTap: () => _open(e))],
                    ]),
    );
  }
}

class _Group extends StatelessWidget {
  const _Group(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Padding(padding: const EdgeInsets.fromLTRB(4, 12, 0, 8), child: Text(text, style: AppText.caption.copyWith(fontWeight: FontWeight.w700)));
}

class _Row extends StatelessWidget {
  const _Row({required this.event, required this.isNew, required this.proposal, required this.heard, required this.onTap});
  final TripEvent event;
  final bool isNew;
  final ProposalDetail? proposal;
  final Map<String, String> heard;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final e = event;
    final (icon, fg, bg) = switch (e.type) {
      'safety_raised' => (Icons.shield_outlined, AppColors.danger, AppColors.dangerSoft),
      'delay' => (Icons.schedule, AppColors.warning, AppColors.warningSoft),
      'near_hazard' => (Icons.warning_amber_rounded, AppColors.danger, AppColors.dangerSoft),
      'arrived' => (Icons.location_on_outlined, AppColors.primary, AppColors.primarySoft),
      'safety_eased' => (Icons.verified_user_outlined, AppColors.success, AppColors.successSoft),
      _ => (Icons.directions_walk, AppColors.text2, AppColors.field),
    };
    final placeId = e.card?.placeId ?? e.payload['place_id'] as String?;
    final heardLen = placeId == null ? null : heard[placeId];
    final status = switch (e.type) {
      'arrived' => heardLen == null ? (e.card != null ? '가이드 준비됨' : '') : '해설 들음 · ${heardLen == 'long' ? '자세히' : '짧게'}',
      'near_hazard' => e.zones.isEmpty ? '' : shortZoneName(e.zones.first.name),
      'delay' || 'safety_raised' when e.failed => '구룡메이트에 전하지 못했어요',
      'delay' || 'safety_raised' when !e.agentReady => '준비 중',
      _ => '',
    };
    Widget? action = switch (e.type) {
      'delay' || 'safety_raised' when proposal != null => switch (proposal!.status) {
          'pending' => _Chip('제안 보기', AppColors.ai, AppColors.aiSoft, Icons.auto_awesome),
          'accepted' => _Chip('승인함', AppColors.success, AppColors.successSoft, Icons.check),
          'rejected' => _Chip('거절함', AppColors.text2, AppColors.field, null),
          _ => _Chip('새 제안으로 바뀜', AppColors.text2, AppColors.field, null),
        },
      'near_hazard' => _Chip('지도', AppColors.danger, AppColors.dangerSoft, Icons.map_outlined),
      'arrived' when heardLen == null => _Chip('해설 듣기', AppColors.ai, AppColors.aiSoft, Icons.auto_awesome),
      _ => null,
    };
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: AppCard(
        padding: const EdgeInsets.fromLTRB(8, 14, 12, 14),
        onTap: onTap,
        child: Row(children: [
          SizedBox(width: 10, child: isNew ? Center(child: Container(width: 6, height: 6, decoration: const BoxDecoration(color: AppColors.primary, shape: BoxShape.circle))) : null),
          Container(width: 40, height: 40, decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(12)), child: Icon(icon, color: fg, size: 22)),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(plainSummary(e.message), style: AppText.label.copyWith(fontWeight: FontWeight.w700, color: AppColors.text), maxLines: 2, overflow: TextOverflow.ellipsis),
              Text([e.timeText, if (status.isNotEmpty) status].join(' · '), style: AppText.caption),
            ]),
          ),
          if (action != null) ...[const SizedBox(width: 8), action],
        ]),
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip(this.label, this.fg, this.bg, this.icon);
  final String label;
  final Color fg;
  final Color bg;
  final IconData? icon;

  @override
  Widget build(BuildContext context) => InfoChip(label: label, fg: fg, bg: bg, icon: icon);
}
