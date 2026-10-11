import 'package:flutter/material.dart';

import '../../core/format.dart';
import '../../core/models/plan.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import 'place_search_sheet.dart';
import 'plan_controller.dart';

/// 일정 카드 ⋮ 메뉴: 시간 변경 · 체류 · 메모 · 날짜 이동 · 건너뛰기 · 유지 규칙 · 삭제.
Future<void> showItemActions(BuildContext context, PlanController c, PlanItem item) async {
  final plan = c.plan;
  if (plan == null) return;
  final kept = plan.constraints.any((r) => r.type == 'must_include' && r.params['place_id'] == item.placeId);
  final action = await showModalBottomSheet<String>(
    context: context,
    showDragHandle: true,
    isScrollControlled: true,
    builder: (ctx) {
      Widget tile(String key, IconData icon, String label, {String? sub, Color? color}) => ListTile(
            leading: Icon(icon, color: color ?? AppColors.text2),
            title: Text(label, style: AppText.label.copyWith(color: color)),
            subtitle: sub == null ? null : Text(sub, style: AppText.caption),
            onTap: () => Navigator.pop(ctx, key),
          );
      return SafeArea(
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
              child: Row(children: [Expanded(child: Text(item.title, style: AppText.headline, overflow: TextOverflow.ellipsis))]),
            ),
            tile('detail', Icons.info_outline, '장소 정보'),
            tile('time', Icons.schedule, item.startTime == null ? '시작 시간 고정' : '고정 시간 변경', sub: item.startTime == null ? '앞 일정에 이어서 자동 계산 중' : '${item.startTime} 고정'),
            if (item.startTime != null) tile('unfix', Icons.push_pin_outlined, '시간 고정 해제'),
            tile('stay', Icons.timelapse, '체류 시간 변경', sub: durationKo(item.stayMinutes)),
            tile('memo', Icons.edit_note, '메모'),
            if (plan.numDays > 1) tile('day', Icons.event_repeat, '다른 날로 이동'),
            tile('skip', item.skipped ? Icons.undo : Icons.redo, item.skipped ? '다시 방문 예정으로' : '이번 장소 건너뛰기'),
            tile('keep', kept ? Icons.lock_open : Icons.lock_outline, kept ? '꼭 유지 해제' : '꼭 유지하기', sub: kept ? null : 'AI가 바꿀 때도 이 장소는 남겨요'),
            tile('map', Icons.map_outlined, '지도에서 보기'),
            tile('delete', Icons.delete_outline, '삭제', color: AppColors.danger),
          ]),
        ),
      );
    },
  );
  if (action == null || !context.mounted) return;

  try {
    switch (action) {
      case 'detail':
        await showPlaceDetailSheet(context, c, item.placeId, inPlan: true);
      case 'time':
        final init = toMinutes(item.startTime ?? item.startAt) ?? 600;
        final t = await showTimePicker(context: context, initialTime: TimeOfDay(hour: (init ~/ 60) % 24, minute: init % 60));
        if (t != null) await c.updateItem(item.id, {'start_time': '${two(t.hour)}:${two(t.minute)}'});
      case 'unfix':
        await c.updateItem(item.id, {'start_time': null});
      case 'stay':
        final m = await _pickStay(context, item.stayMinutes);
        if (m != null) await c.updateItem(item.id, {'stay_minutes': m});
      case 'memo':
        final text = await _editText(context, '메모', item.memo);
        if (text != null) await c.updateItem(item.id, {'memo': text});
      case 'day':
        final d = await _pickDay(context, plan, item.day);
        if (d != null) await c.updateItem(item.id, {'day': d});
      case 'skip':
        await c.updateItem(item.id, {'status': item.skipped ? 'planned' : 'skipped'});
      case 'keep':
        if (kept) {
          final rule = plan.constraints.firstWhere((r) => r.type == 'must_include' && r.params['place_id'] == item.placeId);
          await c.removeConstraint(rule.id);
        } else {
          await c.addConstraint({'type': 'must_include', 'params': {'place_id': item.placeId}, 'note': '${item.title} 유지'});
        }
      case 'map':
        c.showOnMap(item.id);
      case 'delete':
        final ok = await confirmDelete(context, item.title);
        if (ok) await c.removeItem(item.id);
    }
  } catch (e) {
    if (context.mounted) showError(context, e);
  }
}

Future<bool> confirmDelete(BuildContext context, String title) async =>
    await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('일정에서 삭제할까요?'),
        content: Text("'$title'을(를) 일정에서 뺍니다."),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('취소')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('삭제', style: TextStyle(color: AppColors.danger))),
        ],
      ),
    ) ??
    false;

Future<int?> _pickStay(BuildContext context, int current) {
  var value = current.toDouble().clamp(10, 360);
  return showDialog<int>(
    context: context,
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, set) => AlertDialog(
        title: const Text('체류 시간'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(durationKo(value.round()), style: AppText.title),
          Slider(value: value.toDouble(), min: 10, max: 360, divisions: 35, onChanged: (v) => set(() => value = v)),
          Wrap(spacing: 6, children: [
            for (final m in [30, 60, 90, 120])
              ChoiceChip(label: Text(durationKo(m)), selected: value.round() == m, onSelected: (_) => set(() => value = m.toDouble())),
          ]),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('취소')),
          TextButton(onPressed: () => Navigator.pop(ctx, value.round()), child: const Text('저장')),
        ],
      ),
    ),
  );
}

Future<String?> _editText(BuildContext context, String title, String initial) =>
    showDialog<String>(context: context, builder: (_) => _TextDialog(title: title, initial: initial));

/// 입력 컨트롤러를 대화상자가 갖는다. 닫히는 애니메이션이 끝난 뒤에 정리돼야 해서다.
class _TextDialog extends StatefulWidget {
  const _TextDialog({required this.title, required this.initial});
  final String title;
  final String initial;

  @override
  State<_TextDialog> createState() => _TextDialogState();
}

class _TextDialogState extends State<_TextDialog> {
  late final _ctrl = TextEditingController(text: widget.initial);

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: Text(widget.title),
        content: TextField(controller: _ctrl, autofocus: true, maxLines: 3, decoration: const InputDecoration(hintText: '예: 현금 준비')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('취소')),
          TextButton(onPressed: () => Navigator.pop(context, _ctrl.text.trim()), child: const Text('저장')),
        ],
      );
}

Future<int?> _pickDay(BuildContext context, PlanDetail plan, int current) => showModalBottomSheet<int>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            for (var d = 1; d <= plan.numDays; d++)
              ListTile(
                enabled: d != current,
                title: Text('Day $d'),
                subtitle: Text('${md(plan.startDate.add(Duration(days: d - 1)))} ${weekdayKo(plan.startDate.add(Duration(days: d - 1)))}'),
                trailing: d == current ? const Text('현재') : null,
                onTap: () => Navigator.pop(ctx, d),
              ),
          ]),
        ),
      ),
    );
