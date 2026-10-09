import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:provider/provider.dart';

import 'core/app_session.dart';
import 'core/theme/app_theme.dart';
import 'features/auth/login_screen.dart';
import 'features/plans/plan_list_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final session = await AppSession.load();
  runApp(ChangeNotifierProvider.value(value: session, child: const GuryongApp()));
}

class GuryongApp extends StatefulWidget {
  const GuryongApp({super.key});

  @override
  State<GuryongApp> createState() => _GuryongAppState();
}

class _GuryongAppState extends State<GuryongApp> {
  final _nav = GlobalKey<NavigatorState>();
  late final AppSession _session;
  late AuthStatus _last;

  @override
  void initState() {
    super.initState();
    _session = context.read<AppSession>();
    _last = _session.status;
    _session.addListener(_onSession);
    // 저장된 refresh 토큰이 있으면 01 을 건너뛰고 새 토큰 쌍을 받는다 (00-existing §01).
    if (_session.status == AuthStatus.restoring) _session.restore();
  }

  @override
  void dispose() {
    _session.removeListener(_onSession);
    super.dispose();
  }

  /// 로그인 상태가 바뀌면(로그인 성공 → 02, refresh 실패·로그아웃 → 01) 위에 쌓인 화면(34·35·설정 등)을 걷어 낸다.
  void _onSession() {
    final s = _session.status;
    if (s != _last) _nav.currentState?.popUntil((r) => r.isFirst);
    _last = s;
  }

  @override
  Widget build(BuildContext context) {
    final status = context.select<AppSession, AuthStatus>((s) => s.status);
    return MaterialApp(
      title: '구룡메이트',
      navigatorKey: _nav,
      debugShowCheckedModeBanner: false,
      theme: buildTheme(),
      locale: const Locale('ko', 'KR'),
      supportedLocales: const [Locale('ko', 'KR'), Locale('en')],
      localizationsDelegates: const [
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      // 로그인 상태가 바뀌면 화면 스택을 통째로 바꾼다 (로그인 성공 → 02/29, refresh 실패·로그아웃 → 01).
      home: switch (status) {
        AuthStatus.restoring => const _Splash(),
        AuthStatus.signedIn => const PlanListScreen(),
        AuthStatus.signedOut => const LoginScreen(),
      },
    );
  }
}

class _Splash extends StatelessWidget {
  const _Splash();

  @override
  Widget build(BuildContext context) => const Scaffold(
        backgroundColor: AppColors.surface,
        body: Center(child: AppLogo()),
      );
}
