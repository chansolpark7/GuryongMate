import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

/// 서버 오류. 서비스 공통 형식 `{"detail": {"code", "message", …}}` 의 code·추가 필드를 그대로 들고 있다.
class ApiException implements Exception {
  ApiException(this.status, this.message, {this.code, this.detail = const {}});
  final int status; // 0 = 네트워크 오류
  final String message;
  final String? code;
  final Map<String, dynamic> detail;

  bool get isConflict => status == 409;
  bool get isNetwork => status == 0;

  @override
  String toString() => message;
}

/// 로그인 토큰을 가진 쪽(AppSession). 401 이면 [refresh] 를 한 번 부르고 다시 보낸다.
abstract class AuthHandler {
  String? get accessToken;

  /// 새 토큰 쌍을 받으면 true. 실패하면 false (이미 로그아웃 처리됨).
  Future<bool> refresh();
}

/// 게이트웨이(기본 :8000) 하나로 모든 서비스를 호출한다. 인증은 `Authorization: Bearer <access>` (account §2).
class ApiClient {
  ApiClient({required this.baseUrl, this.auth, http.Client Function()? clientFactory}) : _newClient = clientFactory ?? http.Client.new {
    _http = _newClient();
  }

  final String baseUrl;
  final AuthHandler? auth;

  /// 테스트에서 가짜 서버를 끼우기 위한 것. SSE 는 요청마다 새 클라이언트를 만들어 끊을 때 닫는다.
  final http.Client Function() _newClient;
  late final http.Client _http;

  static const _timeout = Duration(seconds: 30);

  Map<String, String> get headers => {
        if (auth?.accessToken != null) 'Authorization': 'Bearer ${auth!.accessToken}',
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

  Future<dynamic> get(String path, [Map<String, dynamic>? query]) => _send(path, () => _http.get(uri(path, query), headers: headers));

  Future<dynamic> post(String path, [Object? body, Map<String, dynamic>? query]) =>
      _send(path, () => _http.post(uri(path, query), headers: headers, body: body == null ? null : jsonEncode(body)));

  Future<dynamic> put(String path, Object body) => _send(path, () => _http.put(uri(path), headers: headers, body: jsonEncode(body)));

  Future<dynamic> patch(String path, Object body) => _send(path, () => _http.patch(uri(path), headers: headers, body: jsonEncode(body)));

  Future<dynamic> delete(String path) => _send(path, () => _http.delete(uri(path), headers: headers));

  /// `/auth/*` 는 토큰을 새로 받는 경로라 401 이어도 refresh 하지 않는다.
  bool _canRefresh(String path) => auth != null && !path.startsWith('/auth/');

  Future<dynamic> _send(String path, Future<http.Response> Function() call) async {
    var res = await _call(call);
    if (res.statusCode == 401 && _canRefresh(path) && await auth!.refresh()) {
      res = await _call(call);
    }
    final text = utf8.decode(res.bodyBytes);
    if (res.statusCode >= 400) throw parseError(text, res.statusCode);
    if (text.isEmpty) return null;
    return jsonDecode(text);
  }

  Future<http.Response> _call(Future<http.Response> Function() call) async {
    try {
      return await call().timeout(_timeout);
    } on TimeoutException {
      throw ApiException(0, '서버 응답이 없어요. 잠시 후 다시 시도해 주세요.', code: 'timeout');
    } catch (e) {
      throw ApiException(0, '서버에 연결하지 못했어요 ($baseUrl)', code: 'network');
    }
  }

  /// 오류 응답 → ApiException. `detail` 이 문자열·검증 오류 목록(FastAPI 기본)이어도 읽는다.
  static ApiException parseError(String body, int status) {
    try {
      final detail = (jsonDecode(body) as Map)['detail'];
      if (detail is Map) {
        final d = Map<String, dynamic>.from(detail);
        return ApiException(status, '${d['message'] ?? '요청을 처리하지 못했어요 ($status)'}', code: d['code'] as String?, detail: d);
      }
      if (detail is String) return ApiException(status, detail);
      if (detail is List && detail.isNotEmpty) {
        return ApiException(status, detail.map((e) => e is Map ? e['msg'] ?? '$e' : '$e').join('\n'), code: 'invalid_request');
      }
    } catch (_) {}
    if (status == 502 || status == 503) return ApiException(status, '서버에 연결하지 못했어요', code: 'upstream_unavailable');
    return ApiException(status, '요청을 처리하지 못했어요 ($status)');
  }

  /// SSE 요청. 스트림을 취소하면 연결을 끊는다(서버는 턴을 중단하고 그때까지 기록을 남긴다).
  /// 스트림 시작 전 401 이면 refresh 한 번 뒤 다시 연다 (스트림 중 401 은 없다고 본다, 34 §토큰).
  Stream<SseEvent> postSse(String path, [Object? body]) {
    late StreamController<SseEvent> out;
    http.Client? client;
    StreamSubscription<String>? sub;

    Future<http.StreamedResponse> open() {
      client?.close();
      client = _newClient();
      final req = http.Request('POST', uri(path))
        ..headers.addAll({...headers, 'Accept': 'text/event-stream'})
        ..body = body == null ? '' : jsonEncode(body);
      return client!.send(req);
    }

    Future<void> start() async {
      try {
        var res = await open();
        if (res.statusCode == 401 && _canRefresh(path)) {
          await res.stream.drain<void>();
          if (await auth!.refresh()) res = await open();
        }
        if (res.statusCode >= 400) {
          final text = await res.stream.bytesToString();
          out.addError(parseError(text, res.statusCode));
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
          onError: (Object e) => out.addError(ApiException(0, '응답이 끊겼어요.', code: 'network')),
          onDone: () => out.close(),
        );
      } catch (e) {
        if (!out.isClosed) {
          out.addError(e is ApiException ? e : ApiException(0, '서버에 연결하지 못했어요 ($baseUrl)', code: 'network'));
          await out.close();
        }
      }
    }

    out = StreamController<SseEvent>(
      onListen: start,
      onCancel: () async {
        await sub?.cancel();
        client?.close();
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
