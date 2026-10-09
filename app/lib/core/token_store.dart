import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// access·refresh 토큰 저장소 (account §2: Keychain/Keystore = flutter_secure_storage).
abstract class TokenStore {
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
