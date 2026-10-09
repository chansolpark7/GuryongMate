// account · docent 응답 (docs/account §2, docs/docent §5). trip 모델은 trip.dart (예전 경로로도 쓸 수 있게 다시 내보낸다).
export 'trip.dart';

class UserView {
  UserView({required this.id, required this.provider, required this.loginId, required this.email, required this.displayName});
  final String id;
  final String provider; // password | google
  final String? loginId;
  final String? email;
  final String displayName;

  factory UserView.fromJson(Map<String, dynamic> j) => UserView(
        id: j['id'] as String,
        provider: j['provider'] as String? ?? 'password',
        loginId: j['login_id'] as String?,
        email: j['email'] as String?,
        displayName: j['display_name'] as String? ?? '',
      );

  Map<String, dynamic> toJson() => {'id': id, 'provider': provider, 'login_id': loginId, 'email': email, 'display_name': displayName};
}

class TokenResponse {
  TokenResponse({required this.accessToken, required this.refreshToken, required this.user, required this.created});
  final String accessToken;
  final String refreshToken;
  final UserView user;
  final bool created;

  factory TokenResponse.fromJson(Map<String, dynamic> j) => TokenResponse(
        accessToken: j['access_token'] as String,
        refreshToken: j['refresh_token'] as String,
        user: UserView.fromJson(j['user'] as Map<String, dynamic>),
        created: j['created'] as bool? ?? false,
      );
}

/// docent 메모리 한 줄 (31).
class MemoryItem {
  MemoryItem({required this.id, required this.scope, required this.planId, required this.category, required this.text, required this.source, required this.createdAt});
  final int id;
  final String scope; // user | plan
  final int? planId;
  final String category; // interest · style · companion · food · avoid · known · feedback
  final String text;
  final String source;
  final DateTime createdAt;

  factory MemoryItem.fromJson(Map<String, dynamic> j) => MemoryItem(
        id: j['id'] as int,
        scope: j['scope'] as String? ?? 'user',
        planId: j['plan_id'] as int?,
        category: j['category'] as String? ?? 'interest',
        text: j['text'] as String? ?? '',
        source: j['source'] as String? ?? '',
        createdAt: DateTime.parse(j['created_at'] as String).toLocal(),
      );
}

class MemoryList {
  MemoryList({required this.items, required this.memoryVersion, required this.styleLength});
  final List<MemoryItem> items;
  final int memoryVersion;
  final String? styleLength;

  factory MemoryList.fromJson(Map<String, dynamic> j) => MemoryList(
        items: ((j['items'] as List?) ?? const []).map((e) => MemoryItem.fromJson(e as Map<String, dynamic>)).toList(),
        memoryVersion: j['memory_version'] as int? ?? 0,
        styleLength: j['style_length'] as String?,
      );
}

/// 가이드 노트 목록 한 줄 (32). 장소 이름은 일정에서 찾는다.
class GuideNoteSummary {
  GuideNoteSummary({required this.placeId, required this.status});
  final String placeId;
  final String status; // pending | preparing | ready | failed | stale

  factory GuideNoteSummary.fromJson(Map<String, dynamic> j) =>
      GuideNoteSummary(placeId: j['place_id'] as String, status: j['status'] as String? ?? 'pending');
}
