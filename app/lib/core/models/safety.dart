// 안전 점검 (planner §7) · safety 조회 (docs/safety/README.md §1) · routing 경로 응답 모델.
import 'package:latlong2/latlong.dart';

List<T> _list<T>(Object? v, T Function(Map<String, dynamic>) f) =>
    ((v as List?) ?? const []).map((e) => f(e as Map<String, dynamic>)).toList();

DateTime? _dt(Object? v) => v == null ? null : DateTime.parse(v as String);

/// 판단 근거 하나. level: info(참고) | caution | danger.
class Hazard {
  Hazard({required this.type, required this.level, required this.reason, required this.source});
  final String type; // wind · wave · rain · flood · warning · cctv …
  final String level;
  final String reason;
  final String source;

  factory Hazard.fromJson(Map<String, dynamic> j) => Hazard(
        type: j['type'] as String? ?? '',
        level: j['level'] as String? ?? 'info',
        reason: j['reason'] as String? ?? '',
        source: j['source'] as String? ?? '',
      );
}

class TimeWindow {
  TimeWindow({required this.start, required this.end, required this.hasInfo, this.kind});
  final DateTime start;
  final DateTime end;
  final bool hasInfo;
  final String? kind; // outlook safe_windows 의 place_kind

  factory TimeWindow.fromJson(Map<String, dynamic> j) => TimeWindow(
        start: DateTime.parse(j['start'] as String),
        end: DateTime.parse(j['end'] as String),
        hasInfo: j['has_info'] as bool? ?? false,
        kind: j['place_kind'] as String?,
      );
}

class Detour {
  Detour({required this.onRoute, required this.avoided, required this.detourMinutes, required this.note});
  final List<String> onRoute;
  final List<String> avoided;
  final int? detourMinutes;
  final String? note;

  factory Detour.fromJson(Map<String, dynamic> j) => Detour(
        onRoute: ((j['on_route'] as List?) ?? const []).map((e) => '$e').toList(),
        avoided: ((j['avoided'] as List?) ?? const []).map((e) => '$e').toList(),
        detourMinutes: j['detour_minutes'] as int?,
        note: j['note'] as String?,
      );
}

/// 방문(visit) 또는 그 항목까지 오는 이동(travel) 하나의 위험 단계.
class SafetyItem {
  SafetyItem({
    required this.itemId,
    required this.kind,
    required this.mode,
    required this.day,
    required this.date,
    required this.title,
    required this.startAt,
    required this.endAt,
    required this.risk,
    required this.unassessedReason,
    required this.dataMissing,
    required this.horizon,
    required this.hazards,
    required this.betterTimes,
    required this.sensitivity,
    required this.notes,
    required this.recheckFrom,
    required this.detour,
  });

  final int itemId;
  final String kind; // visit | travel
  final String? mode;
  final int? day;
  final DateTime? date;
  final String title;
  final String? startAt;
  final String? endAt;
  final String? risk; // safe | caution | danger | null
  final String? unassessedReason; // past | beyond_forecast | data_missing
  final bool dataMissing;
  final String? horizon; // now | short_range | medium_range | long_range | past
  final List<Hazard> hazards;
  final List<TimeWindow> betterTimes;
  final List<String> sensitivity;
  final List<String> notes;
  final DateTime? recheckFrom;
  final Detour? detour;

  bool get isVisit => kind == 'visit';
  bool get elevated => risk == 'caution' || risk == 'danger';
  bool get indoor => sensitivity.contains('실내');

  /// caution·danger 근거 중 가장 높은 것.
  Hazard? get topHazard {
    Hazard? best;
    for (final h in hazards) {
      if (h.level == 'danger') return h;
      if (h.level == 'caution') best ??= h;
    }
    return best;
  }

  Hazard? get firstInfo => hazards.where((h) => h.level == 'info').firstOrNull;

  factory SafetyItem.fromJson(Map<String, dynamic> j) => SafetyItem(
        itemId: j['item_id'] as int,
        kind: j['kind'] as String? ?? 'visit',
        mode: j['mode'] as String?,
        day: j['day'] as int?,
        date: _dt(j['date']),
        title: j['title'] as String? ?? '',
        startAt: j['start_at'] as String?,
        endAt: j['end_at'] as String?,
        risk: j['risk'] as String?,
        unassessedReason: j['unassessed_reason'] as String?,
        dataMissing: j['data_missing'] as bool? ?? false,
        horizon: j['horizon'] as String?,
        hazards: _list(j['hazards'], Hazard.fromJson),
        betterTimes: _list(j['better_times'], TimeWindow.fromJson),
        sensitivity: ((j['sensitivity'] as List?) ?? const []).map((e) => '$e').toList(),
        notes: ((j['notes'] as List?) ?? const []).map((e) => '$e').toList(),
        recheckFrom: _dt(j['recheck_from']),
        detour: j['detour'] == null ? null : Detour.fromJson(j['detour'] as Map<String, dynamic>),
      );
}

class SafetyCheck {
  SafetyCheck({required this.planVersion, required this.checkedAt, required this.overallRisk, required this.counts, required this.items, required this.dataOk});
  final int? planVersion;
  final DateTime? checkedAt;
  final String? overallRisk;
  final Map<String, int> counts;
  final List<SafetyItem> items;
  final Map<String, bool> dataOk;

  bool get partialData => dataOk.values.any((ok) => !ok);

  SafetyItem? visit(int itemId) => items.where((i) => i.isVisit && i.itemId == itemId).firstOrNull;
  SafetyItem? travel(int itemId) => items.where((i) => !i.isVisit && i.itemId == itemId).firstOrNull;

  /// 남은 방문 중 주의·위험 수 (null 은 세지 않는다, 21 지표).
  int get riskyVisits => items.where((i) => i.isVisit && i.elevated).length;

  factory SafetyCheck.fromJson(Map<String, dynamic> j) => SafetyCheck(
        planVersion: j['plan_version'] as int?,
        checkedAt: _dt(j['checked_at']),
        overallRisk: j['overall_risk'] as String?,
        counts: {for (final e in ((j['counts'] as Map?) ?? const {}).entries) '${e.key}': (e.value as num).toInt()},
        items: _list(j['items'], SafetyItem.fromJson),
        dataOk: {for (final e in ((j['data_ok'] as Map?) ?? const {}).entries) '${e.key}': e.value == true},
      );
}

class SafetyAlert {
  SafetyAlert({required this.id, required this.level, required this.needsAction, required this.status, required this.message, required this.changes});
  final int id;
  final String level;
  final bool needsAction;
  final String status;
  final String message;
  final List<SafetyAlertChange> changes;

  factory SafetyAlert.fromJson(Map<String, dynamic> j) => SafetyAlert(
        id: j['id'] as int,
        level: j['level'] as String? ?? 'safe',
        needsAction: j['needs_action'] as bool? ?? false,
        status: j['status'] as String? ?? 'new',
        message: j['message'] as String? ?? '',
        changes: _list(j['changes'], SafetyAlertChange.fromJson),
      );
}

class SafetyAlertChange {
  SafetyAlertChange({required this.kind, required this.toRisk, required this.item});
  final String kind; // raised | lowered | cleared
  final String? toRisk;
  final SafetyItem? item;

  factory SafetyAlertChange.fromJson(Map<String, dynamic> j) => SafetyAlertChange(
        kind: j['kind'] as String? ?? 'raised',
        toRisk: j['to_risk'] as String?,
        item: j['item'] == null ? null : SafetyItem.fromJson(j['item'] as Map<String, dynamic>),
      );
}

/// GET /safety/outlook (backend/app/safety/schemas.py `Outlook`).
class Outlook {
  Outlook({required this.date, required this.forecastAvailable, required this.summary, required this.safeWindows, required this.tides, required this.warnings, required this.seasonalNotes, required this.recheckFrom});
  final DateTime date;
  final bool forecastAvailable;
  final Map<String, dynamic>? summary; // max_pop · total_pcp_mm · max_wind_ms · max_wave_m · sky …
  final List<TimeWindow> safeWindows;
  final List<({DateTime time, String kind, double levelCm})> tides;
  final List<String> warnings;
  final List<String> seasonalNotes;
  final DateTime? recheckFrom;

  factory Outlook.fromJson(Map<String, dynamic> j) => Outlook(
        date: DateTime.parse(j['date'] as String),
        forecastAvailable: j['forecast_available'] as bool? ?? false,
        summary: j['summary'] as Map<String, dynamic>?,
        safeWindows: _list(j['safe_windows'], TimeWindow.fromJson),
        tides: [
          for (final t in (j['tides'] as List?) ?? const [])
            (time: DateTime.parse(t['time'] as String), kind: t['kind'] as String, levelCm: (t['level_cm'] as num).toDouble()),
        ],
        warnings: ((j['warnings'] as List?) ?? const []).map((e) => '$e').toList(),
        seasonalNotes: ((j['seasonal_notes'] as List?) ?? const []).map((e) => '$e').toList(),
        recheckFrom: _dt(j['recheck_from']),
      );
}

/// GET /safety/situation 의 시각별 장소 종류 단계.
class SituationHour {
  SituationHour({required this.time, required this.levels, required this.info});
  final DateTime time;
  final Map<String, String?> levels; // breakwater · flood · outdoor · indoor
  final Set<String> info;

  factory SituationHour.fromJson(Map<String, dynamic> j) => SituationHour(
        time: DateTime.parse(j['time'] as String),
        levels: {for (final e in ((j['levels'] as Map?) ?? const {}).entries) '${e.key}': e.value as String?},
        info: {...((j['info'] as List?) ?? const []).map((e) => '$e')},
      );
}

class Situation {
  Situation({required this.now, required this.hours, required this.warnings});
  final DateTime? now;
  final List<SituationHour> hours;
  final List<String> warnings;

  factory Situation.fromJson(Map<String, dynamic> j) => Situation(
        now: _dt(j['now']),
        hours: _list(j['hours'], SituationHour.fromJson),
        warnings: ((j['warnings'] as List?) ?? const []).map((e) => '$e').toList(),
      );
}

class SimState {
  SimState({required this.scenario, required this.now, required this.scenarios});
  final String scenario;
  final DateTime now;
  final Map<String, String> scenarios;

  factory SimState.fromJson(Map<String, dynamic> j) => SimState(
        scenario: j['scenario'] as String? ?? 'calm',
        now: DateTime.parse(j['now'] as String),
        scenarios: {for (final e in ((j['scenarios'] as Map?) ?? const {}).entries) '${e.key}': '${e.value}'},
      );
}

List<LatLng> _ring(Object? v) => [for (final p in (v as List?) ?? const []) LatLng((p[1] as num).toDouble(), (p[0] as num).toDouble())];

/// GET /safety/avoid 의 구역. polygon 은 [lng, lat] 닫힌 고리 → LatLng 로 바꿔 둔다.
class AvoidZone {
  AvoidZone({required this.id, required this.name, required this.kind, required this.risk, required this.reason, required this.polygon});
  final String id;
  final String name;
  final String kind; // flood | breakwater
  final String risk;
  final String reason;
  final List<LatLng> polygon;

  factory AvoidZone.fromJson(Map<String, dynamic> j) => AvoidZone(
        id: j['id'] as String,
        name: j['name'] as String? ?? '',
        kind: j['kind'] as String? ?? '',
        risk: j['risk'] as String? ?? 'caution',
        reason: j['reason'] as String? ?? '',
        polygon: _ring(j['polygon']),
      );
}

/// POST /routing/route. geometry 는 GeoJSON LineString ([lng, lat]).
class RouteResult {
  RouteResult({required this.mode, required this.source, required this.minutes, required this.distanceM, required this.points, required this.hazards});
  final String mode;
  final String source; // road | estimate
  final int minutes;
  final int distanceM;
  final List<LatLng> points;
  final RouteHazards? hazards;

  bool get estimated => source == 'estimate';

  factory RouteResult.fromJson(Map<String, dynamic> j) => RouteResult(
        mode: j['mode'] as String? ?? 'walk',
        source: j['source'] as String? ?? 'road',
        minutes: (j['minutes'] as num?)?.round() ?? 0,
        distanceM: (j['distance_m'] as num?)?.round() ?? 0,
        points: _ring((j['geometry'] as Map?)?['coordinates']),
        hazards: j['hazards'] is Map ? RouteHazards.fromJson(j['hazards'] as Map<String, dynamic>) : null,
      );
}

class RouteHazards {
  RouteHazards({required this.checked, required this.avoided, required this.onRoute, required this.baselineMinutes, required this.baselineDistanceM, required this.detourMinutes, required this.note});
  final bool checked;
  final List<String> avoided;
  final List<String> onRoute;
  final int? baselineMinutes;
  final int? baselineDistanceM;
  final int? detourMinutes;
  final String? note;

  static List<String> _names(Object? v) => [for (final e in (v as List?) ?? const []) e is Map ? '${e['name'] ?? e['id']}' : '$e'];

  factory RouteHazards.fromJson(Map<String, dynamic> j) {
    final base = j['baseline'] as Map?;
    return RouteHazards(
      checked: j['checked'] as bool? ?? false,
      avoided: _names(j['avoided']),
      onRoute: _names(j['on_route']),
      baselineMinutes: (base?['minutes'] as num?)?.round(),
      baselineDistanceM: (base?['distance_m'] as num?)?.round(),
      detourMinutes: (j['detour_minutes'] as num?)?.round(),
      note: j['note'] as String?,
    );
  }
}
