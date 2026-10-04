import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'api/api_client.dart';
import 'api/guryong_api.dart';

/// 로그인 상태와 서버 주소. account 서비스가 생기기 전까지 "로그인"은
/// 사용자 ID(이메일)를 정해 `X-User-Id` 헤더로 보내는 것뿐이다.
class AppSession extends ChangeNotifier {
  AppSession._(this._prefs, this._clientFactory) {
    _baseUrl = _prefs.getString(_kBaseUrl) ?? defaultBaseUrl;
    _userId = _prefs.getString(_kUserId);
    _rebuild();
  }

  static const _kBaseUrl = 'base_url';
  static const _kUserId = 'user_id';

  static Future<AppSession> load({http.Client Function()? clientFactory}) async =>
      AppSession._(await SharedPreferences.getInstance(), clientFactory);

  /// Android 에뮬레이터에서 호스트 PC 는 10.0.2.2 다. Windows 에서는 localhost 가 IPv6 를 먼저 시도해 느리므로 127.0.0.1.
  static String get defaultBaseUrl {
    if (!kIsWeb && Platform.isAndroid) return 'http://10.0.2.2:8000';
    return 'http://127.0.0.1:8000';
  }

  final SharedPreferences _prefs;
  final http.Client Function()? _clientFactory;
  late String _baseUrl;
  String? _userId;
  late GuryongApi api;

  String get baseUrl => _baseUrl;
  String? get userId => _userId;
  bool get signedIn => _userId != null;

  void _rebuild() =>
      api = GuryongApi(ApiClient(baseUrl: _baseUrl, userId: _userId ?? 'anonymous', clientFactory: _clientFactory));

  Future<void> signIn(String userId) async {
    _userId = userId.trim();
    await _prefs.setString(_kUserId, _userId!);
    _rebuild();
    notifyListeners();
  }

  Future<void> signOut() async {
    _userId = null;
    await _prefs.remove(_kUserId);
    _rebuild();
    notifyListeners();
  }

  Future<void> setBaseUrl(String url) async {
    var v = url.trim();
    while (v.endsWith('/')) {
      v = v.substring(0, v.length - 1);
    }
    if (v.isEmpty) v = defaultBaseUrl;
    if (!v.startsWith('http')) v = 'http://$v';
    _baseUrl = v;
    await _prefs.setString(_kBaseUrl, v);
    _rebuild();
    notifyListeners();
  }
}
