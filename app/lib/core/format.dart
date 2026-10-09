// 화면 표기용 포맷 함수.

const _weekdays = ['월', '화', '수', '목', '금', '토', '일'];

String weekdayKo(DateTime d) => _weekdays[d.weekday - 1];

String two(int n) => n.toString().padLeft(2, '0');

/// 10.10
String md(DateTime d) => '${d.month}.${two(d.day)}';

/// 10.10 – 10.12 (당일치기면 10.10)
String dateRange(DateTime a, DateTime b) => a == b ? md(a) : '${md(a)} – ${md(b)}';

/// 2박 3일 / 당일치기
String nightsDays(DateTime a, DateTime b) {
  final days = b.difference(a).inDays + 1;
  return days <= 1 ? '당일치기' : '${days - 1}박 $days일';
}

/// 2026-10-10
String isoDate(DateTime d) => '${d.year}-${two(d.month)}-${two(d.day)}';

/// 1시간 30분 / 40분
String durationKo(int minutes) {
  final h = minutes ~/ 60, m = minutes % 60;
  if (h == 0) return '$m분';
  return m == 0 ? '$h시간' : '$h시간 $m분';
}

/// "HH:MM" → 분. 자정 넘김("24:30")도 그대로 계산한다.
int? toMinutes(String? hhmm) {
  if (hhmm == null) return null;
  final p = hhmm.split(':');
  if (p.length < 2) return null;
  return int.parse(p[0]) * 60 + int.parse(p[1]);
}

String fromMinutes(int m) => '${two(m ~/ 60)}:${two(m % 60)}';

/// Figma 표기: "Last edited 2h ago"
String editedAgo(DateTime t, {DateTime? now}) {
  final diff = (now ?? DateTime.now()).difference(t);
  if (diff.inMinutes < 1) return 'Edited just now';
  if (diff.inHours < 1) return 'Last edited ${diff.inMinutes}m ago';
  if (diff.inDays < 1) return 'Last edited ${diff.inHours}h ago';
  if (diff.inDays == 1) return 'Edited yesterday';
  if (diff.inDays < 7) return 'Edited ${diff.inDays}d ago';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return '${months[t.month - 1]} ${t.day}';
}

DateTime dateOnly(DateTime d) => DateTime(d.year, d.month, d.day);
