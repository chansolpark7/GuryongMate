// trip 서비스 응답 모델 (docs/trip/README.md §1.3 TripState, §2.1 LocationResult, §3.3 Event)
// 와 docent 가이드 노트 (docs/docent/README.md §3.1).
//
// 시각: trip 은 "2026-10-10T14:20:00+09:00" 처럼 계획 지역(KST) 시각을 준다. 화면은 그 벽시계를 그대로 그려야 하므로
// (가상 시각이면 그 시각) 기기 시간대로 바꾸지 않고 앞 19자리를 시간대 없는 DateTime 으로 읽는다 → [wallClock].

/// "2026-10-10T14:20:00.123+09:00" → 2026-10-10 14:20:00 (시간대 없이, 적힌 벽시계 그대로).
DateTime? wallClock(Object? iso) {
  if (iso is! String || iso.length < 16) return null;
  final s = iso.length >= 19 ? iso.substring(0, 19) : iso.substring(0, 16);
  return DateTime.tryParse(s);
}

String _two(int n) => n.toString().padLeft(2, '0');

/// 벽시계 → "HH:MM".
String clockText(DateTime t) => '${_two(t.hour)}:${_two(t.minute)}';

class TripLocation {
  TripLocation({required this.lat, required this.lng, this.accuracy, this.recordedAt, this.source = 'app'});
  final double lat;
  final double lng;
  final double? accuracy;
  final DateTime? recordedAt; // 벽시계
  final String source; // app | cli | replay | sim

  factory TripLocation.fromJson(Map<String, dynamic> j) => TripLocation(
        lat: (j['lat'] as num).toDouble(),
        lng: (j['lng'] as num).toDouble(),
        accuracy: (j['accuracy'] as num?)?.toDouble(),
        recordedAt: wallClock(j['recorded_at']),
        source: j['source'] as String? ?? 'app',
      );
}

/// 재계산한 예상 시각 (TripState.expected, audit-2026-10-12 D2). 오늘 남은 항목(planned·visiting)의 시작·종료와 그날 종료, "HH:MM".
class TripExpected {
  TripExpected({this.end, this.items = const {}});
  final String? end;
  final Map<int, ({String? start, String? end})> items;

  factory TripExpected.fromJson(Map<String, dynamic> j) => TripExpected(
        end: j['end'] as String?,
        items: {
          for (final e in ((j['items'] as Map?) ?? const {}).entries)
            if (int.tryParse('${e.key}') != null && e.value is Map)
              int.parse('${e.key}'): (start: (e.value as Map)['start'] as String?, end: (e.value as Map)['end'] as String?),
        },
      );
}

/// trip 여행 상태. 모드는 스위치가 아니라 이 상태다: active 면 동행 모드 (trip §1.1).
class TripState {
  TripState({
    required this.active,
    this.planId,
    this.day,
    this.date,
    this.startedAt,
    this.endedAt,
    this.endedReason,
    this.now,
    this.clock = 'system',
    this.location,
    this.currentItemId,
    this.currentState,
    this.currentSince,
    this.nextItemId,
    this.nextTitle,
    this.nextPlannedStart,
    this.nextEta,
    this.delayMinutes,
    this.expected,
    this.sessionId,
  });

  final int? planId;
  final bool active;
  final int? day;
  final DateTime? date;
  final DateTime? startedAt; // 벽시계
  final DateTime? endedAt;
  final String? endedReason; // user | auto
  final DateTime? now; // 서버의 지금 (가상 시각이면 그 시각)
  final String clock; // system | sim
  final TripLocation? location;

  final int? currentItemId;
  final String? currentState; // visiting | moving | idle
  final String? currentSince; // "HH:MM"

  final int? nextItemId;
  final String? nextTitle;
  final String? nextPlannedStart; // "HH:MM"
  final String? nextEta; // "HH:MM"

  /// 다음 일정 기준 예상 지연 (음수 = 여유).
  final int? delayMinutes;

  /// 마지막 재계산의 예상 시각. 재계산 전·진행 중이 아님·옛 서버면 null.
  final TripExpected? expected;
  final String? sessionId;

  bool get visiting => currentState == 'visiting';
  bool get moving => currentState == 'moving';
  bool get idle => currentState == null || currentState == 'idle';

  factory TripState.fromJson(Map<String, dynamic> j) {
    final cur = j['current'] as Map?;
    final next = j['next'] as Map?;
    return TripState(
      planId: j['plan_id'] as int?,
      active: j['active'] as bool? ?? false,
      day: j['day'] as int?,
      date: j['date'] == null ? null : DateTime.tryParse(j['date'] as String),
      startedAt: wallClock(j['started_at']),
      endedAt: wallClock(j['ended_at']),
      endedReason: j['ended_reason'] as String?,
      now: wallClock(j['now']),
      clock: j['clock'] as String? ?? 'system',
      location: j['location'] is Map ? TripLocation.fromJson(Map<String, dynamic>.from(j['location'] as Map)) : null,
      currentItemId: cur?['item_id'] as int?,
      currentState: cur?['state'] as String?,
      currentSince: cur?['since'] as String?,
      nextItemId: next?['item_id'] as int?,
      nextTitle: next?['title'] as String?,
      nextPlannedStart: next?['planned_start'] as String?,
      nextEta: next?['eta'] as String?,
      delayMinutes: j['delay_minutes'] as int?,
      expected: j['expected'] is Map ? TripExpected.fromJson(Map<String, dynamic>.from(j['expected'] as Map)) : null,
      sessionId: j['session_id'] as String?,
    );
  }
}

/// 단계 3 이벤트의 Agent 선제 턴 결과.
class EventAgent {
  EventAgent({required this.sessionId, required this.messageId, required this.proposalId});
  final String? sessionId;
  final int? messageId;
  final int? proposalId;

  factory EventAgent.fromJson(Map<String, dynamic> j) =>
      EventAgent(sessionId: j['session_id'] as String?, messageId: j['message_id'] as int?, proposalId: j['proposal_id'] as int?);
}

/// 단계 2 카드 (도착 해설).
class EventCard {
  EventCard({required this.kind, required this.placeId, required this.itemId});
  final String kind; // docent
  final String? placeId;
  final int? itemId;

  factory EventCard.fromJson(Map<String, dynamic> j) => EventCard(kind: j['kind'] as String? ?? '', placeId: j['place_id'] as String?, itemId: j['item_id'] as int?);
}

/// trip 이벤트 (§3.3). `message` 는 서버의 고정 문구라 앱은 그대로 쓴다.
class TripEvent {
  TripEvent({
    required this.id,
    required this.type,
    required this.level,
    required this.severity,
    required this.message,
    required this.status,
    this.planId,
    this.day,
    this.now,
    this.itemIds = const [],
    this.payload = const {},
    this.agent,
    this.card,
    this.seenAt,
    this.createdAt,
  });

  final int id;
  final int? planId;
  final int? day;
  final String type; // arrived · departed · delay · safety_raised · safety_eased · near_hazard · day_started · day_ended
  final int level;
  final String severity; // info | warning | danger
  final DateTime? now; // 판정 시각 (벽시계)
  final List<int> itemIds;
  final String message;
  final Map<String, dynamic> payload;
  final String status; // queued | delivered | suppressed | handled | failed
  final EventAgent? agent;
  final EventCard? card;
  final DateTime? seenAt;

  /// 서버에 기록된 실제 시각 (가상 시각 아님, 시간대 있는 순간). 여행일을 실제로 시작한 때(day_started)를 알 때 쓴다.
  final DateTime? createdAt;

  bool get proactive => type == 'delay' || type == 'safety_raised';
  bool get suppressed => status == 'suppressed';
  bool get failed => status == 'failed';

  /// 선제 턴이 끝나 대화에 메시지가 생겼는지.
  bool get agentReady => agent?.messageId != null || agent?.proposalId != null;

  /// 단계 3 인데 아직 Agent 결과가 없다 (queued) → 2초 폴링.
  bool get waitingAgent => proactive && !agentReady && !suppressed && !failed;

  String get timeText => now == null ? '' : clockText(now!);

  /// near_hazard 의 구역 (`payload.zones`, gaps b2).
  List<HazardZoneRef> get zones => [
        for (final z in (payload['zones'] as List?) ?? const [])
          if (z is Map) HazardZoneRef.fromJson(Map<String, dynamic>.from(z)),
      ];

  factory TripEvent.fromJson(Map<String, dynamic> j) => TripEvent(
        id: j['id'] as int,
        planId: j['plan_id'] as int?,
        day: j['day'] as int?,
        type: j['type'] as String? ?? '',
        level: j['level'] as int? ?? 0,
        severity: j['severity'] as String? ?? 'info',
        now: wallClock(j['now']),
        itemIds: ((j['item_ids'] as List?) ?? const []).whereType<int>().toList(),
        message: j['message'] as String? ?? '',
        payload: j['payload'] is Map ? Map<String, dynamic>.from(j['payload'] as Map) : const {},
        status: j['status'] as String? ?? 'delivered',
        agent: j['agent'] is Map ? EventAgent.fromJson(Map<String, dynamic>.from(j['agent'] as Map)) : null,
        card: j['card'] is Map ? EventCard.fromJson(Map<String, dynamic>.from(j['card'] as Map)) : null,
        seenAt: wallClock(j['seen_at']),
        createdAt: j['created_at'] is String ? DateTime.tryParse(j['created_at'] as String) : null,
      );
}

class HazardZoneRef {
  HazardZoneRef({required this.id, required this.name, required this.kind, required this.risk, required this.reason, this.distanceM});
  final String id;
  final String name;
  final String kind; // flood | breakwater
  final String risk;
  final String reason;
  final num? distanceM;

  factory HazardZoneRef.fromJson(Map<String, dynamic> j) => HazardZoneRef(
        id: '${j['id'] ?? ''}',
        name: j['name'] as String? ?? '',
        kind: j['kind'] as String? ?? '',
        risk: j['risk'] as String? ?? 'caution',
        reason: j['reason'] as String? ?? '',
        distanceM: j['distance_m'] as num?,
      );
}

/// POST /trip/plans/{id}/location 응답.
class LocationResult {
  LocationResult({required this.state, required this.events, required this.accepted, required this.ignored});
  final TripState state;
  final List<TripEvent> events;
  final int accepted;
  final int ignored;

  factory LocationResult.fromJson(Map<String, dynamic> j) => LocationResult(
        state: TripState.fromJson(Map<String, dynamic>.from(j['state'] as Map)),
        events: [for (final e in (j['events'] as List?) ?? const []) TripEvent.fromJson(Map<String, dynamic>.from(e as Map))],
        accepted: j['accepted'] as int? ?? 0,
        ignored: j['ignored'] as int? ?? 0,
      );
}

/// 앱이 보내는 위치 한 점 (LocationIn).
class LocationPoint {
  LocationPoint({required this.lat, required this.lng, this.accuracy, required this.recordedAt, this.source = 'app'});
  final double lat;
  final double lng;
  final double? accuracy;

  /// 서버 기준 벽시계 (가상 시각이면 그 시각에 맞춘 값). KST 로 보낸다.
  final DateTime recordedAt;
  final String source;

  Map<String, dynamic> toJson() => {
        'lat': lat,
        'lng': lng,
        if (accuracy != null) 'accuracy': double.parse(accuracy!.toStringAsFixed(1)),
        'recorded_at': '${recordedAt.toIso8601String().substring(0, 19)}+09:00',
        'source': source,
      };
}

/// 가이드 노트 (docent §3.1). 빈 배열 섹션은 화면에서 숨긴다.
class GuideNote {
  GuideNote({
    required this.placeId,
    required this.status,
    required this.short,
    required this.long,
    required this.forYou,
    required this.highlights,
    required this.companionTips,
    required this.cautions,
    required this.qa,
    required this.sources,
    required this.preparedAt,
  });

  final String placeId;
  final String status; // pending | preparing | ready | failed | stale
  final String short;
  final String long;
  final List<String> forYou;
  final List<String> highlights;
  final List<String> companionTips;
  final List<String> cautions;
  final List<(String, String)> qa; // (q, a)
  final List<NoteSource> sources;
  final DateTime? preparedAt;

  bool get ready => status == 'ready' || status == 'stale';
  bool get preparing => status == 'pending' || status == 'preparing';

  static List<String> _strs(Object? v) => [for (final e in (v as List?) ?? const []) if ('$e'.trim().isNotEmpty) '$e'];

  factory GuideNote.fromJson(Map<String, dynamic> j) => GuideNote(
        placeId: j['place_id'] as String? ?? '',
        status: j['status'] as String? ?? 'ready',
        short: j['short'] as String? ?? '',
        long: j['long'] as String? ?? '',
        forYou: _strs(j['for_you']),
        highlights: _strs(j['highlights']),
        companionTips: _strs(j['companion_tips']),
        cautions: _strs(j['cautions']),
        qa: [
          for (final e in (j['qa'] as List?) ?? const [])
            if (e is Map) ('${e['q'] ?? ''}', '${e['a'] ?? ''}'),
        ],
        sources: [
          for (final e in (j['sources'] as List?) ?? const [])
            if (e is Map) NoteSource.fromJson(Map<String, dynamic>.from(e)),
        ],
        preparedAt: j['prepared_at'] == null ? null : DateTime.tryParse(j['prepared_at'] as String)?.toLocal(),
      );
}

class NoteSource {
  NoteSource({required this.title, required this.url, required this.tier});
  final String title;
  final String? url;
  final String tier; // official | press | community | unknown

  factory NoteSource.fromJson(Map<String, dynamic> j) =>
      NoteSource(title: j['title'] as String? ?? j['name'] as String? ?? '', url: j['url'] as String?, tier: j['tier'] as String? ?? 'unknown');
}
