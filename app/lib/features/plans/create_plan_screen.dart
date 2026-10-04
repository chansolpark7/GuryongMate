import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/app_session.dart';
import '../../core/format.dart';
import '../../core/models/plan.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';

class CreatePlanResult {
  CreatePlanResult(this.plan, this.aiPrompt);
  final PlanDetail plan;

  /// "AI로 계획 만들기"로 시작했으면 Planner 에서 AI 시트를 열고 바로 보낼 문장.
  final String? aiPrompt;
}

const _regions = ['경북 포항시 구룡포읍', '경북 포항시 호미곶면', '경북 포항시 전체'];
const _styles = ['맛집', '바다·자연', '사진 명소', '여유롭게', '액티비티', '카페', '역사·문화'];

/// 03_Create_Plan (full-screen modal)
class CreatePlanScreen extends StatefulWidget {
  const CreatePlanScreen({super.key, this.startWithAi = false});
  final bool startWithAi;

  @override
  State<CreatePlanScreen> createState() => _CreatePlanScreenState();
}

class _CreatePlanScreenState extends State<CreatePlanScreen> {
  final _title = TextEditingController(text: '구룡포 여행');
  DateTimeRange? _range;
  String _region = _regions.first;
  final _picked = <String>{};
  TimeOfDay _dayStart = const TimeOfDay(hour: 10, minute: 0);
  TimeOfDay _dayEnd = const TimeOfDay(hour: 18, minute: 0);
  String _mode = 'walk';
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _title.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _title.dispose();
    super.dispose();
  }

  bool get _valid => _title.text.trim().isNotEmpty && _range != null;

  String _hhmm(TimeOfDay t) => '${two(t.hour)}:${two(t.minute)}';

  Future<void> _pickDates() async {
    final now = dateOnly(DateTime.now());
    final r = await showDateRangePicker(
      context: context,
      firstDate: now.subtract(const Duration(days: 365)),
      lastDate: now.add(const Duration(days: 730)),
      initialDateRange: _range,
      helpText: '여행 날짜 (최대 14일)',
    );
    if (r == null) return;
    if (r.end.difference(r.start).inDays + 1 > 14) {
      if (mounted) showError(context, '여행 기간은 최대 14일이에요.');
      return;
    }
    setState(() => _range = r);
  }

  Future<void> _pickRegion() async {
    final r = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          for (final r in _regions)
            ListTile(
              leading: const Icon(Icons.place_outlined),
              title: Text(r),
              trailing: r == _region ? const Icon(Icons.check, color: AppColors.primary) : null,
              onTap: () => Navigator.pop(ctx, r),
            ),
        ]),
      ),
    );
    if (r != null) setState(() => _region = r);
  }

  Future<void> _pickTime(bool start) async {
    final t = await showTimePicker(context: context, initialTime: start ? _dayStart : _dayEnd);
    if (t == null) return;
    setState(() => start ? _dayStart = t : _dayEnd = t);
  }

  Future<void> _submit() async {
    final range = _range!;
    if (_dayEnd.hour * 60 + _dayEnd.minute <= _dayStart.hour * 60 + _dayStart.minute) {
      showError(context, '하루 종료 시각은 시작 시각보다 늦어야 해요.');
      return;
    }
    setState(() => _saving = true);
    final styles = _styles.where(_picked.contains).toList();
    final memo = ['지역: $_region', if (styles.isNotEmpty) '여행 스타일: ${styles.join(', ')}'].join('\n');
    try {
      final plan = await context.read<AppSession>().api.createPlan({
        'title': _title.text.trim(),
        'start_date': isoDate(range.start),
        'end_date': isoDate(range.end),
        'day_start': _hhmm(_dayStart),
        'day_end': _hhmm(_dayEnd),
        'travel_mode': _mode,
        'memo': memo,
      });
      final region = _region.replaceFirst('경북 포항시 ', '');
      final prompt = widget.startWithAi
          ? '$region에서 ${dateRange(range.start, range.end)} ${nightsDays(range.start, range.end)} 여행 일정을 짜줘. '
              '하루 ${_hhmm(_dayStart)}~${_hhmm(_dayEnd)}, ${_mode == 'walk' ? '도보' : '차량'} 이동'
              '${styles.isEmpty ? '' : ', 여행 스타일은 ${styles.join(', ')}'}.'
          : null;
      if (mounted) Navigator.of(context).pop(CreatePlanResult(plan, prompt));
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = _range;
    return Scaffold(
      backgroundColor: AppColors.surface,
      appBar: AppBar(
        backgroundColor: AppColors.surface,
        leading: IconButton(icon: const Icon(Icons.close), onPressed: () => Navigator.pop(context), tooltip: '닫기'),
        title: Text('새 여행 만들기', style: AppText.headline),
        centerTitle: true,
      ),
      body: ListView(padding: const EdgeInsets.fromLTRB(20, 12, 20, 24), children: [
        const _Label('Plan 이름'),
        TextField(controller: _title, decoration: const InputDecoration(hintText: '예: 엄마랑 구룡포')),
        const SizedBox(height: 20),
        const _Label('날짜'),
        _FieldButton(
          icon: Icons.calendar_today_outlined,
          text: r == null
              ? null
              : '${md(r.start)} (${weekdayKo(r.start)}) – ${md(r.end)} (${weekdayKo(r.end)}) · ${nightsDays(r.start, r.end)}',
          hint: '날짜를 선택하세요',
          onTap: _pickDates,
        ),
        const SizedBox(height: 20),
        const _Label('지역'),
        _FieldButton(icon: Icons.place_outlined, text: _region, onTap: _pickRegion),
        const SizedBox(height: 20),
        const _Label('하루 일정 시간'),
        Row(children: [
          Expanded(child: _FieldButton(icon: Icons.schedule, text: '${_hhmm(_dayStart)} 시작', onTap: () => _pickTime(true))),
          const SizedBox(width: 8),
          Expanded(child: _FieldButton(icon: Icons.flag_outlined, text: '${_hhmm(_dayEnd)} 종료', onTap: () => _pickTime(false))),
        ]),
        const SizedBox(height: 20),
        const _Label('이동 수단'),
        Wrap(spacing: 8, children: [
          _StyleChip(label: '도보', selected: _mode == 'walk', onTap: () => setState(() => _mode = 'walk')),
          _StyleChip(label: '차량', selected: _mode == 'car', onTap: () => setState(() => _mode = 'car')),
        ]),
        const SizedBox(height: 20),
        Row(children: [
          Text('여행 스타일', style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
          const SizedBox(width: 4),
          Text('선택', style: AppText.caption),
        ]),
        const SizedBox(height: 8),
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final s in _styles)
            _StyleChip(
              label: s,
              selected: _picked.contains(s),
              onTap: () => setState(() => _picked.contains(s) ? _picked.remove(s) : _picked.add(s)),
            ),
        ]),
      ]),
      bottomNavigationBar: Container(
        decoration: const BoxDecoration(border: Border(top: BorderSide(color: AppColors.line))),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 16),
            child: AppButton(
              label: widget.startWithAi ? 'AI로 계획 만들기' : '여행 만들기',
              icon: widget.startWithAi ? Icons.auto_awesome : null,
              loading: _saving,
              onPressed: _valid ? _submit : null,
            ),
          ),
        ),
      ),
    );
  }
}

class _Label extends StatelessWidget {
  const _Label(this.text);
  final String text;

  @override
  Widget build(BuildContext context) =>
      Padding(padding: const EdgeInsets.only(bottom: 8), child: Text(text, style: AppText.label.copyWith(fontWeight: FontWeight.w700)));
}

class _FieldButton extends StatelessWidget {
  const _FieldButton({required this.icon, this.text, this.hint, required this.onTap});
  final IconData icon;
  final String? text;
  final String? hint;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Material(
        color: AppColors.field,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: onTap,
          child: SizedBox(
            height: 52,
            child: Row(children: [
              const SizedBox(width: 14),
              Icon(icon, size: 20, color: AppColors.text2),
              const SizedBox(width: 10),
              Expanded(
                child: Text(text ?? hint ?? '', style: AppText.body.copyWith(color: text == null ? AppColors.text3 : AppColors.text), overflow: TextOverflow.ellipsis),
              ),
            ]),
          ),
        ),
      );
}

class _StyleChip extends StatelessWidget {
  const _StyleChip({required this.label, required this.selected, required this.onTap});
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Material(
        color: selected ? AppColors.primarySoft : AppColors.surface,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(18),
          side: BorderSide(color: selected ? AppColors.primarySoft : AppColors.line),
        ),
        child: InkWell(
          borderRadius: BorderRadius.circular(18),
          onTap: onTap,
          child: Container(
            height: 36,
            padding: const EdgeInsets.symmetric(horizontal: 14),
            child: Center(widthFactor: 1, child: Text(label, style: AppText.label.copyWith(color: selected ? AppColors.primary : AppColors.text2))),
          ),
        ),
      );
}
