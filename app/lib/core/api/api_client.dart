import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

/// 게이트웨이(기본 :8000) 하나로 places · planner · agent 를 호출한다.
/// 로그인 서비스(account)가 생기기 전까지 사용자 구분은 `X-User-Id` 헤더로 한다.
class ApiException implements Exception {
  ApiException(this.status, this.message);
  final int status; // 0 = 네트워크 오류
  final String message;

  bool get isConflict => status == 409;

  @override
  String toString() => message;
}

class ApiClient {
  ApiClient({required this.baseUrl, required this.userId, http.Client Function()? clientFactory})
      : _newClient = clientFactory ?? http.Client.new {
    _http = _newClient();
  }

  final String baseUrl;
  final String userId;

  /// 테스트에서 가짜 서버를 끼우기 위한 것. SSE 는 요청마다 새 클라이언트를 만들어 끊을 때 닫는다.
  final http.Client Function() _newClient;
  late final http.Client _http;

  static const _timeout = Duration(seconds: 30);

  Map<String, String> get headers => {
        'X-User-Id': userId,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      };

  Uri uri(String path, [Map<String, dynamic>? query]) {
    final q = <String, dynamic>{};
    query?.forEach((k, v) {
      if (v == null) return;
      q[k] = v is List ? v.map((e) => '$e').toList() : '$v';
    });
    return Uri.parse('$baseUrl$path').replace(queryParameters: q.isEmpty ? null : q);
  }

  Future<dynamic> get(String path, [Map<String, dynamic>? query]) => _send(() => _http.get(uri(path, query), headers: headers));

  Future<dynamic> post(String path, [Object? body, Map<String, dynamic>? query]) =>
      _send(() => _http.post(uri(path, query), headers: headers, body: body == null ? null : jsonEncode(body)));

  Future<dynamic> patch(String path, Object body) =>
      _send(() => _http.patch(uri(path), headers: headers, body: jsonEncode(body)));

  Future<dynamic> delete(String path) => _send(() => _http.delete(uri(path), headers: headers));

  Future<dynamic> _send(Future<http.Response> Function() call) async {
    final http.Response res;
    try {
      res = await call().timeout(_timeout);
    } on TimeoutException {
      throw ApiException(0, '서버 응답이 없습니다. 잠시 후 다시 시도하세요.');
    } catch (e) {
      throw ApiException(0, '서버에 연결할 수 없습니다 ($baseUrl)');
    }
    final text = utf8.decode(res.bodyBytes);
    if (res.statusCode >= 400) throw ApiException(res.statusCode, errorMessage(text, res.statusCode));
    if (text.isEmpty) return null;
    return jsonDecode(text);
  }

  /// FastAPI 오류 응답({"detail": str | [{msg}]})에서 사람이 읽을 메시지를 뽑는다.
  static String errorMessage(String body, int status) {
    try {
      final detail = (jsonDecode(body) as Map)['detail'];
      if (detail is String) return detail;
      if (detail is List && detail.isNotEmpty) {
        return detail.map((e) => e is Map ? e['msg'] ?? '$e' : '$e').join('\n');
      }
    } catch (_) {}
    return '요청을 처리하지 못했습니다 ($status)';
  }

  /// SSE 요청. 스트림을 취소하면 연결을 끊는다(서버는 턴을 정리한다).
  Stream<SseEvent> postSse(String path, Object body) {
    late StreamController<SseEvent> out;
    final client = _newClient();
    StreamSubscription<String>? sub;

    Future<void> start() async {
      final req = http.Request('POST', uri(path))
        ..headers.addAll({...headers, 'Accept': 'text/event-stream'})
        ..body = jsonEncode(body);
      try {
        final res = await client.send(req);
        if (res.statusCode >= 400) {
          final text = await res.stream.bytesToString();
          out.addError(ApiException(res.statusCode, errorMessage(text, res.statusCode)));
          await out.close();
          return;
        }
        final parser = SseParser();
        sub = res.stream.transform(utf8.decoder).listen(
          (chunk) {
            for (final ev in parser.feed(chunk)) {
              out.add(ev);
            }
          },
          onError: (Object e) => out.addError(ApiException(0, '응답 스트림이 끊겼습니다.')),
          onDone: () => out.close(),
        );
      } catch (_) {
        if (!out.isClosed) {
          out.addError(ApiException(0, '서버에 연결할 수 없습니다 ($baseUrl)'));
          await out.close();
        }
      }
    }

    out = StreamController<SseEvent>(
      onListen: start,
      onCancel: () async {
        await sub?.cancel();
        client.close();
      },
    );
    return out.stream;
  }
}

class SseEvent {
  SseEvent(this.event, this.data);
  final String event;
  final Map<String, dynamic> data;
}

/// `event: <이름>` / `data: <JSON>` / 빈 줄 형식의 SSE 를 조각 단위로 파싱한다.
class SseParser {
  final _buf = StringBuffer();
  String _event = 'message';
  final _data = <String>[];

  List<SseEvent> feed(String chunk) {
    final events = <SseEvent>[];
    _buf.write(chunk);
    var text = _buf.toString();
    int nl;
    while ((nl = text.indexOf('\n')) >= 0) {
      var line = text.substring(0, nl);
      text = text.substring(nl + 1);
      if (line.endsWith('\r')) line = line.substring(0, line.length - 1);
      if (line.isEmpty) {
        if (_data.isNotEmpty) {
          try {
            final decoded = jsonDecode(_data.join('\n'));
            events.add(SseEvent(_event, decoded is Map<String, dynamic> ? decoded : {'value': decoded}));
          } catch (_) {}
        }
        _event = 'message';
        _data.clear();
      } else if (line.startsWith('event:')) {
        _event = line.substring(6).trim();
      } else if (line.startsWith('data:')) {
        _data.add(line.substring(5).trimLeft());
      }
    }
    _buf
      ..clear()
      ..write(text);
    return events;
  }
}
