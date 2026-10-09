// planner 서비스 응답 모델 (docs/planner/README.md §2.3, backend/app/planner/schemas.py).
// 시각은 "HH:MM" 문자열 그대로 둔다. 자정을 넘으면 "24:30" 처럼 올 수 있다.

List<T> _list<T>(Object? v, T Function(Map<String, dynamic>) f) =>
    ((v as List?) ?? const []).map((e) => f(e as Map<String, dynamic>)).toList();

DateTime _date(Object? v) => DateTime.parse(v as String);

class PlaceInfo {
  PlaceInfo({required this.id, required this.title, required this.contentType, required this.lat, required this.lng, required this.addr, required this.openHours, required this.restDate});
  final String id;
  final String title;
  final String contentType;
  final double lat;
  final double lng;
  final String addr;
  final String openHours;
  final String restDate;

  factory PlaceInfo.fromJson(Map<String, dynamic> j) => PlaceInfo(
        id: j['id'] as String,
        title: j['title'] as String? ?? '',
        contentType: j['content_type'] as String? ?? '',
        lat: (j['lat'] as num).toDouble(),
        lng: (j['lng'] as num).toDouble(),
        addr: j['addr'] as String? ?? '',
        openHours: j['open_hours'] as String? ?? '',
        restDate: j['rest_date'] as String? ?? '',
      );
}

class Violation {
  Violation({required this.code, required this.severity, required this.message, this.day, this.itemId, this.constraintId});
  final String code;
  final String severity; // error | warning
  final String message;
  final int? day;
  final int? itemId;
  final int? constraintId;

  bool get isError => severity == 'error';

  factory Violation.fromJson(Map<String, dynamic> j) => Violation(
        code: j['code'] as String,
        severity: j['severity'] as String,
        message: j['message'] as String? ?? '',
        day: j['day'] as int?,
        itemId: j['item_id'] as int?,
        constraintId: j['constraint_id'] as int?,
      );

  /// 칩에 쓰는 짧은 이름.
  String get shortLabel => switch (code) {
        'day_overflow' => '종료 시각 초과',
        'fixed_time_missed' => '고정 시각 늦음',
        'closed_day' => '휴무일',
        'outside_open_hours' => '운영시간 밖',
        'place_unavailable' => '장소 정보 없음',
        'must_include' => '필수 장소 누락',
        'exclude' => '제외 장소 포함',
        'end_before' => '종료 조건 위반',
        'max_travel_minutes' => '이동 시간 초과',
        'max_walk_minutes' || 'long_walk' => '긴 도보 구간',
        'max_daily_walk_minutes' || 'daily_walk_total' => '도보 합계 초과',
        'travel_estimated' => '이동 시간 추정',
        _ => '확인 필요',
      };
}

/// 같은 구간의 도보·차량 비교 (`travel_options`).
class TravelOption {
  TravelOption({required this.minutes, required this.distanceM, required this.source});
  final int minutes;
  final int? distanceM;
  final String source; // road | estimate

  bool get estimated => source == 'estimate';

  factory TravelOption.fromJson(Map<String, dynamic> j) =>
      TravelOption(minutes: j['minutes'] as int, distanceM: j['distance_m'] as int?, source: j['source'] as String? ?? 'road');
}

class PlanItem {
  PlanItem({
    required this.id,
    required this.day,
    required this.order,
    required this.placeId,
    required this.place,
    required this.stayMinutes,
    required this.startTime,
    required this.memo,
    required this.status,
    required this.travelMinutes,
    required this.travelDistanceM,
    required this.arriveAt,
    required this.startAt,
    required this.endAt,
    this.travelMode,
    this.legMode,
    this.travelSource,
    this.travelOptions = const {},
    this.arrivedAt,
    this.doneAt,
  });

  final int id;
  final int day;
  final int order;
  final String placeId;
  final PlaceInfo? place;
  final int stayMinutes;
  final String? startTime; // 고정 시각
  final String memo;
  final String status; // planned | visiting | done | skipped
  final int? travelMinutes;
  final int? travelDistanceM;
  final String? arriveAt;
  final String? startAt;
  final String? endAt;

  /// 이 항목에 정한 '여기까지 오는 수단'. null = 계획 기본.
  final String? travelMode;

  /// 실제로 계산한 수단 (첫 항목·건너뜀은 null).
  final String? legMode;
  final String? travelSource; // road | estimate
  final Map<String, TravelOption> travelOptions; // walk / car
  final String? arrivedAt;
  final String? doneAt;

  String get title => place?.title ?? placeId;
  bool get skipped => status == 'skipped';
  bool get estimated => travelSource == 'estimate';

  factory PlanItem.fromJson(Map<String, dynamic> j) => PlanItem(
        id: j['id'] as int,
        day: j['day'] as int,
        order: j['order'] as int,
        placeId: j['place_id'] as String,
        place: j['place'] == null ? null : PlaceInfo.fromJson(j['place'] as Map<String, dynamic>),
        stayMinutes: j['stay_minutes'] as int,
        startTime: j['start_time'] as String?,
        memo: j['memo'] as String? ?? '',
        status: j['status'] as String? ?? 'planned',
        travelMinutes: j['travel_minutes'] as int?,
        travelDistanceM: j['travel_distance_m'] as int?,
        arriveAt: j['arrive_at'] as String?,
        startAt: j['start_at'] as String?,
        endAt: j['end_at'] as String?,
        travelMode: j['travel_mode'] as String?,
        legMode: j['leg_mode'] as String?,
        travelSource: j['travel_source'] as String?,
        travelOptions: {
          for (final e in ((j['travel_options'] as Map?) ?? const {}).entries)
            if (e.value != null) '${e.key}': TravelOption.fromJson(Map<String, dynamic>.from(e.value as Map)),
        },
        arrivedAt: j['arrived_at'] as String?,
        doneAt: j['done_at'] as String?,
      );
}

class PlanDay {
  PlanDay({required this.day, required this.date, required this.weekday, required this.endAt, required this.items});
  final int day;
  final DateTime date;
  final String weekday;
  final String? endAt;
  final List<PlanItem> items;

  factory PlanDay.fromJson(Map<String, dynamic> j) => PlanDay(
        day: j['day'] as int,
        date: _date(j['date']),
        weekday: j['weekday'] as String? ?? '',
        endAt: j['end_at'] as String?,
        items: _list(j['items'], PlanItem.fromJson),
      );

  int get travelMinutes => items.fold(0, (s, i) => s + (i.skipped ? 0 : (i.travelMinutes ?? 0)));
}

class Constraint {
  Constraint({required this.id, required this.type, required this.params, required this.hard, required this.source, required this.note});
  final int id;
  final String type;
  final Map<String, dynamic> params;
  final bool hard;
  final String source;
  final String note;

  factory Constraint.fromJson(Map<String, dynamic> j) => Constraint(
        id: j['id'] as int,
        type: j['type'] as String,
        params: Map<String, dynamic>.from(j['params'] as Map? ?? const {}),
        hard: j['hard'] as bool? ?? true,
        source: j['source'] as String? ?? 'user',
        note: j['note'] as String? ?? '',
      );
}

class PlanSummary {
  PlanSummary({
    required this.id,
    required this.title,
    required this.startDate,
    required this.endDate,
    required this.dayStart,
    required this.dayEnd,
    required this.travelMode,
    required this.memo,
    required this.createdAt,
    required this.updatedAt,
    required this.itemCount,
    this.version = 0,
    this.progressVersion = 0,
  });

  final int id;
  final String title;
  final DateTime startDate;
  final DateTime endDate;
  final String dayStart;
  final String dayEnd;
  final String travelMode; // walk | car
  final String memo;
  final DateTime createdAt;
  final DateTime updatedAt;
  final int itemCount;

  /// 일정을 바꾸면 오른다 (planner §2.2).
  final int version;

  /// 진행 상태(도착·완료)만 바뀌면 오른다. 화면 갱신용.
  final int progressVersion;

  int get numDays => endDate.difference(startDate).inDays + 1;

  factory PlanSummary.fromJson(Map<String, dynamic> j) => PlanSummary(
        id: j['id'] as int,
        title: j['title'] as String,
        startDate: _date(j['start_date']),
        endDate: _date(j['end_date']),
        dayStart: j['day_start'] as String,
        dayEnd: j['day_end'] as String,
        travelMode: j['travel_mode'] as String? ?? 'walk',
        memo: j['memo'] as String? ?? '',
        createdAt: _date(j['created_at']).toLocal(),
        updatedAt: _date(j['updated_at']).toLocal(),
        itemCount: j['item_count'] as int? ?? 0,
        version: j['version'] as int? ?? 0,
        progressVersion: j['progress_version'] as int? ?? 0,
      );
}

class PlanDetail extends PlanSummary {
  PlanDetail({
    required super.id,
    required super.title,
    required super.startDate,
    required super.endDate,
    required super.dayStart,
    required super.dayEnd,
    required super.travelMode,
    required super.memo,
    required super.createdAt,
    required super.updatedAt,
    required super.itemCount,
    super.version,
    super.progressVersion,
    required this.days,
    required this.constraints,
    required this.violations,
  });

  final List<PlanDay> days;
  final List<Constraint> constraints;
  final List<Violation> violations;

  factory PlanDetail.fromJson(Map<String, dynamic> j) {
    final s = PlanSummary.fromJson(j);
    return PlanDetail(
      id: s.id,
      title: s.title,
      startDate: s.startDate,
      endDate: s.endDate,
      dayStart: s.dayStart,
      dayEnd: s.dayEnd,
      travelMode: s.travelMode,
      memo: s.memo,
      createdAt: s.createdAt,
      updatedAt: s.updatedAt,
      itemCount: s.itemCount,
      version: s.version,
      progressVersion: s.progressVersion,
      days: _list(j['days'], PlanDay.fromJson),
      constraints: _list(j['constraints'], Constraint.fromJson),
      violations: _list(j['violations'], Violation.fromJson),
    );
  }

  /// 같은 계획에서 Day 하나의 항목만 바꾼 사본 (드래그 중 로컬 반영).
  PlanDetail withDay(PlanDay day) => PlanDetail(
        id: id,
        title: title,
        startDate: startDate,
        endDate: endDate,
        dayStart: dayStart,
        dayEnd: dayEnd,
        travelMode: travelMode,
        memo: memo,
        createdAt: createdAt,
        updatedAt: updatedAt,
        itemCount: itemCount,
        version: version,
        progressVersion: progressVersion,
        days: days.map((d) => d.day == day.day ? day : d).toList(),
        constraints: constraints,
        violations: violations,
      );

  Iterable<PlanItem> get allItems => days.expand((d) => d.items);

  PlanDay? dayOf(int day) {
    for (final d in days) {
      if (d.day == day) return d;
    }
    return null;
  }

  PlanItem? item(int id) {
    for (final i in allItems) {
      if (i.id == id) return i;
    }
    return null;
  }

  List<Violation> violationsFor(int itemId) => violations.where((v) => v.itemId == itemId).toList();
}

/// 제안 카드의 변경 요약 한 줄 (planner Change).
class Change {
  Change({required this.kind, required this.message, this.itemId, this.day});
  final String kind; // added | removed | moved | updated | plan | rule_added | rule_removed | day_end
  final String message;
  final int? itemId;
  final int? day;

  factory Change.fromJson(Map<String, dynamic> j) =>
      Change(kind: j['kind'] as String, message: j['message'] as String? ?? '', itemId: j['item_id'] as int?, day: j['day'] as int?);
}

class ProposalDetail {
  ProposalDetail({
    required this.id,
    required this.planId,
    required this.status,
    required this.summary,
    required this.source,
    required this.ops,
    required this.createdAt,
    required this.changes,
    required this.before,
    required this.after,
    this.decision,
    this.baseChanged = false,
    this.progressChanged = const [],
  });

  final int id;
  final int planId;
  final String status; // pending | accepted | rejected | superseded
  final String summary;
  final String source; // llm | user | event
  final List<Map<String, dynamic>> ops;
  final DateTime createdAt;
  final List<Change> changes;
  final PlanDetail? before;
  final PlanDetail? after;
  final String? decision; // applied | overwritten

  /// 제안 이후 일정이 바뀜 → [전체 적용] 이 409 base_changed (36).
  final bool baseChanged;

  /// 제안 이후 다녀온(done) 항목 중 이 제안이 바꾸는 것 → 승인 409 progress_changed.
  final List<int> progressChanged;

  bool get pending => status == 'pending';

  factory ProposalDetail.fromJson(Map<String, dynamic> j) => ProposalDetail(
        id: j['id'] as int,
        planId: j['plan_id'] as int,
        status: j['status'] as String,
        summary: j['summary'] as String? ?? '',
        source: j['source'] as String? ?? '',
        ops: ((j['ops'] as List?) ?? const []).map((e) => Map<String, dynamic>.from(e as Map)).toList(),
        createdAt: _date(j['created_at']).toLocal(),
        changes: _list(j['changes'], Change.fromJson),
        before: j['before'] == null ? null : PlanDetail.fromJson(j['before'] as Map<String, dynamic>),
        after: j['after'] == null ? null : PlanDetail.fromJson(j['after'] as Map<String, dynamic>),
        decision: j['decision'] as String?,
        baseChanged: j['base_changed'] as bool? ?? false,
        progressChanged: ((j['progress_changed'] as List?) ?? const []).map((e) => e as int).toList(),
      );

  /// 카드가 바꾸는 날 (변경 요약의 day 중 첫 번째, 없으면 1).
  int get day => changes.map((c) => c.day).whereType<int>().firstOrNull ?? 1;
}
