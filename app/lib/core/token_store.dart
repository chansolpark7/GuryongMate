import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// access·refresh 토큰 저장소 (account §2: Keychain/Keystore = flutter_secure_storage).
abstract class TokenStore {
  /// 앱은 보안 저장소, 웹은 [PrefsTokenStore].
  factory TokenStore.platform() => kIsWeb ? PrefsTokenStore() : SecureTokenStore();

  Future<({String? access, String? refresh, String? user})> read();
  Future<void> write({required String access, required String refresh, required String user});
  Future<void> clear();
}

class SecureTokenStore implements TokenStore {
  static const _s = FlutterSecureStorage();

  @override
  Future<({String? access, String? refresh, String? user})> read() async {
    try {
      return (access: await _s.read(key: 'access_token'), refresh: await _s.read(key: 'refresh_token'), user: await _s.read(key: 'user'));
    } catch (_) {
      return (access: null, refresh: null, user: null);
    }
  }

  @override
  Future<void> write({required String access, required String refresh, required String user}) async {
    await _s.write(key: 'access_token', value: access);
    await _s.write(key: 'refresh_token', value: refresh);
    await _s.write(key: 'user', value: user);
  }

  @override
  Future<void> clear() async {
    for (final k in ['access_token', 'refresh_token', 'user']) {
      await _s.delete(key: k);
    }
  }
}

/// 웹: flutter_secure_storage 웹판은 WebCrypto 가 필요해 https·localhost 에서만 된다 (http 공개 서버에서는 저장 실패).
/// 그래서 브라우저 localStorage(shared_preferences)에 둔다 — 시험용 웹 앱이라 이 정도로 둔다 (docs/deploy/README.md §7).
class PrefsTokenStore implements TokenStore {
  static const _keys = ['access_token', 'refresh_token', 'user'];

  @override
  Future<({String? access, String? refresh, String? user})> read() async {
    final p = await SharedPreferences.getInstance();
    return (access: p.getString('access_token'), refresh: p.getString('refresh_token'), user: p.getString('user'));
  }

  @override
  Future<void> write({required String access, required String refresh, required String user}) async {
    final p = await SharedPreferences.getInstance();
    await p.setString('access_token', access);
    await p.setString('refresh_token', refresh);
    await p.setString('user', user);
  }

  @override
  Future<void> clear() async {
    final p = await SharedPreferences.getInstance();
    for (final k in _keys) {
      await p.remove(k);
    }
  }
}

/// 테스트용.
class MemoryTokenStore implements TokenStore {
  MemoryTokenStore({this.access, this.refresh, this.user});
  String? access;
  String? refresh;
  String? user;

  @override
  Future<({String? access, String? refresh, String? user})> read() async => (access: access, refresh: refresh, user: user);

  @override
  Future<void> write({required String access, required String refresh, required String user}) async {
    this.access = access;
    this.refresh = refresh;
    this.user = user;
  }

  @override
  Future<void> clear() async => access = refresh = user = null;
}
