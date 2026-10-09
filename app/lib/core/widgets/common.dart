import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme/app_theme.dart';

/// 오류 → 사람이 읽을 문구 (README §6.3). 서버 code 가 있으면 code 로 고른다.
String userMessage(Object error) {
  if (error is! ApiException) return '$error';
  return switch (error.code) {
    'agent_busy' => '구룡메이트가 답하는 중이에요. 잠시 후 다시 눌러 주세요',
    'upstream_unavailable' || 'places_unavailable' || 'planner_unavailable' => '서버에 연결하지 못했어요. 일정은 그대로예요.',
    'safety_unavailable' => '안전 정보를 불러오지 못했어요. 일정은 그대로예요.',
    'network' || 'timeout' => error.message,
    'proposal_not_pending' => '이미 처리된 제안이에요',
    _ => error.status == 422 ? '요청을 처리하지 못했어요. 다시 시도해 주세요.' : error.message,
  };
}

/// 오류를 스낵바로 보여 준다. agent_busy 는 빨간색이 아닌 Dark 스낵바 (States_Feedback).
void showError(BuildContext context, Object error) {
  if (!context.mounted) return;
  final busy = error is ApiException && error.code == 'agent_busy';
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(
      content: Row(children: [
        if (busy) ...[const Icon(Icons.schedule, color: Colors.white, size: 18), const SizedBox(width: 8)],
        Expanded(child: Text(userMessage(error))),
      ]),
      backgroundColor: busy ? AppColors.dark : AppColors.danger,
    ));
}

/// [margin] 으로 띄울 위치를 정한다 (하단 버튼을 가리지 않게).
void showInfo(BuildContext context, String msg, {EdgeInsets? margin}) {
  if (!context.mounted) return;
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(msg), margin: margin));
}

/// AI FAB (56 원형, AI gradient).
class AiFab extends StatelessWidget {
  const AiFab({super.key, required this.onTap, this.size = 56});
  final VoidCallback onTap;
  final double size;

  @override
  Widget build(BuildContext context) => Tooltip(
        message: '구룡메이트 열기',
        // 그라데이션·그림자는 바깥 원에, 눌림 효과는 원으로 잘린 투명 Material 에 (Ink 는 사각 하이라이트가 비친다)
        child: DecoratedBox(
          decoration: const BoxDecoration(
            gradient: AppColors.aiGradient,
            shape: BoxShape.circle,
            boxShadow: [BoxShadow(color: Color(0x557B61FF), blurRadius: 18, offset: Offset(0, 6))],
          ),
          child: Material(
            type: MaterialType.transparency,
            shape: const CircleBorder(),
            clipBehavior: Clip.antiAlias,
            child: InkWell(
              customBorder: const CircleBorder(),
              onTap: onTap,
              child: SizedBox(width: size, height: size, child: const Icon(Icons.auto_awesome, color: Colors.white, size: 26)),
            ),
          ),
        ),
      );
}

/// AI 아바타 (AI Soft 원 + 반짝임).
class AiAvatar extends StatelessWidget {
  const AiAvatar({super.key, this.size = 32});
  final double size;

  @override
  Widget build(BuildContext context) => Container(
        width: size,
        height: size,
        decoration: const BoxDecoration(color: AppColors.aiSoft, shape: BoxShape.circle),
        child: Icon(Icons.auto_awesome, color: AppColors.ai, size: size * 0.5),
      );
}

/// 높이 24 둥근 칩 (날씨·혼잡도·경고 등).
class InfoChip extends StatelessWidget {
  const InfoChip({super.key, required this.label, this.icon, this.fg = AppColors.text2, this.bg = AppColors.field});
  final String label;
  final IconData? icon;
  final Color fg;
  final Color bg;

  factory InfoChip.warning(String label, {IconData icon = Icons.error_outline}) =>
      InfoChip(label: label, icon: icon, fg: AppColors.warning, bg: AppColors.warningSoft);
  factory InfoChip.error(String label, {IconData icon = Icons.error_outline}) =>
      InfoChip(label: label, icon: icon, fg: AppColors.danger, bg: AppColors.dangerSoft);
  factory InfoChip.success(String label, {IconData? icon}) =>
      InfoChip(label: label, icon: icon, fg: AppColors.success, bg: AppColors.successSoft);

  @override
  Widget build(BuildContext context) => Container(
        height: 24,
        padding: const EdgeInsets.symmetric(horizontal: 8),
        decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(12)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (icon != null) ...[Icon(icon, size: 14, color: fg), const SizedBox(width: 4)],
          Text(label, style: AppText.caption.copyWith(color: fg, fontWeight: FontWeight.w500)),
        ]),
      );
}

/// 큰 버튼 (h52 r12). primary / secondary(흰 바탕+테두리) / soft(Primary Soft).
enum ButtonKind { primary, secondary, soft, danger, ai, aiSoft }

class AppButton extends StatelessWidget {
  const AppButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.kind = ButtonKind.primary,
    this.icon,
    this.height = 52,
    this.loading = false,
    this.leading,
  });

  final String label;
  final VoidCallback? onPressed;
  final ButtonKind kind;
  final IconData? icon;
  final Widget? leading;
  final double height;
  final bool loading;

  @override
  Widget build(BuildContext context) {
    final (bg, fg, border) = switch (kind) {
      ButtonKind.primary => (AppColors.primary, Colors.white, null),
      ButtonKind.secondary => (AppColors.surface, AppColors.text, AppColors.line),
      ButtonKind.soft => (AppColors.primarySoft, AppColors.primary, null),
      ButtonKind.danger => (AppColors.surface, AppColors.danger, AppColors.line),
      ButtonKind.ai => (AppColors.ai, Colors.white, null),
      ButtonKind.aiSoft => (AppColors.aiSoft, AppColors.ai, null),
    };
    final disabled = onPressed == null || loading;
    return SizedBox(
      height: height,
      child: TextButton(
        onPressed: disabled ? null : onPressed,
        style: TextButton.styleFrom(
          backgroundColor: disabled && (kind == ButtonKind.primary || kind == ButtonKind.ai) ? bg.withValues(alpha: 0.4) : bg,
          foregroundColor: fg,
          disabledForegroundColor: kind == ButtonKind.primary || kind == ButtonKind.ai ? Colors.white : AppColors.text3,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
            side: border == null ? BorderSide.none : BorderSide(color: border),
          ),
          textStyle: AppText.section.copyWith(fontSize: height < 48 ? 14 : 16),
        ),
        child: loading
            ? SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: fg))
            : Row(mainAxisSize: MainAxisSize.min, children: [
                if (leading != null) ...[leading!, const SizedBox(width: 8)],
                if (icon != null) ...[Icon(icon, size: 18), const SizedBox(width: 6)],
                Flexible(child: Text(label, overflow: TextOverflow.ellipsis)),
              ]),
      ),
    );
  }
}

/// 바텀시트 상단 grabber (36×5).
class Grabber extends StatelessWidget {
  const Grabber({super.key});

  @override
  Widget build(BuildContext context) => Center(
        child: Container(
          width: 36,
          height: 5,
          margin: const EdgeInsets.only(top: 8, bottom: 8),
          decoration: BoxDecoration(color: AppColors.grabber, borderRadius: BorderRadius.circular(3)),
        ),
      );
}

/// 흰 카드 (r16, 그림자).
class AppCard extends StatelessWidget {
  const AppCard({super.key, required this.child, this.padding = const EdgeInsets.all(16), this.onTap, this.onLongPress, this.border, this.color = AppColors.surface, this.radius = 16});
  final Widget child;
  final EdgeInsets padding;
  final VoidCallback? onTap;
  final VoidCallback? onLongPress;
  final BorderSide? border;
  final Color color;
  final double radius;

  @override
  Widget build(BuildContext context) => Container(
        decoration: BoxDecoration(borderRadius: BorderRadius.circular(radius), boxShadow: AppShadow.card),
        child: Material(
          color: color,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(radius), side: border ?? BorderSide.none),
          clipBehavior: Clip.antiAlias,
          child: InkWell(onTap: onTap, onLongPress: onLongPress, child: Padding(padding: padding, child: child)),
        ),
      );
}

/// 장소 분류별 아이콘·색 (썸네일 대체).
(IconData, Color, Color) placeVisual(String contentType) => switch (contentType) {
      '음식점' => (Icons.restaurant, AppColors.warning, const Color(0xFFFDEEE3)),
      '숙박' => (Icons.hotel_outlined, AppColors.ai, AppColors.aiSoft),
      '문화시설' => (Icons.account_balance_outlined, AppColors.primary, AppColors.primarySoft),
      '쇼핑' => (Icons.shopping_bag_outlined, AppColors.danger, AppColors.dangerSoft),
      '레포츠' => (Icons.directions_bike, AppColors.success, AppColors.successSoft),
      '축제공연행사' => (Icons.celebration_outlined, AppColors.ai, AppColors.aiSoft),
      _ => (Icons.landscape_outlined, AppColors.success, AppColors.successSoft),
    };

/// 썸네일: 이미지가 있으면 이미지, 없으면 분류 아이콘.
class PlaceThumb extends StatelessWidget {
  const PlaceThumb({super.key, required this.contentType, this.url = '', this.size = 52, this.radius = 12});
  final String contentType;
  final String url;
  final double size;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final (icon, fg, bg) = placeVisual(contentType);
    final fallback = Container(
      width: size,
      height: size,
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(radius)),
      child: Icon(icon, color: fg, size: size * 0.42),
    );
    if (url.isEmpty) return fallback;
    return ClipRRect(
      borderRadius: BorderRadius.circular(radius),
      child: Image.network(url, width: size, height: size, fit: BoxFit.cover, errorBuilder: (_, _, _) => fallback),
    );
  }
}

/// 로딩 / 오류 / 빈 상태 공통.
class StateView extends StatelessWidget {
  const StateView({super.key, this.icon = Icons.cloud_off_outlined, required this.title, this.message, this.actionLabel, this.onAction});
  final IconData icon;
  final String title;
  final String? message;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(icon, size: 40, color: AppColors.text3),
            const SizedBox(height: 12),
            Text(title, style: AppText.section, textAlign: TextAlign.center),
            if (message != null) ...[const SizedBox(height: 6), Text(message!, style: AppText.meta, textAlign: TextAlign.center)],
            if (actionLabel != null) ...[
              const SizedBox(height: 16),
              AppButton(label: actionLabel!, onPressed: onAction, kind: ButtonKind.soft, height: 44),
            ],
          ]),
        ),
      );
}
