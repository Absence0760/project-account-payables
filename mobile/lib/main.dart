import 'package:flutter/material.dart';

import 'package:feohledger_mobile/l10n/gen/app_localizations.dart';
import 'package:feohledger_mobile/screens/home_screen.dart';
import 'package:feohledger_mobile/screens/login_screen.dart';
import 'package:feohledger_mobile/services/biometric_service.dart';
import 'package:feohledger_mobile/services/push_service.dart';
import 'package:feohledger_mobile/stores/auth_store.dart';
import 'package:feohledger_mobile/stores/locale_store.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Load the device's persisted display-language choice before first build so
  // MaterialApp.locale is correct on the very first frame.
  await LocaleStore.instance.init();
  runApp(const APApp());
}

class APApp extends StatelessWidget {
  const APApp({super.key});

  @override
  Widget build(BuildContext context) {
    // Rebuild MaterialApp whenever the device locale choice changes so the
    // whole tree re-localizes live (no restart). `locale: null` follows the
    // system locale (resolved against supportedLocales).
    return ListenableBuilder(
      listenable: LocaleStore.instance,
      builder: (context, _) {
        return MaterialApp(
          title: 'FeohLedger',
          debugShowCheckedModeBanner: false,
          navigatorKey: PushService.navigatorKey,
          locale: LocaleStore.instance.locale,
          localizationsDelegates: AppLocalizations.localizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          theme: ThemeData(
            colorScheme: ColorScheme.fromSeed(seedColor: Colors.blue),
            useMaterial3: true,
            appBarTheme: const AppBarTheme(
              centerTitle: false,
              elevation: 0,
            ),
          ),
          home: const SplashScreen(),
        );
      },
    );
  }
}

class SplashScreen extends StatefulWidget {
  const SplashScreen({super.key});

  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen> {
  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    // Initialize push notifications (no-op if Firebase not configured)
    await PushService.instance.init();

    final hasSession = await AuthStore.instance.init();
    if (!mounted) return;

    if (hasSession) {
      // Has a valid session — check biometric lock
      final bioEnabled = await BiometricService.instance.isEnabled;
      if (bioEnabled) {
        final authenticated = await BiometricService.instance.authenticate();
        if (!authenticated) {
          // Biometric failed — go to login screen
          if (mounted) {
            Navigator.of(context).pushReplacement(
              MaterialPageRoute(builder: (_) => const LoginScreen()),
            );
          }
          return;
        }
      }
      if (mounted) {
        Navigator.of(context).pushReplacement(
          MaterialPageRoute(builder: (_) => const HomeScreen()),
        );
      }
    } else {
      if (mounted) {
        Navigator.of(context).pushReplacement(
          MaterialPageRoute(builder: (_) => const LoginScreen()),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.receipt_long, size: 64, color: Colors.blue),
            SizedBox(height: 16),
            CircularProgressIndicator(),
          ],
        ),
      ),
    );
  }
}
