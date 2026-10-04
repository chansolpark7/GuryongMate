// 테스트용 planner 응답 JSON 만들기. 형태는 backend/app/planner/schemas.py 의 PlanDetail 과 같다.

Map<String, dynamic> itemJson(
  int id,
  int order,
  String title,
  String start,
  String end, {
  int day = 1,
  int stay = 60,
  int? travel,
  String status = 'planned',
  String? startTime,
  double lat = 35.99,
  double lng = 129.55,
  String contentType = '관광지',
  String? placeId,
}) =>
    {
      'id': id,
      'day': day,
      'order': order,
      'place_id': placeId ?? 'kto-$id',
      'place': {
        'id': placeId ?? 'kto-$id',
        'title': title,
        'content_type': contentType,
        'lat': lat,
        'lng': lng,
        'addr': '경북 포항시 남구 구룡포읍',
        'open_hours': '',
        'rest_date': '',
      },
      'stay_minutes': stay,
      'start_time': startTime,
      'memo': '',
      'status': status,
      'travel_minutes': travel,
      'travel_distance_m': travel == null ? null : travel * 70,
      'arrive_at': start,
      'start_at': start,
      'end_at': end,
    };

Map<String, dynamic> planJson(
  List<Map<String, dynamic>> items, {
  int id = 1,
  String title = '구룡포 여행',
  String start = '2026-10-10',
  String end = '2026-10-12',
  List<Map<String, dynamic>> violations = const [],
  List<Map<String, dynamic>> constraints = const [],
}) {
  final startDate = DateTime.parse(start), endDate = DateTime.parse(end);
  final n = endDate.difference(startDate).inDays + 1;
  return {
    'id': id,
    'user_id': 'seoyun',
    'title': title,
    'start_date': start,
    'end_date': end,
    'day_start': '09:00',
    'day_end': '18:00',
    'travel_mode': 'walk',
    'memo': '',
    'created_at': '2026-10-04T10:00:00Z',
    'updated_at': '2026-10-04T10:00:00Z',
    'item_count': items.length,
    'days': [
      for (var d = 1; d <= n; d++)
        {
          'day': d,
          'date': startDate.add(Duration(days: d - 1)).toIso8601String().substring(0, 10),
          'weekday': '금',
          'end_at': items.where((i) => i['day'] == d).map((i) => i['end_at']).lastOrNull,
          'items': (items.where((i) => i['day'] == d).toList()..sort((a, b) => (a['order'] as int).compareTo(b['order'] as int))),
        },
    ],
    'constraints': constraints,
    'violations': violations,
  };
}
