import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'api/api_client.dart';
import 'api/guryong_api.dart';
import 'models/account.dart';
import 'token_store.dart';

/// 빌드 설정. 데모·개발 그룹(33)은 `--dart-define=DEMO=true` 빌드에서만 보인다.
abstract final class AppConfig {
  static bool demo = const bool.fromEnvironment('DEMO');
  static const version = '0.4.0';
}

enum AuthStatus { restoring, signedOut, signedIn }

/// 로그인 상태(account 토큰)와 서버 주소. 모든 요청에 `Authorization: Bearer <access>` 를 붙이고,
/// 401 이면 refresh 를 한 번만 보내 새 쌍으로 다시 시도한다 (34 §토큰, account §2).
class AppSession extends ChangeNotifier implements AuthHandler {
  AppSession._(this._prefs, this._store, this._clientFactory) {
    _baseUrl = _prefs.getString(_kBaseUrl) ?? defaultBaseUrl;
    demoLocationMode = demoLocationForced || (_prefs.getBool(_kDemoLocation) ?? false);
    _rebuild();
  }

  static const _kBaseUrl = 'base_url';
  static const _kDemoLocation = 'demo_location_mode';

  static Future<AppSession> load({http.Client Function()? clientFactory, TokenStore? tokenStore}) async {
    final s = AppSession._(await SharedPreferences.getInstance(), tokenStore ?? TokenStore.platform(), clientFactory);
    final t = await s._store.read();
    s._access = t.access;
    s._refresh = t.refresh;
    if (t.user != null) {
      try {
        s.user = UserView.fromJson(jsonDecode(t.user!) as Map<String, dynamic>);
      } catch (_) {}
    }
    s.status = s._refresh == null ? AuthStatus.signedOut : AuthStatus.restoring;
    return s;
  }

  /// 공개 테스트 서버 (GCP, docs/deploy/README.md).
  static const serverUrl = 'http://34.64.209.209:8000';

  /// 빌드 때 `--dart-define=API_BASE=…` (로컬 개발: http://127.0.0.1:8000, Android 에뮬레이터: http://10.0.2.2:8000).
  static const _apiBase = String.fromEnvironment('API_BASE');

  /// 기본 서버 주소: API_BASE → 웹은 페이지를 내려준 게이트웨이(같은 출처) → 그 밖은 공개 테스트 서버.
  /// 설정 화면에서 바꾼 값(저장값)이 있으면 그것이 우선한다.
  static String get defaultBaseUrl {
    if (_apiBase.isNotEmpty) return _apiBase;
    if (kIsWeb) return Uri.base.origin;
    return serverUrl;
  }

  /// 웹은 http 주소에서 브라우저가 위치를 주지 않으므로 항상 데모 위치 모드 (관제 대시보드 /console/ 로 위치를 옮긴다).
  static const demoLocationForced = kIsWeb;

  final SharedPreferences _prefs;
  final TokenStore _store;
  final http.Client Function()? _clientFactory;
  late String _baseUrl;
  String? _access;
  String? _refresh;
  UserView? user;
  AuthStatus status = AuthStatus.signedOut;

  /// 로그인 화면에 한 번 보여 줄 안내 (refresh_token_reused 등).
  String? signOutNotice;

  /// 데모 위치 모드 (33). 켜면 GPS 대신 서버에 들어온 위치를 그린다 — 동행 모드에서 쓴다.
  bool demoLocationMode = false;

  late GuryongApi api;
  Future<bool>? _refreshing;

  /// 서비스 시각 - 기기 시계 (데모 가상 시각, `GET /safety/sim`). 404 sim_disabled(실제 데이터)면 0.
  Duration serverOffset = Duration.zero;

  /// 서비스 기준 지금. 여행 날짜 판단(계획 목록 분류·D-n·새 계획 기본 날짜)은 이걸 쓴다.
  /// 서버가 실제 시각으로 남긴 값(created_at·decided_at 등)과 비교할 때는 그대로 DateTime.now().
  DateTime serverNow() => DateTime.now().add(serverOffset);

  /// 서비스 기준 오늘 (KST 날짜).
  DateTime serverToday() {
    final k = serverNow().toUtc().add(const Duration(hours: 9));
    return DateTime(k.year, k.month, k.day);
  }

  void setServerTime(DateTime now) => serverOffset = now.difference(DateTime.now());

  /// 서비스 시각을 다시 맞춘다 (계획 목록을 불러올 때마다 — 관제 화면에서 가상 시각을 바꿨을 수 있다). 네트워크 오류면 이전 값 그대로.
  Future<void> syncClock() async {
    try {
      setServerTime((await api.sim()).now);
    } on ApiException catch (e) {
      if (e.status == 404) serverOffset = Duration.zero;
    } catch (_) {}
  }

  String get baseUrl => _baseUrl;
  bool get signedIn => status == AuthStatus.signedIn;
  @override
  String? get accessToken => _access;

  void _rebuild() => api = GuryongApi(ApiClient(baseUrl: _baseUrl, auth: this, clientFactory: _clientFactory));

  /// 앱 시작: 저장된 refresh 토큰으로 새 쌍을 받아 01 을 건너뛴다. 실패하면 01.
  Future<void> restore() async {
    if (_refresh == null) {
      status = AuthStatus.signedOut;
      notifyListeners();
      return;
    }
    final ok = await refresh();
    if (ok) {
      status = AuthStatus.signedIn;
      notifyListeners();
    } else if (status != AuthStatus.signedOut) {
      status = AuthStatus.signedOut;
      notifyListeners();
    }
  }

  /// 동시에 여러 요청이 401 이어도 refresh 는 한 번만 보낸다 (refresh 는 1회용이라 두 번 보내면 refresh_token_reused).
  @override
  Future<bool> refresh() => _refreshing ??= _doRefresh().whenComplete(() => _refreshing = null);

  Future<bool> _doRefresh() async {
    final rt = _refresh;
    if (rt == null) return false;
    try {
      await _save(await api.refresh(rt));
      return true;
    } on ApiException catch (e) {
      if (e.isNetwork) {
        signOutNotice = '서버에 연결하지 못했어요. 연결되면 다시 로그인해 주세요.';
        status = AuthStatus.signedOut;
        notifyListeners();
        return false;
      }
      await _clear(e.code == 'refresh_token_reused' ? '다른 곳에서 로그인 정보가 쓰여 다시 로그인해 주세요' : '로그인이 만료됐어요. 다시 로그인해 주세요');
      return false;
    }
  }

  Future<void> _save(TokenResponse t) async {
    _access = t.accessToken;
    _refresh = t.refreshToken;
    user = t.user;
    await _store.write(access: t.accessToken, refresh: t.refreshToken, user: jsonEncode(t.user.toJson()));
  }

  Future<void> _clear([String? notice]) async {
    _access = null;
    _refresh = null;
    user = null;
    signOutNotice = notice;
    status = AuthStatus.signedOut;
    await _store.clear();
    notifyListeners();
  }

  /// 로그인·가입·Google 성공 → 02 (스택 교체는 main 의 home 전환).
  Future<void> signInWith(TokenResponse t) async {
    await _save(t);
    signOutNotice = null;
    status = AuthStatus.signedIn;
    notifyListeners();
  }

  Future<void> signOut() async {
    final rt = _refresh;
    if (rt != null) {
      try {
        await api.logout(rt);
      } catch (_) {}
    }
    await _clear();
  }

  /// 탈퇴 (33, 확인 2단계 뒤). 실패하면 ApiException 을 그대로 던진다.
  Future<void> deleteAccount() async {
    await api.deleteMe();
    await _clear();
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

  Future<void> setDemoLocationMode(bool on) async {
    if (demoLocationForced) return;
    demoLocationMode = on;
    await _prefs.setBool(_kDemoLocation, on);
    notifyListeners();
  }
}
