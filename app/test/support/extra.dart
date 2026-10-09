// 녹화 fixture 에 없는 응답 (safety outlook·situation·sim, agent info, health, 로그인 잠김 429).
// 형식은 backend/app/safety/schemas.py (Outlook·Situation·SimState) 와 docs/account §1 을 따른다.

Map<String, dynamic> outlookJson({String date = '2026-10-10', bool far = false}) => {
      'date': date,
      'now': '2026-10-10T11:00:00+09:00',
      'horizon': far ? 'long_range' : 'short_range',
      'forecast_available': !far,
      'confidence': far ? 'none' : 'high',
      'summary': far ? null : {'max_pop': 30, 'total_pcp_mm': 0.0, 'max_wind_ms': 11.0, 'max_wave_m': 1.8, 'temp_min': 15.0, 'temp_max': 21.0, 'sky': '구름많음'},
      'hazards': [],
      'safe_windows': far
          ? []
          : [
              {'place_kind': 'breakwater', 'start': '${date}T09:00:00+09:00', 'end': '${date}T14:00:00+09:00', 'has_info': false},
              {'place_kind': 'outdoor', 'start': '${date}T09:00:00+09:00', 'end': '${date}T15:00:00+09:00', 'has_info': false},
            ],
      'hourly': [],
      'tides': [
        {'time': '${date}T05:41:00+09:00', 'kind': 'high', 'level_cm': 33},
        {'time': '${date}T11:50:00+09:00', 'kind': 'low', 'level_cm': 12},
        {'time': '${date}T17:52:00+09:00', 'kind': 'high', 'level_cm': 31},
      ],
      'warnings': [],
      'seasonal_notes': far ? ['가을 동해안은 바람이 잦아요 (평년 경향이며 예보가 아님).'] : [],
      'guidance': '',
      'recheck_from': far ? '2026-10-27' : null,
      'data_status': {},
    };

/// 그날 09~18시: 방파제 14~15 주의·16~18 위험, 해안 저지대 16~18 주의, 야외 15 참고·16~18 주의, 실내 16~18 참고.
Map<String, dynamic> situationJson({String date = '2026-10-10'}) {
  String level(String kind, int h) => switch (kind) {
        'breakwater' => h >= 16 ? 'danger' : (h >= 14 ? 'caution' : 'safe'),
        'flood' => h >= 16 ? 'caution' : 'safe',
        'outdoor' => h >= 16 ? 'caution' : 'safe',
        _ => 'safe',
      };
  return {
    'now': '2026-10-10T11:00:00+09:00',
    'fingerprint': 'abc',
    'forecast_until': '2026-10-12T21:00:00+09:00',
    'zones': {},
    'warnings': [],
    'hours': [
      for (var h = 9; h <= 18; h++)
        {
          'time': '${date}T${h.toString().padLeft(2, '0')}:00:00+09:00',
          'levels': {for (final k in ['breakwater', 'flood', 'outdoor', 'indoor']) k: level(k, h)},
          'info': [if (h == 15) 'outdoor', if (h >= 16) 'indoor'],
        },
    ],
    'data_ok': {'forecast': true},
  };
}

Map<String, dynamic> simJson({String scenario = 'calm', String now = '2026-10-10T14:00:00+09:00'}) => {
      'scenario': scenario,
      'now': now,
      'offset_minutes': 0,
      'scenarios': {'calm': '평상시', 'rain': '비', 'wind_tomorrow': '내일 강풍', 'flood': '침수 센서', 'storm': '폭풍'},
    };
