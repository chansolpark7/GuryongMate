import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:provider/provider.dart';

import '../../core/api/api_client.dart';
import '../../core/app_session.dart';
import '../../core/format.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../legal/legal_doc_screen.dart';
import '../settings/settings_screen.dart';

/// Google OAuth Web 클라이언트 ID (account §3.2 serverClientId). 빌드 때 `--dart-define=GOOGLE_SERVER_CLIENT_ID=…`.
const _googleServerClientId = String.fromEnvironment('GOOGLE_SERVER_CLIENT_ID');

/// 01_Login (Figma 21:1964). Google / 아이디 로그인, 하단 "서버 설정".
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  bool _google = false;

  @override
  void initState() {
    super.initState();
    // refresh 실패(refresh_token_reused 등)로 돌아왔으면 이유를 한 번 알린다.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final s = context.read<AppSession>();
      final notice = s.signOutNotice;
      if (notice != null && mounted) {
        s.signOutNotice = null;
        showInfo(context, notice);
      }
    });
  }

  Future<void> _googleSignIn() async {
    final session = context.read<AppSession>();
    setState(() => _google = true);
    try {
      final g = GoogleSignIn.instance;
      await g.initialize(serverClientId: _googleServerClientId.isEmpty ? null : _googleServerClientId);
      if (!g.supportsAuthenticate()) throw UnsupportedError('google');
      final account = await g.authenticate();
      final idToken = account.authentication.idToken;
      if (idToken == null) throw StateError('no id_token');
      await session.signInWith(await session.api.googleLogin(idToken));
    } on ApiException catch (e) {
      if (mounted) showInfo(context, e.code == 'google_unavailable' ? 'Google 로그인을 잠시 쓸 수 없어요. 잠시 후 다시 시도해 주세요.' : 'Google 로그인에 실패했어요');
    } on GoogleSignInException catch (e) {
      if (mounted && e.code != GoogleSignInExceptionCode.canceled) showInfo(context, 'Google 로그인에 실패했어요');
    } catch (_) {
      // Windows 데스크톱 등 Google Sign-In 을 지원하지 않는 기기
      if (mounted) showInfo(context, '이 기기에서는 Google 로그인을 쓸 수 없어요. 아이디로 계속해 주세요.', margin: const EdgeInsets.fromLTRB(16, 0, 16, 180));
    } finally {
      if (mounted) setState(() => _google = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.surface,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            const Spacer(flex: 5),
            const Center(child: AppLogo()),
            const SizedBox(height: 20),
            Text('구룡메이트', style: AppText.display, textAlign: TextAlign.center),
            const SizedBox(height: 6),
            Text('여행을 함께 걷는 AI', style: AppText.label.copyWith(fontSize: 16, color: AppColors.text2), textAlign: TextAlign.center),
            const SizedBox(height: 4),
            Text('구룡포 여행 계획부터 현장 안내까지', style: AppText.meta.copyWith(fontSize: 14, color: AppColors.text3), textAlign: TextAlign.center),
            const Spacer(flex: 6),
            AppButton(label: 'Google로 계속하기', kind: ButtonKind.secondary, leading: const _GoogleMark(), loading: _google, onPressed: _googleSignIn),
            const SizedBox(height: 12),
            AppButton(
              label: '아이디로 계속하기',
              icon: Icons.mail_outline,
              onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const LoginIdScreen())),
            ),
            const SizedBox(height: 16),
            const _Consent(),
            Center(
              child: TextButton(
                onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const SettingsScreen(serverOnly: true))),
                child: Text('서버 설정', style: AppText.caption.copyWith(color: AppColors.text2, decoration: TextDecoration.underline)),
              ),
            ),
            const SizedBox(height: 8),
          ]),
        ),
      ),
    );
  }
}

class AppLogo extends StatelessWidget {
  const AppLogo({super.key, this.size = 72});
  final double size;

  @override
  Widget build(BuildContext context) => Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          gradient: AppColors.aiGradient,
          borderRadius: BorderRadius.circular(size * 0.28),
          boxShadow: const [BoxShadow(color: Color(0x447B61FF), blurRadius: 24, offset: Offset(0, 10))],
        ),
        child: Stack(children: [
          Center(child: Icon(Icons.route, color: Colors.white, size: size * 0.5)),
          Positioned(right: size * 0.12, top: size * 0.1, child: Icon(Icons.auto_awesome, color: Colors.white, size: size * 0.2)),
        ]),
      );
}

class _GoogleMark extends StatelessWidget {
  const _GoogleMark();

  @override
  Widget build(BuildContext context) => ShaderMask(
        shaderCallback: (r) => const SweepGradient(
          colors: [Color(0xFFEA4335), Color(0xFFFBBC05), Color(0xFF34A853), Color(0xFF4285F4), Color(0xFFEA4335)],
        ).createShader(r),
        child: Text('G', style: TextStyle(fontFamily: appFontFamily, fontSize: 20, fontWeight: FontWeight.w800, color: Colors.white)),
      );
}

/// 34·35 공통 입력 필드: 라벨 14 Bold + 입력 52 r12 #F3F4F6, 오류면 1.5px Danger 테두리 + alert 아이콘 문구.
class AuthField extends StatelessWidget {
  const AuthField({
    super.key,
    required this.label,
    required this.controller,
    this.hint,
    this.error,
    this.help,
    this.obscure = false,
    this.onToggleObscure,
    this.onSubmitted,
    this.autofocus = false,
  });

  final String label;
  final TextEditingController controller;
  final String? hint;
  final String? error;
  final String? help;
  final bool obscure;
  final VoidCallback? onToggleObscure;
  final ValueChanged<String>? onSubmitted;
  final bool autofocus;

  @override
  Widget build(BuildContext context) {
    final errBorder = OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: AppColors.danger, width: 1.5));
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
      const SizedBox(height: 8),
      TextField(
        controller: controller,
        obscureText: obscure,
        autofocus: autofocus,
        autocorrect: false,
        enableSuggestions: !obscure,
        onSubmitted: onSubmitted,
        style: AppText.body,
        decoration: InputDecoration(
          hintText: hint,
          contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 15),
          enabledBorder: error == null ? null : errBorder,
          focusedBorder: error == null ? null : errBorder,
          suffixIcon: onToggleObscure == null
              ? null
              : IconButton(
                  tooltip: obscure ? '비밀번호 보기' : '비밀번호 숨기기',
                  icon: Icon(obscure ? Icons.visibility_outlined : Icons.visibility_off_outlined, color: AppColors.text3, size: 20),
                  onPressed: onToggleObscure,
                ),
        ),
      ),
      if (error != null)
        Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Icon(Icons.warning_amber_rounded, size: 14, color: AppColors.danger),
            const SizedBox(width: 4),
            Expanded(child: Text(error!, style: AppText.caption.copyWith(color: AppColors.danger))),
          ]),
        )
      else if (help != null)
        Padding(padding: const EdgeInsets.only(top: 6), child: Text(help!, style: AppText.caption)),
    ]);
  }
}

/// `errors[].loc` 에서 필드 이름을 찾는다 (422 invalid_request).
String? fieldOf(ApiException e) {
  final errs = e.detail['errors'];
  if (errs is List && errs.isNotEmpty) {
    final loc = (errs.first as Map)['loc'];
    if (loc is List && loc.isNotEmpty) return '${loc.last}';
  }
  return null;
}

/// 34_Login_ID (Figma 73:6084, 잠김 34-S1 73:8131).
class LoginIdScreen extends StatefulWidget {
  const LoginIdScreen({super.key});

  @override
  State<LoginIdScreen> createState() => _LoginIdScreenState();
}

class _LoginIdScreenState extends State<LoginIdScreen> {
  final _id = TextEditingController();
  final _pw = TextEditingController();
  bool _obscure = true;
  bool _busy = false;
  String? _idError;
  String? _pwError;
  DateTime? _lockedUntil;
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    _id.addListener(() => setState(() {}));
    _pw.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _tick?.cancel();
    _id.dispose();
    _pw.dispose();
    super.dispose();
  }

  Duration? get _remaining {
    final u = _lockedUntil;
    if (u == null) return null;
    final r = u.difference(DateTime.now());
    return r.isNegative ? null : r;
  }

  void _lock(int seconds) {
    _lockedUntil = DateTime.now().add(Duration(seconds: seconds));
    _tick?.cancel();
    _tick = Timer.periodic(const Duration(seconds: 1), (t) {
      if (!mounted) return t.cancel();
      if (_remaining == null) {
        t.cancel();
        _lockedUntil = null;
      }
      setState(() {});
    });
  }

  bool get _valid => _id.text.trim().isNotEmpty && _pw.text.isNotEmpty && _remaining == null;

  Future<void> _submit() async {
    if (!_valid || _busy) return;
    final session = context.read<AppSession>();
    setState(() {
      _busy = true;
      _idError = _pwError = null;
    });
    try {
      await session.signInWith(await session.api.login(_id.text, _pw.text));
    } on ApiException catch (e) {
      if (!mounted) return;
      switch (e.code) {
        case 'invalid_credentials':
          _pwError = '아이디 또는 비밀번호가 맞지 않아요';
          _pw.clear();
        case 'too_many_attempts':
          _lock((e.detail['retry_after'] as num?)?.toInt() ?? 900);
        case 'invalid_request':
          final f = fieldOf(e);
          if (f == 'password') {
            _pwError = '비밀번호를 확인해 주세요';
          } else {
            _idError = '아이디를 확인해 주세요';
          }
        default:
          showInfo(context, '서버에 연결하지 못했어요');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final remaining = _remaining;
    final until = _lockedUntil;
    return Scaffold(
      backgroundColor: AppColors.surface,
      appBar: AppBar(backgroundColor: AppColors.surface, leading: const BackButton()),
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.fromLTRB(20, 8, 20, 24), children: [
          Text('아이디로 로그인', style: AppText.title),
          const SizedBox(height: 4),
          if (remaining != null && until != null)
            // 34-S1: 잠김 박스가 부제 자리를 대신한다
            Container(
              margin: const EdgeInsets.only(top: 12),
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(color: AppColors.dangerSoft, borderRadius: BorderRadius.circular(12)),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Icon(Icons.lock_outline, size: 20, color: AppColors.danger),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('로그인을 여러 번 실패해 잠겼어요', style: AppText.label.copyWith(color: AppColors.danger, fontWeight: FontWeight.w700)),
                    Text('${two(until.hour)}:${two(until.minute)}에 다시 시도할 수 있어요', style: AppText.caption.copyWith(color: AppColors.danger)),
                  ]),
                ),
              ]),
            )
          else
            Text('구룡메이트 계정으로 계속해요', style: AppText.meta.copyWith(fontSize: 14)),
          const SizedBox(height: 28),
          AuthField(label: '아이디', controller: _id, hint: '아이디', error: _idError, autofocus: true),
          const SizedBox(height: 20),
          AuthField(
            label: '비밀번호',
            controller: _pw,
            hint: '비밀번호',
            error: _pwError,
            obscure: _obscure,
            onToggleObscure: () => setState(() => _obscure = !_obscure),
            onSubmitted: (_) => _submit(),
          ),
          const SizedBox(height: 28),
          if (remaining != null)
            Container(
              height: 52,
              alignment: Alignment.center,
              decoration: BoxDecoration(color: const Color(0xFFE5E7EB), borderRadius: BorderRadius.circular(12)),
              child: Text('로그인 (${two(remaining.inMinutes)}:${two(remaining.inSeconds % 60)} 남음)', style: AppText.section.copyWith(color: AppColors.text3)),
            )
          else
            AppButton(label: '로그인', loading: _busy, onPressed: _valid ? _submit : null),
          const SizedBox(height: 16),
          Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            Text('처음이신가요? ', style: AppText.meta.copyWith(fontSize: 14)),
            InkWell(
              onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const SignupScreen())),
              child: Text('가입하기', style: AppText.label.copyWith(color: AppColors.primary, fontWeight: FontWeight.w700)),
            ),
          ]),
        ]),
      ),
    );
  }
}

/// 35_Signup (Figma 73:6173). 입력 중 규칙 확인 (account §3.1).
class SignupScreen extends StatefulWidget {
  const SignupScreen({super.key});

  @override
  State<SignupScreen> createState() => _SignupScreenState();
}

class _SignupScreenState extends State<SignupScreen> {
  final _id = TextEditingController();
  final _pw = TextEditingController();
  final _name = TextEditingController();
  bool _obscure = true;
  bool _busy = false;
  String? _idServer;
  String? _pwServer;
  String? _nameServer;

  static final _idRe = RegExp(r'^[a-z0-9][a-z0-9._-]{3,31}$');

  @override
  void initState() {
    super.initState();
    // 서버 오류는 글자를 고쳤을 때만 지운다 (포커스·커서 이동으로도 리스너가 불린다)
    var last = ['', '', ''];
    for (final c in [_id, _pw, _name]) {
      c.addListener(() {
        final now = [_id.text, _pw.text, _name.text];
        final changed = !listEquals(now, last);
        last = now;
        setState(() {
          if (changed) _idServer = _pwServer = _nameServer = null;
        });
      });
    }
  }

  @override
  void dispose() {
    _id.dispose();
    _pw.dispose();
    _name.dispose();
    super.dispose();
  }

  String get _idv => _id.text.trim().toLowerCase();
  bool get _idOk => _idRe.hasMatch(_idv);
  bool get _pwOk =>
      _pw.text.length >= 8 && _pw.text.length <= 128 && RegExp('[A-Za-z]').hasMatch(_pw.text) && RegExp('[0-9]').hasMatch(_pw.text) && _pw.text.toLowerCase() != _idv;
  bool get _nameOk => _name.text.trim().length <= 30;

  Future<void> _submit() async {
    if (!(_idOk && _pwOk && _nameOk) || _busy) return;
    final session = context.read<AppSession>();
    setState(() => _busy = true);
    try {
      await session.signInWith(await session.api.signup(_idv, _pw.text, displayName: _name.text));
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        switch (e.code) {
          case 'login_id_taken':
            _idServer = '이미 쓰는 아이디예요';
          case 'weak_password':
            _pwServer = '8자 이상, 영문과 숫자를 함께 써 주세요';
          case 'invalid_request':
            final f = fieldOf(e);
            if (f == 'password') {
              _pwServer = '8자 이상, 영문과 숫자를 함께 써 주세요';
            } else if (f == 'display_name') {
              _nameServer = '이름은 30자까지 쓸 수 있어요';
            } else {
              _idServer = '영문 소문자·숫자 4~32자';
            }
          default:
            showInfo(context, '서버에 연결하지 못했어요');
        }
      });
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final idTyped = _id.text.isNotEmpty, pwTyped = _pw.text.isNotEmpty;
    return Scaffold(
      backgroundColor: AppColors.surface,
      appBar: AppBar(backgroundColor: AppColors.surface, leading: const BackButton()),
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.fromLTRB(20, 8, 20, 24), children: [
          Text('가입하기', style: AppText.title),
          const SizedBox(height: 4),
          Text('아이디와 비밀번호만 있으면 돼요', style: AppText.meta.copyWith(fontSize: 14)),
          const SizedBox(height: 28),
          AuthField(
            label: '아이디',
            controller: _id,
            hint: '아이디',
            autofocus: true,
            error: _idServer ?? (idTyped && !_idOk ? '영문 소문자·숫자 4~32자' : null),
            help: '영문 소문자·숫자 4~32자',
          ),
          const SizedBox(height: 20),
          AuthField(
            label: '비밀번호',
            controller: _pw,
            hint: '비밀번호',
            obscure: _obscure,
            onToggleObscure: () => setState(() => _obscure = !_obscure),
            error: _pwServer ?? (pwTyped && !_pwOk ? (_pw.text.toLowerCase() == _idv ? '아이디와 다른 비밀번호를 써 주세요' : '8자 이상, 영문과 숫자를 함께 써 주세요') : null),
            help: '8자 이상, 영문과 숫자를 함께 써 주세요',
          ),
          const SizedBox(height: 20),
          AuthField(
            label: '이름',
            controller: _name,
            hint: '선택',
            error: _nameServer ?? (!_nameOk ? '이름은 30자까지 쓸 수 있어요' : null),
            help: '구룡메이트가 부를 이름이에요. 비우면 아이디로 불러요',
            onSubmitted: (_) => _submit(),
          ),
          const SizedBox(height: 28),
          AppButton(label: '가입하고 시작하기', loading: _busy, onPressed: _idOk && _pwOk && _nameOk ? _submit : null),
        ]),
      ),
    );
  }
}

/// "계속하면 이용약관 및 개인정보처리방침에 동의하게 됩니다" — 두 문서 이름은 밑줄 링크 → 문서 화면.
class _Consent extends StatefulWidget {
  const _Consent();

  @override
  State<_Consent> createState() => _ConsentState();
}

class _ConsentState extends State<_Consent> {
  late final _terms = TapGestureRecognizer()..onTap = () => openLegalDoc(context, LegalDoc.terms);
  late final _privacy = TapGestureRecognizer()..onTap = () => openLegalDoc(context, LegalDoc.privacy);

  @override
  void dispose() {
    _terms.dispose();
    _privacy.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final link = AppText.caption.copyWith(color: AppColors.text2, fontWeight: FontWeight.w700, decoration: TextDecoration.underline, decorationColor: AppColors.text2);
    return Text.rich(
      TextSpan(children: [
        const TextSpan(text: '계속하면 '),
        TextSpan(text: '이용약관', style: link, recognizer: _terms),
        const TextSpan(text: ' 및 '),
        TextSpan(text: '개인정보처리방침', style: link, recognizer: _privacy),
        const TextSpan(text: '에 동의하게 됩니다'),
      ]),
      key: const ValueKey('login-consent'),
      style: AppText.caption,
      textAlign: TextAlign.center,
    );
  }
}
