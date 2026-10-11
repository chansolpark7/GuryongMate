import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/api/guryong_api.dart';
import '../../core/format.dart';
import '../../core/models/place.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import 'plan_controller.dart';

class PickedPlace {
  PickedPlace(this.placeId, this.day, this.stayMinutes);
  final String placeId;
  final int day;
  final int stayMinutes;
}

const _types = ['전체', '관광지', '음식점', '문화시설', '레포츠', '쇼핑', '숙박'];

/// "일정 추가" → 장소 검색 바텀시트. 선택한 Day 끝에 추가한다 (시각은 직전 일정 + 이동시간으로 자동).
Future<PickedPlace?> showPlaceSearchSheet(BuildContext context, PlanController c) => showModalBottomSheet<PickedPlace>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      builder: (_) => FractionallySizedBox(heightFactor: 0.92, child: _PlaceSearch(controller: c)),
    );

class _PlaceSearch extends StatefulWidget {
  const _PlaceSearch({required this.controller});
  final PlanController controller;

  @override
  State<_PlaceSearch> createState() => _PlaceSearchState();
}

class _PlaceSearchState extends State<_PlaceSearch> {
  final _q = TextEditingController();
  String _type = '전체';
  List<PlaceSummary>? _results;
  Object? _error;
  Timer? _debounce;
  int _seq = 0;

  @override
  void initState() {
    super.initState();
    _search();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _q.dispose();
    super.dispose();
  }

  void _onChanged(String _) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350), _search);
  }

  Future<void> _search() async {
    final seq = ++_seq;
    setState(() => _error = null);
    final c = widget.controller;
    // 기준점: 그날 마지막 일정 근처. 일정이 없으면 구룡포읍 전체.
    final last = c.currentDay?.items.where((i) => i.place != null).lastOrNull?.place;
    final q = _q.text.trim();
    try {
      final res = await c.api.searchPlaces(
        q: q,
        contentType: _type == '전체' ? null : _type,
        area: last == null && q.isEmpty ? '구룡포읍' : null,
        lat: last?.lat,
        lng: last?.lng,
        radiusM: last != null && q.isEmpty ? 5000 : null,
        limit: 40,
      );
      if (mounted && seq == _seq) setState(() => _results = res);
    } catch (e) {
      if (mounted && seq == _seq) setState(() => _error = e);
    }
  }

  Future<void> _openDetail(PlaceSummary p) async {
    final picked = await showPlaceDetailSheet(context, widget.controller, p.id);
    if (picked != null && mounted) Navigator.pop(context, picked);
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final results = _results;
    return Column(children: [
      const Grabber(),
      Padding(
        padding: const EdgeInsets.fromLTRB(20, 4, 12, 8),
        child: Row(children: [
          Expanded(child: Text('Day ${c.day}에 일정 추가', style: AppText.headline)),
          IconButton(icon: const Icon(Icons.close), onPressed: () => Navigator.pop(context), tooltip: '닫기'),
        ]),
      ),
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16),
        child: TextField(
          controller: _q,
          onChanged: _onChanged,
          onSubmitted: (_) => _search(),
          textInputAction: TextInputAction.search,
          decoration: const InputDecoration(
            hintText: '장소 이름이나 하고 싶은 것 (예: 바다 보며 쉬기)',
            prefixIcon: Icon(Icons.search, color: AppColors.text3),
          ),
        ),
      ),
      SizedBox(
        height: 52,
        child: ListView.separated(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          scrollDirection: Axis.horizontal,
          itemCount: _types.length,
          separatorBuilder: (_, _) => const SizedBox(width: 6),
          itemBuilder: (_, i) {
            final t = _types[i];
            final on = t == _type;
            return ChoiceChip(
              label: Text(t),
              selected: on,
              showCheckmark: false,
              labelStyle: AppText.label.copyWith(color: on ? AppColors.primary : AppColors.text2),
              selectedColor: AppColors.primarySoft,
              backgroundColor: AppColors.surface,
              side: BorderSide(color: on ? AppColors.primarySoft : AppColors.line),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
              onSelected: (_) {
                setState(() => _type = t);
                _search();
              },
            );
          },
        ),
      ),
      Expanded(
        child: _error != null
            ? StateView(title: '장소를 불러오지 못했어요', message: '$_error', actionLabel: '다시 시도', onAction: _search)
            : results == null
                ? const Center(child: CircularProgressIndicator())
                : results.isEmpty
                    ? const StateView(icon: Icons.search_off, title: '검색 결과가 없어요')
                    : ListView.separated(
                        padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
                        itemCount: results.length,
                        separatorBuilder: (_, _) => const Divider(height: 1, color: AppColors.line),
                        itemBuilder: (_, i) {
                          final p = results[i];
                          final meta = [
                            p.categoryLabel,
                            if (p.distanceM != null) p.distanceM! < 1000 ? '${p.distanceM!.round()}m' : '${(p.distanceM! / 1000).toStringAsFixed(1)}km',
                            if (p.area.isNotEmpty) p.area,
                          ].join(' · ');
                          return ListTile(
                            contentPadding: const EdgeInsets.symmetric(vertical: 4),
                            leading: PlaceThumb(contentType: p.contentType, url: p.thumbnailUrl),
                            title: Text(p.title, style: AppText.section.copyWith(fontSize: 15)),
                            subtitle: Text(meta, style: AppText.caption, maxLines: 1, overflow: TextOverflow.ellipsis),
                            trailing: IconButton.filledTonal(
                              tooltip: '바로 추가',
                              style: IconButton.styleFrom(backgroundColor: AppColors.primarySoft, foregroundColor: AppColors.primary),
                              icon: const Icon(Icons.add),
                              onPressed: () => Navigator.pop(context, PickedPlace(p.id, c.day, 60)),
                            ),
                            onTap: () => _openDetail(p),
                          );
                        },
                      ),
      ),
    ]);
  }
}

/// 장소 상세. [inPlan] 이 아니면 하단에 "Day N에 추가" (체류 시간 선택).
Future<PickedPlace?> showPlaceDetailSheet(BuildContext context, PlanController c, String placeId, {bool inPlan = false}) =>
    showModalBottomSheet<PickedPlace>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      builder: (_) => FractionallySizedBox(heightFactor: 0.88, child: _PlaceDetail(controller: c, placeId: placeId, inPlan: inPlan)),
    );

class _PlaceDetail extends StatefulWidget {
  const _PlaceDetail({required this.controller, required this.placeId, required this.inPlan});
  final PlanController controller;
  final String placeId;
  final bool inPlan;

  @override
  State<_PlaceDetail> createState() => _PlaceDetailState();
}

class _PlaceDetailState extends State<_PlaceDetail> {
  PlaceDetail? _place;

  @override
  Widget build(BuildContext context) {
    final p = _place;
    final c = widget.controller;
    return Column(children: [
      const Grabber(),
      Expanded(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
          child: PlaceDetailContent(api: c.api, placeId: widget.placeId, onLoaded: (p) => setState(() => _place = p)),
        ),
      ),
      if (!widget.inPlan && p != null)
        Container(
          decoration: const BoxDecoration(border: Border(top: BorderSide(color: AppColors.line))),
          padding: const EdgeInsets.fromLTRB(20, 10, 20, 12),
          child: SafeArea(
            top: false,
            child: AddPlaceFooter(day: c.day, onAdd: (stay) => Navigator.pop(context, PickedPlace(p.id, c.day, stay))),
          ),
        ),
    ]);
  }
}

/// "체류 30분·1시간·…" + [Day N에 추가]. 장소 상세 모달과 Map 시트(관광지 레이어)가 같이 쓴다.
class AddPlaceFooter extends StatefulWidget {
  const AddPlaceFooter({super.key, required this.day, required this.onAdd});
  final int day;
  final ValueChanged<int> onAdd;

  @override
  State<AddPlaceFooter> createState() => _AddPlaceFooterState();
}

class _AddPlaceFooterState extends State<AddPlaceFooter> {
  int _stay = 60;

  @override
  Widget build(BuildContext context) => Column(mainAxisSize: MainAxisSize.min, children: [
        Row(children: [
          Text('체류', style: AppText.label),
          const SizedBox(width: 10),
          Expanded(
            child: Wrap(spacing: 6, children: [
              for (final m in [30, 60, 90, 120])
                ChoiceChip(
                  label: Text(durationKo(m)),
                  selected: _stay == m,
                  visualDensity: VisualDensity.compact,
                  onSelected: (_) => setState(() => _stay = m),
                ),
            ]),
          ),
        ]),
        const SizedBox(height: 10),
        AppButton(label: 'Day ${widget.day}에 추가', icon: Icons.add, onPressed: () => widget.onAdd(_stay)),
      ]);
}

/// 장소 상세 본문: 사진 · 이름 · 분류 · 주소 · 운영시간 · 휴무 · 요금 · 주차 · 메뉴 · 전화 · 소개(더 보기) · 현장 해설 이야기.
/// 스크롤하지 않는 Column 이라 감싸는 쪽(모달의 SingleChildScrollView, Map 시트의 ListView)이 스크롤한다.
class PlaceDetailContent extends StatefulWidget {
  const PlaceDetailContent({super.key, required this.api, required this.placeId, this.onLoaded, this.showTitle = true});
  final GuryongApi api;
  final String placeId;
  final ValueChanged<PlaceDetail>? onLoaded;

  /// 큰 제목 + 분류 줄. Map 시트는 위 장소 카드에 이름·분류가 있어 끈다 (모달은 그대로, 2026-10-11).
  final bool showTitle;

  @override
  State<PlaceDetailContent> createState() => _PlaceDetailContentState();
}

class _PlaceDetailContentState extends State<PlaceDetailContent> {
  PlaceDetail? _place;
  List<Story> _stories = const [];
  Object? _error;
  bool _overviewOpen = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(PlaceDetailContent old) {
    super.didUpdateWidget(old);
    if (old.placeId == widget.placeId) return;
    setState(() {
      _place = null;
      _stories = const [];
      _error = null;
      _overviewOpen = false;
    });
    _load();
  }

  Future<void> _load() async {
    final id = widget.placeId;
    final api = widget.api;
    try {
      final p = await api.place(id);
      if (!mounted || id != widget.placeId) return;
      setState(() => _place = p);
      widget.onLoaded?.call(p);
      if (p.storyCount > 0) {
        final s = await api.placeStories(id);
        if (mounted && id == widget.placeId) setState(() => _stories = s);
      }
    } catch (e) {
      if (mounted && id == widget.placeId) setState(() => _error = e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = _place;
    if (_error != null) return StateView(title: '장소 정보를 불러오지 못했어요', message: '$_error');
    if (p == null) return const SizedBox(height: 120, child: Center(child: CircularProgressIndicator()));
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
      if (p.images.isNotEmpty || p.imageUrl.isNotEmpty) ...[
        _Gallery(urls: p.images.isNotEmpty ? p.images.map((i) => i.url).toList() : [p.imageUrl]),
        const SizedBox(height: 14),
      ],
      if (widget.showTitle) ...[
        Text(p.title, style: AppText.title),
        const SizedBox(height: 4),
        Text([p.categoryLabel, if (p.area.isNotEmpty) p.area].join(' · '), style: AppText.meta),
        const SizedBox(height: 16),
      ],
      _InfoRow(Icons.place_outlined, p.addr),
      _InfoRow(Icons.schedule, p.openHours, label: '운영'),
      _InfoRow(Icons.event_busy_outlined, p.restDate, label: '휴무'),
      _InfoRow(Icons.payments_outlined, p.fee, label: '요금'),
      _InfoRow(Icons.local_parking, p.parking, label: '주차'),
      _InfoRow(Icons.restaurant_menu, p.menu, label: '메뉴'),
      _InfoRow(Icons.call_outlined, p.tel),
      if (p.overview.isNotEmpty) ...[
        const SizedBox(height: 12),
        Text('소개', style: AppText.section),
        const SizedBox(height: 6),
        Text(p.overview, style: AppText.body.copyWith(color: AppColors.text2), maxLines: _overviewOpen ? null : 5, overflow: _overviewOpen ? null : TextOverflow.ellipsis),
        if (p.overview.length > 150)
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton(onPressed: () => setState(() => _overviewOpen = !_overviewOpen), child: Text(_overviewOpen ? '접기' : '더 보기')),
          ),
      ],
      if (_stories.isNotEmpty) ...[
        const SizedBox(height: 12),
        Row(children: [
          const Icon(Icons.headphones, size: 18, color: AppColors.ai),
          const SizedBox(width: 6),
          Text('현장 해설 이야기 ${_stories.length}', style: AppText.section),
        ]),
        const SizedBox(height: 6),
        for (final s in _stories)
          Card(
            elevation: 0,
            color: AppColors.aiSoft,
            margin: const EdgeInsets.only(bottom: 8),
            child: ExpansionTile(
              shape: const Border(),
              title: Text(s.title, style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
              subtitle: Text([if (s.themeTitle.isNotEmpty) s.themeTitle, if (s.playTimeSec > 0) '${(s.playTimeSec / 60).ceil()}분'].join(' · '), style: AppText.caption),
              childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
              children: [Text(s.script, style: AppText.meta.copyWith(color: AppColors.text))],
            ),
          ),
      ],
    ]);
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow(this.icon, this.text, {this.label});
  final IconData icon;
  final String text;
  final String? label;

  @override
  Widget build(BuildContext context) {
    if (text.trim().isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(icon, size: 18, color: AppColors.text3),
        const SizedBox(width: 8),
        if (label != null) SizedBox(width: 36, child: Text(label!, style: AppText.meta.copyWith(color: AppColors.text3))),
        Expanded(child: Text(text.replaceAll(RegExp(r'<br\s*/?>'), '\n'), style: AppText.meta.copyWith(color: AppColors.text))),
      ]),
    );
  }
}

class _Gallery extends StatelessWidget {
  const _Gallery({required this.urls});
  final List<String> urls;

  @override
  Widget build(BuildContext context) => SizedBox(
        height: 190,
        child: PageView.builder(
          controller: PageController(viewportFraction: urls.length > 1 ? 0.92 : 1),
          itemCount: urls.length,
          itemBuilder: (_, i) => Padding(
            padding: EdgeInsets.only(right: urls.length > 1 ? 8 : 0),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(16),
              child: Image.network(
                urls[i],
                fit: BoxFit.cover,
                errorBuilder: (_, _, _) => Container(color: AppColors.field, child: const Icon(Icons.image_not_supported_outlined, color: AppColors.text3)),
              ),
            ),
          ),
        ),
      );
}
