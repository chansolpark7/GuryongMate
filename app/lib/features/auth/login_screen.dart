import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/app_session.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';

/// 01_Login. account 서비스가 아직 없어 이메일(사용자 ID)만 정해 X-User-Id 로 쓴다.
class LoginScreen extends StatelessWidget {
  const LoginScreen({super.key});

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
            Text('Planit', style: AppText.display, textAlign: TextAlign.center),
            const SizedBox(height: 6),
            Text('Plan smarter with AI', style: AppText.label.copyWith(fontSize: 16, color: AppColors.text2), textAlign: TextAlign.center),
            const SizedBox(height: 4),
            Text('AI가 함께 편집하는 여행 플래너', style: AppText.meta.copyWith(fontSize: 14, color: AppColors.text3), textAlign: TextAlign.center),
            const Spacer(flex: 6),
            AppButton(
              label: 'Google로 계속하기',
              kind: ButtonKind.secondary,
              leading: const _GoogleMark(),
              // 로그인 버튼 위에 띄워 "이메일로 계속하기"를 가리지 않게 한다
              onPressed: () => showInfo(context, 'Google 로그인은 계정 서비스가 준비되면 연결돼요. 이메일로 계속해 주세요.',
                  margin: const EdgeInsets.fromLTRB(16, 0, 16, 180)),
            ),
            const SizedBox(height: 12),
            AppButton(
              label: '이메일로 계속하기',
              icon: Icons.mail_outline,
              onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const EmailLoginScreen())),
            ),
            const SizedBox(height: 16),
            Text('계속하면 이용약관 및 개인정보처리방침에 동의하게 됩니다', style: AppText.caption, textAlign: TextAlign.center),
            const SizedBox(height: 24),
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

/// 이메일 입력 화면. 서버 주소도 여기서 바꾼다 (에뮬레이터·실기기 테스트용).
class EmailLoginScreen extends StatefulWidget {
  const EmailLoginScreen({super.key});

  @override
  State<EmailLoginScreen> createState() => _EmailLoginScreenState();
}

class _EmailLoginScreenState extends State<EmailLoginScreen> {
  final _email = TextEditingController();
  late final TextEditingController _server;
  bool _checking = false;

  @override
  void initState() {
    super.initState();
    _server = TextEditingController(text: context.read<AppSession>().baseUrl);
    _email.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _email.dispose();
    _server.dispose();
    super.dispose();
  }

  bool get _valid => _email.text.trim().isNotEmpty && _email.text.trim().length <= 64;

  Future<void> _submit() async {
    final session = context.read<AppSession>();
    setState(() => _checking = true);
    try {
      await session.setBaseUrl(_server.text);
      await session.signIn(_email.text);
      // 연결 확인 (실패해도 로그인은 유지하고 안내만 한다)
      await session.api.plans();
      if (mounted) Navigator.of(context).popUntil((r) => r.isFirst);
    } catch (e) {
      if (mounted) {
        showError(context, e);
        Navigator.of(context).popUntil((r) => r.isFirst);
      }
    } finally {
      if (mounted) setState(() => _checking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.surface,
      appBar: AppBar(backgroundColor: AppColors.surface, title: Text('이메일로 계속하기', style: AppText.headline), centerTitle: true),
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.all(20), children: [
          Text('이메일', style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          TextField(
            controller: _email,
            keyboardType: TextInputType.emailAddress,
            autofocus: true,
            decoration: const InputDecoration(hintText: 'seoyun@example.com'),
            onSubmitted: (_) => _valid ? _submit() : null,
          ),
          const SizedBox(height: 8),
          Text('같은 이메일로 들어오면 내 여행 계획을 이어서 볼 수 있어요.', style: AppText.caption),
          const SizedBox(height: 28),
          Theme(
            data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
            child: ExpansionTile(
              tilePadding: EdgeInsets.zero,
              title: Text('서버 설정', style: AppText.label.copyWith(color: AppColors.text2)),
              children: [
                TextField(controller: _server, decoration: const InputDecoration(hintText: 'http://127.0.0.1:8000')),
                const SizedBox(height: 6),
                Text('Android 에뮬레이터는 http://10.0.2.2:8000, 실기기는 PC 의 IP 주소를 넣으세요.', style: AppText.caption),
              ],
            ),
          ),
        ]),
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 16),
          child: AppButton(label: '계속하기', loading: _checking, onPressed: _valid ? _submit : null),
        ),
      ),
    );
  }
}
