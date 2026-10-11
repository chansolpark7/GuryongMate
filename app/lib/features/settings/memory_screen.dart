import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/app_session.dart';
import '../../core/format.dart';
import '../../core/models/account.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';

const memoryCategories = {
  'interest': '관심사',
  'style': '해설 스타일',
  'companion': '동행자·제약',
  'food': '음식',
  'avoid': '피하고 싶은 것',
  'known': '이미 들은 이야기',
  'feedback': '반응',
};

/// 31_Memory (Figma 73:5603). 구룡메이트가 기억하는 것을 보여 주고 지울 수 있게 한다 (docent §1.2).
class MemoryScreen extends StatefulWidget {
  const MemoryScreen({super.key, this.planId});
  final int? planId;

  @override
  State<MemoryScreen> createState() => _MemoryScreenState();
}

class _MemoryScreenState extends State<MemoryScreen> {
  List<MemoryItem>? _items;
  Object? _error;
  int _segment = 0; // 0 전체 · 1 이번 여행 · 2 모든 여행

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final m = await context.read<AppSession>().api.memory(planId: widget.planId);
      if (mounted) {
        setState(() {
          _items = m.items;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  /// 확인 없이 지우고 4초 동안 되돌리기 (같은 내용 POST, source: user_edit).
  Future<void> _delete(MemoryItem m) async {
    final api = context.read<AppSession>().api;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await api.deleteMemory(m.id);
      setState(() => _items = _items?.where((x) => x.id != m.id).toList());
      messenger
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(
          duration: const Duration(seconds: 4),
          content: const Text('지웠어요'),
          action: SnackBarAction(
            label: '되돌리기',
            onPressed: () async {
              await api.addMemory({'scope': m.scope, if (m.planId != null) 'plan_id': m.planId, 'category': m.category, 'text': m.text, 'source': 'user_edit'});
              _load();
            },
          ),
        ));
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _edit(MemoryItem m) async {
    final api = context.read<AppSession>().api;
    var text = m.text;
    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (ctx) => Padding(
        padding: EdgeInsets.fromLTRB(20, 0, 20, 20 + MediaQuery.viewInsetsOf(ctx).bottom),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text('고치기', style: AppText.headline),
          const SizedBox(height: 12),
          TextFormField(initialValue: m.text, autofocus: true, maxLength: 200, onChanged: (v) => text = v),
          const SizedBox(height: 8),
          AppButton(label: '저장', onPressed: () => Navigator.pop(ctx, true)),
        ]),
      ),
    );
    if (ok != true || text.trim().isEmpty || text.trim() == m.text) return;
    try {
      await api.updateMemory(m.id, {'text': text.trim(), 'source': 'user_edit'});
      _load();
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _clearAll(List<MemoryItem> items) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('기억한 것을 모두 지울까요?'),
        content: Text('취향·해설 스타일·동행 정보 ${items.length}개가 지워져요. 일정은 바뀌지 않아요.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('취소')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('모두 지우기', style: TextStyle(color: AppColors.danger))),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    final api = context.read<AppSession>().api;
    try {
      for (final m in items) {
        await api.deleteMemory(m.id); // 일괄 삭제 API 없음 (gaps b11)
      }
    } catch (e) {
      if (mounted) showError(context, e);
    }
    _load();
  }

  String _meta(MemoryItem m) {
    final scope = m.scope == 'plan' ? '이번 여행' : '모든 여행';
    final now = DateTime.now();
    final t = m.createdAt;
    final date = t.year == now.year && t.month == now.month && t.day == now.day ? '오늘 ${two(t.hour)}:${two(t.minute)}' : '${t.month}/${t.day}';
    final src = m.source == 'user_edit' ? '직접 수정' : '대화';
    return '$scope · $date $src';
  }

  @override
  Widget build(BuildContext context) {
    final all = _items;
    final items = (all ?? const <MemoryItem>[]).where((m) => _segment == 0 || (_segment == 1 ? m.scope == 'plan' : m.scope == 'user')).toList();
    return Scaffold(
      appBar: AppBar(centerTitle: true, title: Text('나에 대해', style: AppText.headline)),
      body: all == null
          ? (_error == null
              ? const Center(child: CircularProgressIndicator())
              : StateView(icon: Icons.dns_outlined, title: '서버에 연결하지 못했어요', message: '잠시 후 다시 시도해 주세요.', actionLabel: '다시 시도', onAction: _load))
          : ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 32), children: [
              Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(color: AppColors.aiSoft, borderRadius: BorderRadius.circular(14)),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  const Icon(Icons.auto_awesome, size: 16, color: AppColors.ai),
                  const SizedBox(width: 8),
                  Expanded(child: Text('구룡메이트가 대화에서 알게 된 것이에요. 말씀하셨거나 확인하신 것만 저장해요. 틀린 건 지워 주세요.', style: AppText.meta.copyWith(color: AppColors.ai))),
                ]),
              ),
              const SizedBox(height: 14),
              _Segment(index: _segment, onChanged: (i) => setState(() => _segment = i)),
              if (items.isEmpty)
                const Padding(
                  padding: EdgeInsets.only(top: 40),
                  child: StateView(icon: Icons.bookmark_border, title: '아직 기억한 게 없어요', message: '대화하면서 알려주시는 취향을 여기에 모아요.'),
                ),
              for (final e in memoryCategories.entries)
                if (items.any((m) => m.category == e.key)) ...[
                  Padding(padding: const EdgeInsets.fromLTRB(4, 16, 4, 6), child: Text(e.value, style: AppText.caption.copyWith(color: AppColors.text2, fontWeight: FontWeight.w700))),
                  for (final m in items.where((m) => m.category == e.key))
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: AppCard(
                        radius: 14,
                        padding: const EdgeInsets.fromLTRB(16, 10, 4, 10),
                        onTap: () => _edit(m),
                        child: Row(children: [
                          Expanded(
                            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                              Row(children: [
                                Flexible(child: Text(m.text, style: AppText.label)),
                                if (DateTime.now().difference(m.createdAt).inHours < 24) ...[
                                  const SizedBox(width: 6),
                                  Container(
                                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                                    decoration: BoxDecoration(color: AppColors.aiSoft, borderRadius: BorderRadius.circular(8)),
                                    child: Text('새로', style: AppText.caption.copyWith(color: AppColors.ai, fontWeight: FontWeight.w700)),
                                  ),
                                ],
                              ]),
                              Text(_meta(m), style: AppText.caption),
                            ]),
                          ),
                          IconButton(tooltip: '지우기', icon: const Icon(Icons.delete_outline, color: AppColors.text2), onPressed: () => _delete(m)),
                        ]),
                      ),
                    ),
                ],
              if (items.isNotEmpty) Center(child: TextButton(onPressed: () => _clearAll(items), child: Text('모두 지우기', style: AppText.label.copyWith(color: AppColors.danger)))),
            ]),
    );
  }
}

class _Segment extends StatelessWidget {
  const _Segment({required this.index, required this.onChanged});
  final int index;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(4),
        decoration: BoxDecoration(color: AppColors.line, borderRadius: BorderRadius.circular(12)),
        child: Row(children: [
          for (final (i, t) in ['전체', '이번 여행', '모든 여행'].indexed)
            Expanded(
              child: GestureDetector(
                onTap: () => onChanged(i),
                child: Container(
                  height: 32,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(color: i == index ? AppColors.surface : Colors.transparent, borderRadius: BorderRadius.circular(9)),
                  child: Text(t, style: AppText.label.copyWith(fontWeight: i == index ? FontWeight.w700 : FontWeight.w500, color: i == index ? AppColors.text : AppColors.text2)),
                ),
              ),
            ),
        ]),
      );
}
