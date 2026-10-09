import 'package:flutter/material.dart';

import '../../core/api/api_client.dart';
import '../../core/models/plan.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import 'chat_controller.dart';

/// planner Change.message 의 "추가: " 같은 접두사를 뗀다.
String stripChangePrefix(String m) {
  final i = m.indexOf(': ');
  return i >= 0 && i < 8 ? m.substring(i + 2) : m;
}

/// 규칙 문구는 괄호 안 사람 말("18시 전에 끝내기")만 쓴다 (함수명·코드 노출 금지).
String ruleText(String message) {
  final m = RegExp(r'\(([^()]+)\)\s*$').firstMatch(message);
  if (m != null) return m.group(1)!;
  final s = stripChangePrefix(message);
  return s.contains('{') ? '여행 조건' : s;
}

(String, Color, Color) changeKindBadge(String kind) => switch (kind) {
      'added' => ('추가', AppColors.success, AppColors.successSoft),
      'removed' => ('삭제', AppColors.danger, AppColors.dangerSoft),
      'moved' => ('이동', AppColors.primary, AppColors.primarySoft),
      'updated' => ('변경', AppColors.primary, AppColors.primarySoft),
      'rule_added' || 'rule_removed' => ('조건', AppColors.text2, AppColors.field),
      'day_end' => ('종료', AppColors.text2, AppColors.field),
      _ => ('여행', AppColors.text2, AppColors.field),
    };

enum BaseChoice { repropose, overwrite }

/// [전체 적용] 흐름 (F2, agent §4.1). 06 카드와 21 비교 화면이 같이 쓴다.
/// - 200 → 카드 Applied (셸이 시트를 내리고 실행 취소 스낵바)
/// - 409 base_changed → 36 확인창 → 다시 제안(repropose) / 그대로 반영(overwrite) / 취소
/// - 409 progress_changed → 다시 제안만 (덮어쓰기 없음)
/// - 409 agent_busy → 스낵바, 카드 유지 · 409 proposal_not_pending → 카드를 서버 상태로 + 스낵바 · 503 → 오류 스낵바
/// 다시 제안을 골랐으면 true (부른 쪽이 대화로 돌아가야 함).
Future<bool> acceptWithFlow(BuildContext context, ChatController chat, ProposalEntry entry) async {
  try {
    await chat.accept(entry);
    return false;
  } on ApiException catch (e) {
    if (!context.mounted) return false;
    switch (e.code) {
      case 'base_changed':
        final choice = await showBaseChangedDialog(context, e.detail);
        if (!context.mounted || choice == null) return false;
        if (choice == BaseChoice.repropose) {
          chat.repropose(entry);
          return true;
        }
        try {
          await chat.accept(entry, overwrite: true);
        } on ApiException catch (e2) {
          if (context.mounted) showError(context, e2);
        }
        return false;
      case 'progress_changed':
        final again = await showProgressChangedDialog(context, e.detail);
        if (again == true) {
          chat.repropose(entry);
          return true;
        }
        return false;
      default:
        showError(context, e);
        return false;
    }
  }
}

Future<void> rejectWithFlow(BuildContext context, ChatController chat, ProposalEntry entry) async {
  try {
    await chat.reject(entry);
  } on ApiException catch (e) {
    if (context.mounted) showError(context, e);
  }
}

/// 36_Base_Changed (Figma 73:6261). since_proposal = 그대로 반영하면 사라지는 변경.
Future<BaseChoice?> showBaseChangedDialog(BuildContext context, Map<String, dynamic> detail) {
  List<Change> parse(Object? v) => [for (final c in (v as List?) ?? const []) Change.fromJson(Map<String, dynamic>.from(c as Map))];
  final since = parse(detail['since_proposal']);
  final effect = parse(detail['overwrite_effect']);
  return showDialog<BaseChoice>(
    context: context,
    builder: (ctx) => Dialog(
      insetPadding: const EdgeInsets.symmetric(horizontal: 28),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 334),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 22, 20, 12),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text('이 제안 이후 일정이 바뀌었어요', style: AppText.headline),
            const SizedBox(height: 6),
            Text('카드에서 본 일정 그대로 반영하면 아래 변경이 사라져요.', style: AppText.meta),
            const SizedBox(height: 14),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(color: AppColors.background, borderRadius: BorderRadius.circular(12)),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('사라지는 변경', style: AppText.caption.copyWith(fontWeight: FontWeight.w700, color: AppColors.text2)),
                const SizedBox(height: 6),
                if (since.isEmpty) Text('다른 곳에서 일정이 바뀌었어요', style: AppText.meta.copyWith(color: AppColors.text)),
                for (final c in since) _ChangeLine(change: c),
              ]),
            ),
            if (effect.isNotEmpty) _OverwriteEffect(changes: effect),
            const SizedBox(height: 14),
            AppButton(label: '현재 일정에 맞춰 다시 제안', icon: Icons.auto_awesome, kind: ButtonKind.ai, height: 48, onPressed: () => Navigator.pop(ctx, BaseChoice.repropose)),
            const SizedBox(height: 8),
            AppButton(label: '카드에서 본 일정 그대로 반영', kind: ButtonKind.secondary, height: 48, onPressed: () => Navigator.pop(ctx, BaseChoice.overwrite)),
            TextButton(onPressed: () => Navigator.pop(ctx), child: Text('취소', style: AppText.label.copyWith(color: AppColors.text2))),
          ]),
        ),
      ),
    ),
  );
}

class _ChangeLine extends StatelessWidget {
  const _ChangeLine({required this.change});
  final Change change;

  @override
  Widget build(BuildContext context) {
    final (label, fg, bg) = changeKindBadge(change.kind);
    final text = change.kind.startsWith('rule') ? ruleText(change.message) : stripChangePrefix(change.message);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
          decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(6)),
          child: Text(label, style: AppText.caption.copyWith(color: fg, fontWeight: FontWeight.w700)),
        ),
        const SizedBox(width: 8),
        Expanded(child: Text(text, style: AppText.meta.copyWith(color: AppColors.text))),
      ]),
    );
  }
}

/// "자세히": 그대로 반영하면 지금 일정에서 바뀌는 것 (overwrite_effect, P2 — 접힌 상태로 둔다).
class _OverwriteEffect extends StatefulWidget {
  const _OverwriteEffect({required this.changes});
  final List<Change> changes;

  @override
  State<_OverwriteEffect> createState() => _OverwriteEffectState();
}

class _OverwriteEffectState extends State<_OverwriteEffect> {
  bool _open = false;

  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        TextButton(
          style: TextButton.styleFrom(padding: EdgeInsets.zero, minimumSize: const Size(0, 32)),
          onPressed: () => setState(() => _open = !_open),
          child: Text(_open ? '자세히 접기' : '자세히 · 그대로 반영하면 바뀌는 것', style: AppText.caption.copyWith(color: AppColors.text2)),
        ),
        if (_open) for (final c in widget.changes) _ChangeLine(change: c),
      ]);
}

/// 409 progress_changed: 그사이 다녀온 곳이 있어 그대로 반영할 수 없음. 덮어쓰기 없이 다시 제안만.
Future<bool?> showProgressChangedDialog(BuildContext context, Map<String, dynamic> detail) {
  final items = [for (final i in (detail['items'] as List?) ?? const []) i as Map];
  return showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      title: Text('그사이 다녀온 곳이 있어요', style: AppText.headline),
      content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('이 제안을 그대로 반영할 수 없어요.', style: AppText.meta),
        for (final i in items) Padding(padding: const EdgeInsets.only(top: 6), child: Text('· ${i['title']} (${i['done_at'] ?? ''} 다녀옴)', style: AppText.meta.copyWith(color: AppColors.text))),
      ]),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('취소')),
        TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text('현재 상황에 맞춰 다시 제안', style: TextStyle(color: AppColors.ai, fontWeight: FontWeight.w700, fontFamily: appFontFamily))),
      ],
    ),
  );
}
