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

class GuryongApp extends StatelessWidget {
  const GuryongApp({super.key});

  @override
  Widget build(BuildContext context) {
    final signedIn = context.select<AppSession, bool>((s) => s.signedIn);
    return MaterialApp(
      title: 'Planit',
      debugShowCheckedModeBanner: false,
      theme: buildTheme(),
      locale: const Locale('ko', 'KR'),
      supportedLocales: const [Locale('ko', 'KR'), Locale('en')],
      localizationsDelegates: const [
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      // 로그인 상태가 바뀌면 화면 스택을 통째로 바꾼다 (로그인 성공 시 02_Plan_List 로 replace).
      home: signedIn ? const PlanListScreen() : const LoginScreen(),
    );
  }
}
