// 녹화 fixture(test/fixtures/*.json)를 재생하는 가짜 게이트웨이 (docs/app/fixtures.md §4).
// 요청을 "METHOD route"(자리표시자 {…} = [^/]+) 로 맞춰 녹화 순서대로 응답한다.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

Map<String, dynamic> loadFixture(String name) => jsonDecode(File('test/fixtures/$name').readAsStringSync()) as Map<String, dynamic>;

class Req {
  Req(this.method, this.path, this.query, this.body, this.headers);
  final String method;
  final String path;
  final Map<String, String> query;
  final Object? body;
  final Map<String, String> headers;

  @override
  String toString() => '$method $path';
}

/// 응답: JSON (status, body) 또는 SSE 이벤트 목록.
class Res {
  Res.json(this.status, this.body) : sse = null;
  Res.sse(List<Map<String, dynamic>> events)
      : status = 200,
        body = null,
        sse = events;
  final int status;
  final Object? body;
  final List<Map<String, dynamic>>? sse;

  static Res error(int status, String code, [String message = '', Map<String, dynamic> extra = const {}]) =>
      Res.json(status, {'detail': {'code': code, 'message': message, ...extra}});
}

typedef Handler = FutureOr<Res> Function(Req req);

class FixtureGateway {
  FixtureGateway(List<String> files, {this.sseRealtime = false, this.timeScale = 1}) {
    for (final f in files) {
      final fx = loadFixture(f);
      for (final e in (fx['exchanges'] as List).cast<Map<String, dynamic>>()) {
        queues.putIfAbsent('${e['method']} ${e['route']}', () => []).add(e);
        named[e['name'] as String] = e;
      }
    }
  }

  /// true 면 SSE 를 녹화 시각(t_ms × [timeScale]) 간격으로 보낸다 (진행 단계가 실제처럼 흐름).
  final bool sseRealtime;
  final double timeScale;

  final queues = <String, List<Map<String, dynamic>>>{};
  final named = <String, Map<String, dynamic>>{};
  final _last = <String, Map<String, dynamic>>{};
  final overrides = <String, Handler>{};
  final requests = <Req>[];
  final unmatched = <String>[];

  /// 녹화에 없는 경로 응답을 정한다 (또는 녹화 응답을 덮는다). key = "METHOD /route/{x}".
  void on(String key, Handler h) => overrides[key] = h;

  Map<String, dynamic> ex(String name) => named[name] ?? (throw StateError('fixture 에 $name 없음'));
  dynamic response(String name) => ex(name)['response'];

  /// fixture 안 실제 경로에서 자리표시자 값 꺼내기: param('chat_make_plan', 'session_id').
  String param(String name, String key) {
    final e = ex(name);
    final r = (e['route'] as String).split('/'), p = (e['path'] as String).split('/');
    for (var i = 0; i < r.length; i++) {
      if (r[i] == '{$key}') return p[i];
    }
    throw StateError('$name 에 {$key} 없음');
  }

  List<Req> sent(String method, String pathPattern) => requests.where((r) => r.method == method && _match(pathPattern, r.path)).toList();

  static bool _match(String route, String path) => RegExp('^${route.replaceAll(RegExp(r'\{[^}]+\}'), '[^/]+')}\$').hasMatch(path);

  http.Client client() => MockClient.streaming(handle);

  Future<http.StreamedResponse> handle(http.BaseRequest req, http.ByteStream bodyStream) async {
    final text = await bodyStream.bytesToString();
    Object? body;
    try {
      body = text.isEmpty ? null : jsonDecode(text);
    } catch (_) {
      body = text;
    }
    final r = Req(req.method, req.url.path, req.url.queryParameters, body, req.headers);
    requests.add(r);
    Res? res;
    for (final e in overrides.entries) {
      final parts = e.key.split(' ');
      if (parts[0] == r.method && _match(parts[1], r.path)) {
        res = await e.value(r);
        break;
      }
    }
    if (res == null) {
      for (final e in queues.entries) {
        final parts = e.key.split(' ');
        if (parts[0] != r.method || !_match(parts[1], r.path)) continue;
        final q = e.value;
        final x = q.isNotEmpty ? q.removeAt(0) : _last[e.key];
        if (x == null) continue;
        _last[e.key] = x;
        res = x['sse'] != null ? Res.sse((x['sse'] as List).cast<Map<String, dynamic>>()) : Res.json(x['status'] as int, x['response']);
        break;
      }
    }
    if (res == null) {
      unmatched.add('$r');
      res = Res.error(404, 'not_recorded', 'fixture 에 없는 요청: $r');
    }
    if (res.sse != null) return http.StreamedResponse(_sse(res.sse!), 200, headers: {'content-type': 'text/event-stream'});
    return http.StreamedResponse(
      Stream.value(utf8.encode(res.body == null ? '' : jsonEncode(res.body))),
      res.status,
      headers: {'content-type': 'application/json'},
    );
  }

  Stream<List<int>> _sse(List<Map<String, dynamic>> events) async* {
    var t = 0;
    for (final e in events) {
      if (sseRealtime) {
        final at = ((e['t_ms'] as num?) ?? t).toInt();
        final wait = ((at - t) * timeScale).round();
        if (wait > 0) await Future<void>.delayed(Duration(milliseconds: wait));
        t = at;
      }
      final chunk = utf8.encode('event: ${e['event']}\ndata: ${jsonEncode(e['data'])}\n\n');
      // 실제 네트워크처럼 줄 중간에서도 끊어 보낸다
      for (var i = 0; i < chunk.length; i += 97) {
        yield chunk.sublist(i, i + 97 > chunk.length ? chunk.length : i + 97);
      }
    }
  }
}
