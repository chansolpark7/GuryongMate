import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/models/account.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import 'trip_controller.dart';

/// 지금 열려 있는 계획 화면의 동행 상태. 전역 오버레이(16·23·토스트·미리보기)는 Navigator 바깥에서 이것을 본다.
class TripHub extends ChangeNotifier {
  TripController? _controller;
  TripController? get controller => _controller;

  // 셸의 initState·dispose 에서 불리므로(빌드 중) 알림은 다음 마이크로태스크로 미룬다.
  void attach(TripController c) {
    _controller = c;
    scheduleMicrotask(notifyListeners);
  }

  void detach(TripController c) {
    if (_controller != c) return;
    _controller = null;
    scheduleMicrotask(notifyListeners);
  }
}

final tripHub = TripHub();

/// MaterialApp.builder 에 끼운다: Navigator(모든 화면·시트·다이얼로그) 위에 동행 오버레이를 그린다 (README §4 전역 오버레이).
Widget withTripOverlay(BuildContext context, Widget? child) => Stack(children: [
      ?child,
      const TripOverlayHost(),
    ]);

class TripOverlayHost extends StatefulWidget {
  const TripOverlayHost({super.key});

  @override
  State<TripOverlayHost> createState() => _TripOverlayHostState();
}

/// Navigator 바깥이라 Tooltip·InkWell 등이 쓸 Overlay 를 따로 둔다.
class _TripOverlayHostState extends State<TripOverlayHost> {
  late final _entry = OverlayEntry(builder: (_) => const _HostBody());

  @override
  Widget build(BuildContext context) => Overlay(initialEntries: [_entry]);
}

class _HostBody extends StatelessWidget {
  const _HostBody();

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: tripHub,
        builder: (context, _) {
          final c = tripHub.controller;
          if (c == null) return const SizedBox.shrink();
          return ListenableBuilder(listenable: c, builder: (context, _) => _Overlays(c: c));
        },
      );
}

class _Overlays extends StatelessWidget {
  const _Overlays({required this.c});
  final TripController c;

  @override
  Widget build(BuildContext context) {
    final mq = MediaQuery.of(context);
    final onShell = c.pushDepth == 0;
    final todayTab = c.plan.tab == 2;
    final navBottom = 60 + mq.padding.bottom + 12;
    // Today 는 지도 위 (Figma 16: y 270, 패널 바로 위), 다른 탭은 하단 탭 위
    final cardTop = todayTab && onShell ? mq.padding.top + 226 : null;
    final hazard = c.hazard, arrival = c.arrival, preview = c.preview, toast = c.toast;
    final showArrival = arrival != null && !c.sheetOpen && onShell;
    final showPreview = preview != null && !c.previewCollapsed && !c.sheetOpen;
    return Material(
      type: MaterialType.transparency,
      child: Stack(children: [
        // 토스트: Today 는 패널 바로 위 (Figma 15: y 372), 다른 탭은 하단 탭 위
        if (toast != null)
          todayTab && onShell
              ? Positioned(left: 16, right: 16, top: mq.padding.top + 328, child: EventToast(event: toast, c: c))
              : Positioned(left: 16, right: 16, bottom: navBottom + (showArrival && !c.arrivalCollapsed ? 180 : 0), child: EventToast(event: toast, c: c)),
        if (showArrival)
          Positioned(
            left: 16,
            right: c.arrivalCollapsed ? null : 16,
            top: cardTop,
            bottom: cardTop == null ? navBottom : null,
            child: c.arrivalCollapsed ? _ArrivalChip(onTap: c.expandArrival) : ArrivalCard(key: ValueKey('arrival-${arrival.id}'), event: arrival, c: c),
          ),
        if (showPreview)
          onShell
              ? Positioned(
                  right: 84,
                  top: todayTab ? mq.padding.top + 196 : null,
                  bottom: todayTab ? null : navBottom + 4,
                  child: ProactivePeek(event: preview, c: c),
                )
              : Positioned(left: 16, right: 16, top: mq.padding.top + 8, child: ProactivePeek(event: preview, c: c, banner: true)),
        // 접힌 띠는 셸에서만 (push 화면은 자기 내용이 우선, 알림 목록에도 남아 있다). 펼친 경고는 어디서든 최상단.
        if (hazard != null && (!c.hazardCollapsed || onShell))
          Positioned(
            left: 0,
            right: 0,
            top: 0,
            child: c.hazardCollapsed ? HazardStrip(event: hazard, onTap: c.expandHazard) : HazardAlertPanel(event: hazard, c: c),
          ),
      ]),
    );
  }
}

// ---------------------------------------------------------------------------
// 23 위험 구역 경고

/// "구룡포수협_스마트맨홀 주변" → "구룡포수협 주변"
String shortZoneName(String name) =>
    name.replaceAll('_스마트맨홀', '').replaceAll('_지표면 수위계', '').replaceAll('_지표면', '').replaceAll('_하천수위계', '').replaceAll('_', ' ');

/// "구룡포수협_스마트맨홀 수위 등급 3 (위험)" → "수위 센서 실측 · 위험 (등급 3)" (센서 코드·이름 노출 없이, README §6.2)
String hazardReasonText(String reason) {
  final m = RegExp(r'수위 등급\s*(\d)\s*\(([^)]+)\)').firstMatch(reason);
  if (m != null) return '수위 센서 실측 · ${m.group(2)} (등급 ${m.group(1)})';
  final cctv = RegExp(r'실족 감지').hasMatch(reason);
  if (cctv) return '방파제 CCTV 실족 감지';
  return reason.split('; ').first;
}

class HazardAlertPanel extends StatelessWidget {
  const HazardAlertPanel({super.key, required this.event, required this.c});
  final TripEvent event;
  final TripController c;

  @override
  Widget build(BuildContext context) {
    final z = event.zones.firstOrNull;
    final kind = z?.kind ?? (event.message.contains('방파제') ? 'breakwater' : 'flood');
    final title = kind == 'breakwater' ? '방파제 근처예요' : '침수 감시 구역 근처예요';
    final guide = event.message.contains('근처예요.') ? event.message.split('근처예요.').last.trim().replaceAll(RegExp(r'\.$'), '') : event.message;
    final body = z == null ? event.message : '${shortZoneName(z.name)} · $guide';
    final evidence = [if (z != null) hazardReasonText(z.reason), if (event.timeText.isNotEmpty) event.timeText].join(' · ');
    return Container(
      key: const ValueKey('hazard-panel'),
      padding: EdgeInsets.fromLTRB(20, MediaQuery.paddingOf(context).top + 12, 20, 20),
      decoration: const BoxDecoration(
        color: AppColors.danger,
        borderRadius: BorderRadius.vertical(bottom: Radius.circular(24)),
        boxShadow: [BoxShadow(color: Color(0x59E5484D), blurRadius: 24, offset: Offset(0, 8))],
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Container(
            width: 44,
            height: 44,
            decoration: const BoxDecoration(color: Color(0x33FFFFFF), shape: BoxShape.circle),
            child: const Icon(Icons.warning_amber_rounded, color: Colors.white, size: 28),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: AppText.title.copyWith(fontSize: 20, height: 1.3, color: Colors.white)),
              const SizedBox(height: 2),
              Text(body, style: AppText.body.copyWith(color: Colors.white)),
              if (evidence.isNotEmpty) Text(evidence, style: AppText.caption.copyWith(color: const Color(0xFFFFD7D8))),
            ]),
          ),
        ]),
        const SizedBox(height: 16),
        Row(children: [
          Expanded(
            flex: 245,
            child: SizedBox(
              height: 48,
              child: TextButton.icon(
                onPressed: () {
                  c.collapseHazard();
                  c.onShowDetour?.call(event);
                },
                icon: const Icon(Icons.alt_route, size: 18),
                label: const Text('피해 가는 길 보기'),
                style: TextButton.styleFrom(
                  backgroundColor: Colors.white,
                  foregroundColor: AppColors.danger,
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  textStyle: AppText.section,
                ),
              ),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            flex: 95,
            child: SizedBox(
              height: 48,
              child: TextButton(
                onPressed: c.collapseHazard,
                style: TextButton.styleFrom(
                  backgroundColor: const Color(0x33FFFFFF),
                  foregroundColor: Colors.white,
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  textStyle: AppText.section,
                ),
                child: const Text('확인'),
              ),
            ),
          ),
        ]),
      ]),
    );
  }
}

/// 확인 뒤: 32 높이 Red 띠. 구역을 벗어나면 사라진다.
class HazardStrip extends StatelessWidget {
  const HazardStrip({super.key, required this.event, required this.onTap});
  final TripEvent event;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final z = event.zones.firstOrNull;
    return Padding(
      // Pill·앱 바(52) 아래: 알림·뒤로 버튼을 가리지 않게
      padding: EdgeInsets.fromLTRB(16, MediaQuery.paddingOf(context).top + 64, 16, 0),
      child: Material(
        color: AppColors.danger,
        borderRadius: BorderRadius.circular(16),
        child: InkWell(
          key: const ValueKey('hazard-strip'),
          borderRadius: BorderRadius.circular(16),
          onTap: onTap,
          child: SizedBox(
            height: 32,
            child: Row(children: [
              const SizedBox(width: 12),
              const Icon(Icons.warning_amber_rounded, color: Colors.white, size: 16),
              const SizedBox(width: 6),
              Expanded(
                child: Text('위험 구역 근처${z == null ? '' : ' · ${shortZoneName(z.name)}'}',
                    style: AppText.caption.copyWith(color: Colors.white, fontWeight: FontWeight.w700), overflow: TextOverflow.ellipsis),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// 16 도착 해설 카드

class ArrivalCard extends StatefulWidget {
  const ArrivalCard({super.key, required this.event, required this.c, this.inChat = false});
  final TripEvent event;
  final TripController c;
  final bool inChat;

  @override
  State<ArrivalCard> createState() => _ArrivalCardState();
}

class _ArrivalCardState extends State<ArrivalCard> {
  String? _status; // 노트 status, 없으면 'missing'
  String? _style; // short | long | null
  bool _loading = true;

  String? get _placeId => widget.event.card?.placeId ?? widget.event.payload['place_id'] as String?;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final c = widget.c;
    final pid = _placeId;
    try {
      _style = (await c.api.memory(planId: c.planId)).styleLength;
    } catch (_) {}
    if (pid != null) {
      try {
        _status = (await c.api.guideNoteDetail(c.planId, pid)).status;
      } catch (_) {
        _status = 'missing'; // 404 = 노트 없음 → 16-S1, 조회 실패도 버튼은 그대로 (17 이 오류 처리)
      }
    }
    if (mounted) setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    final e = widget.event;
    final title = (e.payload['title'] as String?) ?? widget.c.item(e.card?.itemId)?.title ?? '';
    final ready = _status == 'ready' || _status == 'stale';
    final preparing = _status == 'pending' || _status == 'preparing';
    final caption = _loading ? '도착했어요' : (ready ? '도착했어요 · 가이드 준비됨' : (preparing ? '도착했어요 · 가이드를 준비하고 있어요' : '도착했어요 · 가이드 없음'));
    final shortFirst = _style == 'short';
    return Container(
      key: const ValueKey('arrival-card'),
      padding: const EdgeInsets.fromLTRB(16, 12, 8, 16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: AppColors.aiLine),
        boxShadow: const [BoxShadow(color: Color(0x2E6E59FF), blurRadius: 24, offset: Offset(0, 8))],
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
        Row(children: [
          const Icon(Icons.auto_awesome, size: 16, color: AppColors.ai),
          const SizedBox(width: 6),
          Expanded(child: Text(caption, style: AppText.caption.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700))),
          if (!widget.inChat)
            IconButton(
              tooltip: '닫기',
              visualDensity: VisualDensity.compact,
              icon: const Icon(Icons.close, size: 18, color: AppColors.text3),
              onPressed: () {
                widget.c.markSeen(e);
                widget.c.dismissArrival();
              },
            ),
        ]),
        Padding(
          padding: const EdgeInsets.only(right: 8),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(title, style: AppText.headline),
            const SizedBox(height: 2),
            Text('이야기 들어볼까요?', style: AppText.body.copyWith(color: AppColors.text2)),
            const SizedBox(height: 14),
            if (_loading)
              const SizedBox(height: 52, child: Center(child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.ai))))
            else if (ready)
              Row(children: [
                Expanded(child: _LengthButton(label: '짧게', sub: shortFirst ? '30초 · 평소 선호' : '30초', filled: shortFirst, onTap: () => widget.c.openArrivalGuide(e, 'short'))),
                const SizedBox(width: 8),
                Expanded(child: _LengthButton(label: '자세히', sub: _style == 'long' ? '2~3분 · 평소 선호' : '2~3분', filled: !shortFirst, onTap: () => widget.c.openArrivalGuide(e, 'long'))),
              ])
            else ...[
              AppButton(
                label: '지금 설명 듣기',
                icon: Icons.auto_awesome,
                kind: ButtonKind.aiSoft,
                onPressed: () {
                  widget.c.markSeen(e);
                  widget.c.dismissArrival();
                  widget.c.onOpenAi?.call(prefill: '$title 짧게 설명해줘', send: true, placeTitle: title);
                },
              ),
              const SizedBox(height: 6),
              Text(preparing ? '가이드를 준비하고 있어요. 지금 바로 들으려면 눌러 주세요' : '준비된 해설이 없어 10초쯤 걸려요', style: AppText.caption),
            ],
          ]),
        ),
      ]),
    );
  }
}

class _LengthButton extends StatelessWidget {
  const _LengthButton({required this.label, required this.sub, required this.filled, required this.onTap});
  final String label;
  final String sub;
  final bool filled;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final fg = filled ? Colors.white : AppColors.ai;
    return Material(
      color: filled ? AppColors.ai : AppColors.surface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12), side: filled ? BorderSide.none : const BorderSide(color: AppColors.aiLine)),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: onTap,
        child: SizedBox(
          height: 52,
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Text(label, style: AppText.section.copyWith(color: fg, fontSize: 15)),
            Text(sub, style: AppText.caption.copyWith(color: filled ? const Color(0xE6FFFFFF) : AppColors.ai, fontSize: 11)),
          ]),
        ),
      ),
    );
  }
}

class _ArrivalChip extends StatelessWidget {
  const _ArrivalChip({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Material(
        color: AppColors.surface,
        elevation: 0,
        shape: const StadiumBorder(side: BorderSide(color: AppColors.aiLine)),
        child: InkWell(
          key: const ValueKey('arrival-chip'),
          customBorder: const StadiumBorder(),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              const Icon(Icons.auto_awesome, size: 14, color: AppColors.ai),
              const SizedBox(width: 4),
              Text('해설 듣기', style: AppText.caption.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700)),
            ]),
          ),
        ),
      );
}

// ---------------------------------------------------------------------------
// 14-S3 선제 미리보기 · 토스트

/// 이벤트 칩: 지연 Amber clock "일정 지연" / 안전 Red shield "안전 · 위험으로 바뀜" (Components_Companion › Proactive Message Header).
class EventKindChip extends StatelessWidget {
  const EventKindChip({super.key, required this.event});
  final TripEvent event;

  @override
  Widget build(BuildContext context) {
    final safety = event.type == 'safety_raised';
    final danger = event.severity == 'danger';
    final (label, icon, fg, bg) = safety
        ? (danger ? '안전 · 위험으로 바뀜' : '안전 · 주의로 바뀜', Icons.shield_outlined, danger ? AppColors.danger : AppColors.warning, danger ? AppColors.dangerSoft : AppColors.warningSoft)
        : ('일정 지연', Icons.schedule, AppColors.warning, AppColors.warningSoft);
    return InfoChip(label: label, icon: icon, fg: fg, bg: bg);
  }
}

class ProactivePeek extends StatelessWidget {
  const ProactivePeek({super.key, required this.event, required this.c, this.banner = false});
  final TripEvent event;
  final TripController c;
  final bool banner;

  @override
  Widget build(BuildContext context) {
    final safety = event.type == 'safety_raised';
    return ConstrainedBox(
      constraints: BoxConstraints(maxWidth: banner ? double.infinity : 232),
      child: Material(
        key: const ValueKey('proactive-peek'),
        color: AppColors.surface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: BorderSide(color: safety ? AppColors.dangerLine : AppColors.aiLine)),
        elevation: 6,
        shadowColor: const Color(0x406E59FF),
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: () => c.openProactive(event),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 10, 4, 10),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
              Row(children: [
                const Icon(Icons.auto_awesome, size: 14, color: AppColors.ai),
                const SizedBox(width: 4),
                Text('구룡메이트', style: AppText.caption.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700)),
                const SizedBox(width: 6),
                Expanded(child: Align(alignment: Alignment.centerLeft, child: EventKindChip(event: event))),
                InkWell(
                  onTap: c.dismissPreview,
                  child: const Padding(padding: EdgeInsets.all(4), child: Icon(Icons.close, size: 16, color: AppColors.text3)),
                ),
              ]),
              const SizedBox(height: 6),
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: Text(event.message, style: AppText.label.copyWith(fontWeight: FontWeight.w500), maxLines: 3, overflow: TextOverflow.ellipsis),
              ),
              const SizedBox(height: 4),
              Text('보기 ›', style: AppText.caption.copyWith(color: AppColors.primary, fontWeight: FontWeight.w700)),
            ]),
          ),
        ),
      ),
    );
  }
}

/// 출발 토스트는 서버 문구에서 "다음은 …" 부분을 쓴다 ("…을(를) 떠났어요. 다음은 구룡포항, 도보 약 8분 (13:39 도착 예정).").
String toastText(TripEvent e) {
  final m = e.message;
  if (e.type == 'departed') {
    final i = m.indexOf('다음은');
    if (i >= 0) return m.substring(i).replaceAll(RegExp(r'\s*\([^)]*\)\.?$'), '').replaceAll(RegExp(r'\.$'), '');
  }
  return m;
}

class EventToast extends StatelessWidget {
  const EventToast({super.key, required this.event, required this.c});
  final TripEvent event;
  final TripController c;

  @override
  Widget build(BuildContext context) {
    final departed = event.type == 'departed';
    return Material(
      key: const ValueKey('event-toast'),
      color: AppColors.dark,
      borderRadius: BorderRadius.circular(14),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 4, 4, 4),
        child: SizedBox(
          height: 44,
          child: Row(children: [
            Icon(departed ? Icons.directions_walk : Icons.verified_user_outlined, color: departed ? Colors.white : const Color(0xFF4ADE80), size: 18),
            const SizedBox(width: 8),
            Expanded(child: Text(toastText(event), style: AppText.label.copyWith(color: Colors.white), maxLines: 2, overflow: TextOverflow.ellipsis)),
            if (departed)
              TextButton(
                onPressed: () {
                  c.dismissToast();
                  c.plan.setTab(1);
                },
                child: Text('경로 보기', style: AppText.label.copyWith(color: const Color(0xFF9DB8FF), fontWeight: FontWeight.w700)),
              )
            else
              const SizedBox(width: 12),
          ]),
        ),
      ),
    );
  }
}
