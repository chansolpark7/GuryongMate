import 'package:flutter/material.dart';

import '../../core/api/api_client.dart';
import '../../core/format.dart';
import '../../core/models/plan.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import 'location.dart';
import 'trip_controller.dart';

/// 11 Trip Start (Figma 73:1486) — 계획 모드 → 동행 모드로 넘어가는 유일한 문. 자동으로 시작하지 않는다 (trip §1.2).
/// 결과: 'started' | 'not_trip_day' (+detail) | null(나중에).
class TripStartResult {
  TripStartResult(this.kind, [this.detail = const {}]);
  final String kind;
  final Map<String, dynamic> detail;
}

Future<TripStartResult?> showTripStartSheet(BuildContext context, TripController t) => showModalBottomSheet<TripStartResult>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      barrierColor: const Color(0x59111827),
      builder: (_) => _TripStartSheet(t: t),
    );

class _TripStartSheet extends StatefulWidget {
  const _TripStartSheet({required this.t});
  final TripController t;

  @override
  State<_TripStartSheet> createState() => _TripStartSheetState();
}

class _TripStartSheetState extends State<_TripStartSheet> {
  bool _starting = false;
  String? _error;
  int _unprepared = 0;

  @override
  void initState() {
    super.initState();
    _countNotes();
  }

  /// 가이드 노트가 없는 장소 수 → "아직 해설이 준비 안 된 n곳은 지금 준비할게요" (서버가 시작 때 준비, F3).
  Future<void> _countNotes() async {
    final t = widget.t;
    final items = t.today?.items.where((i) => !i.skipped).toList() ?? const <PlanItem>[];
    try {
      final notes = await t.api.guideNotes(t.planId);
      final ok = notes.where((n) => n.status == 'ready' || n.status == 'stale').map((n) => n.placeId).toSet();
      final n = items.where((i) => !ok.contains(i.placeId)).map((i) => i.placeId).toSet().length;
      if (mounted) setState(() => _unprepared = n);
    } catch (_) {}
  }

  Future<void> _start() async {
    final t = widget.t;
    setState(() {
      _starting = true;
      _error = null;
    });
    // 위치 권한이 없고 데모 위치 모드가 아니면 13 먼저 (허용/거부 뒤 시작은 계속)
    if (!t.demoMode) {
      final access = await t.checkAccess();
      if (access != LocationAccess.granted && mounted) {
        await Navigator.of(context).push(MaterialPageRoute<void>(fullscreenDialog: true, builder: (_) => LocationPermissionScreen(t: t)));
      }
    }
    try {
      final sid = await _sessionId(t);
      await t.start(sessionId: sid);
      if (mounted) Navigator.of(context).pop(TripStartResult('started'));
    } on ApiException catch (e) {
      if (!mounted) return;
      if (e.code == 'not_trip_day') {
        Navigator.of(context).pop(TripStartResult('not_trip_day', e.detail));
        return;
      }
      setState(() {
        _starting = false;
        _error = '시작하지 못했어요. 다시 시도해 주세요';
      });
    }
  }

  /// 선제 턴을 보낼 세션: 이 계획의 가장 최근 세션. 없으면 생략 (서버가 만든다).
  static Future<String?> _sessionId(TripController t) async {
    try {
      final list = await t.api.sessions(t.planId);
      return list.isEmpty ? null : list.first.id;
    } catch (_) {
      return null;
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.t.plan.plan;
    final day = widget.t.today;
    final count = day?.items.where((i) => !i.skipped).length ?? p?.itemCount ?? 0;
    final date = day?.date ?? p?.startDate;
    final dayNo = widget.t.state?.day ?? day?.day;
    Widget line(IconData icon, String text) => Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Row(children: [
            Icon(icon, size: 20, color: AppColors.primary),
            const SizedBox(width: 10),
            Expanded(child: Text(text, style: AppText.label.copyWith(color: AppColors.text))),
          ]),
        );
    return Container(
      decoration: const BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            const Grabber(),
            const SizedBox(height: 12),
            Align(
              alignment: Alignment.centerLeft,
              child: Container(
                width: 48,
                height: 48,
                decoration: const BoxDecoration(color: AppColors.primarySoft, shape: BoxShape.circle),
                child: const Icon(Icons.near_me_outlined, color: AppColors.primary),
              ),
            ),
            const SizedBox(height: 16),
            Text('오늘 여행 시작할까요?', style: AppText.title.copyWith(fontSize: 20, height: 1.4)),
            if (p != null && p.numDays > 1 && dayNo != null) Text('$dayNo일차', style: AppText.meta.copyWith(color: AppColors.primary, fontWeight: FontWeight.w700)),
            const SizedBox(height: 4),
            Text(
              [
                p?.title ?? '',
                if (date != null) '${date.month}/${two(date.day)} (${weekdayKo(date)}) ${p?.dayStart ?? ''} – ${p?.dayEnd ?? ''}',
                '$count곳',
              ].join(' · '),
              style: AppText.label.copyWith(color: AppColors.text2),
            ),
            const SizedBox(height: 20),
            line(Icons.location_on_outlined, '장소에 도착하면 준비해 둔 해설을 들려드려요'),
            line(Icons.schedule, '늦어져서 일정에 문제가 생기면 먼저 알려드려요'),
            line(Icons.shield_outlined, '위험 구역 가까이 가면 바로 알려드려요'),
            const SizedBox(height: 4),
            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              const Padding(padding: EdgeInsets.only(top: 1), child: Icon(Icons.info_outline, size: 14, color: AppColors.text3)),
              const SizedBox(width: 6),
              Expanded(
                child: Text('위치는 앱을 쓰는 동안만 보내고, 마지막 위치만 저장해요. 이동 기록은 남기지 않아요. 시작해도 일정은 그대로이고 언제든 끝낼 수 있어요.',
                    style: AppText.caption),
              ),
            ]),
            const SizedBox(height: 16),
            if (_unprepared > 0)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Text('아직 해설이 준비 안 된 $_unprepared곳은 지금 준비할게요', style: AppText.caption.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700)),
              ),
            if (_error != null)
              Padding(padding: const EdgeInsets.only(bottom: 8), child: Text(_error!, style: AppText.caption.copyWith(color: AppColors.danger, fontWeight: FontWeight.w700))),
            Row(children: [
              SizedBox(width: 110, child: AppButton(label: '나중에', kind: ButtonKind.secondary, onPressed: _starting ? null : () => Navigator.of(context).pop())),
              const SizedBox(width: 8),
              Expanded(child: AppButton(label: '여행 시작', icon: Icons.near_me_outlined, loading: _starting, onPressed: _start)),
            ]),
          ]),
        ),
      ),
    );
  }
}

/// 12 Not Trip Day (Figma 73:1751): 409 not_trip_day → 날짜 옮기기도 AI 제안 → 승인으로.
/// true = [오늘로 옮기기].
Future<bool> showNotTripDayDialog(BuildContext context, {required String title, required Map<String, dynamic> detail, required DateTime today}) async {
  final start = DateTime.tryParse('${detail['start_date'] ?? ''}');
  final end = DateTime.tryParse('${detail['end_date'] ?? ''}');
  final past = end != null && dateOnly(today).isAfter(end);
  String d(DateTime x) => '${x.month}/${two(x.day)}';
  final range = start == null ? '' : (end == null || end == start ? d(start) : '${d(start)}~${d(end)}');
  final multi = end != null && start != null && end != start;
  final r = await showDialog<bool>(
    context: context,
    barrierColor: const Color(0x59111827),
    builder: (ctx) => Dialog(
      insetPadding: const EdgeInsets.symmetric(horizontal: 32),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(past ? '지난 여행이에요' : '오늘은 여행일이 아니에요', style: AppText.headline),
          const SizedBox(height: 8),
          Text.rich(
            TextSpan(style: AppText.body.copyWith(color: AppColors.text2), children: [
              TextSpan(text: '‘$title’ 일정은 '),
              TextSpan(text: range, style: const TextStyle(fontWeight: FontWeight.w700, color: AppColors.text)),
              TextSpan(text: past ? '였어요.' : (multi ? '예요. 시작일을 오늘(${d(today)})로 옮길까요?' : '예요. 일정을 오늘(${d(today)})로 옮길까요?')),
            ]),
          ),
          if (!past) ...[
            const SizedBox(height: 14),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(color: AppColors.aiSoft, borderRadius: BorderRadius.circular(12)),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Icon(Icons.auto_awesome, size: 14, color: AppColors.ai),
                const SizedBox(width: 6),
                Expanded(child: Text('옮기면 구룡메이트가 날짜 변경안을 만들어요. 승인해야 바뀌어요.', style: AppText.caption.copyWith(color: AppColors.ai))),
              ]),
            ),
            const SizedBox(height: 16),
            AppButton(label: '오늘로 옮기기', onPressed: () => Navigator.pop(ctx, true)),
            const SizedBox(height: 8),
            AppButton(label: '일정 둘러보기만 할게요', kind: ButtonKind.secondary, onPressed: () => Navigator.pop(ctx, false)),
          ] else ...[
            const SizedBox(height: 16),
            AppButton(label: '닫기', kind: ButtonKind.secondary, onPressed: () => Navigator.pop(ctx, false)),
          ],
        ]),
      ),
    ),
  );
  return r ?? false;
}

/// 13 Location Permission (Figma 73:1969): OS 권한 창 전에 왜 필요한지. 거부해도 여행은 시작된다 (→ 14-S1).
class LocationPermissionScreen extends StatelessWidget {
  const LocationPermissionScreen({super.key, required this.t});
  final TripController t;

  @override
  Widget build(BuildContext context) {
    Widget row(IconData icon, String title, String sub) => Padding(
          padding: const EdgeInsets.only(bottom: 20),
          child: Row(children: [
            Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(color: AppColors.primarySoft, borderRadius: BorderRadius.circular(12)),
              child: Icon(icon, size: 20, color: AppColors.primary),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(title, style: AppText.label.copyWith(fontSize: 15, fontWeight: FontWeight.w700, color: AppColors.text)),
                Text(sub, style: AppText.meta),
              ]),
            ),
          ]),
        );
    return Scaffold(
      backgroundColor: AppColors.surface,
      appBar: AppBar(
        backgroundColor: AppColors.surface,
        automaticallyImplyLeading: false,
        leading: IconButton(icon: const Icon(Icons.close), tooltip: '닫기', onPressed: () => Navigator.of(context).pop()),
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 0, 24, 16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            const SizedBox(height: 40),
            Center(
              child: Container(
                width: 120,
                height: 120,
                decoration: const BoxDecoration(color: AppColors.primarySoft, shape: BoxShape.circle),
                child: Center(
                  child: Container(
                    width: 60,
                    height: 60,
                    decoration: const BoxDecoration(color: AppColors.surface, shape: BoxShape.circle),
                    child: const Icon(Icons.location_on_outlined, size: 36, color: AppColors.primary),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 28),
            Text('위치를 켜면 현장에서 함께해요', style: AppText.title, textAlign: TextAlign.center),
            const SizedBox(height: 6),
            Text('앱을 쓰는 동안에만 위치를 사용해요', style: AppText.body.copyWith(color: AppColors.text2), textAlign: TextAlign.center),
            const SizedBox(height: 36),
            row(Icons.location_on_outlined, '도착 알림과 현장 해설', '장소에 도착하면 이야기를 들려드려요'),
            row(Icons.schedule, '늦어질 때 먼저 대안 제안', '지금 위치로 남은 일정을 다시 계산해요'),
            row(Icons.shield_outlined, '위험 구역 접근 경고', '침수 감시 구역·방파제 근처에서 바로 알려요'),
            const Spacer(),
            Text('마지막 위치만 저장하고 이동 기록은 남기지 않아요.\n백그라운드 위치는 쓰지 않아요.', style: AppText.caption, textAlign: TextAlign.center),
            const SizedBox(height: 20),
            AppButton(
              label: '위치 허용',
              onPressed: () async {
                await t.requestAccess();
                if (context.mounted) Navigator.of(context).pop();
              },
            ),
            const SizedBox(height: 8),
            TextButton(onPressed: () => Navigator.of(context).pop(), child: Text('위치 없이 계속', style: AppText.label.copyWith(color: AppColors.text))),
          ]),
        ),
      ),
    );
  }
}

/// 여행 종료 확인 (States_Feedback 다이얼로그). true = [마치기].
Future<bool> confirmEndTrip(BuildContext context) async =>
    await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('여행을 마칠까요?'),
        content: const Text('마쳐도 일정과 기록은 그대로 남아요.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('계속 여행')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('마치기')),
        ],
      ),
    ) ??
    false;
