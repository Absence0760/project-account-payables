import 'package:flutter/material.dart';

import 'package:ap_mobile/screens/home_screen.dart';
import 'package:ap_mobile/screens/mfa_screen.dart';
import 'package:ap_mobile/stores/auth_store.dart';
import 'package:ap_mobile/utils/a11y.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();
  final _tenantController = TextEditingController(text: 'acme');
  final _formKey = GlobalKey<FormState>();
  bool _obscurePassword = true;

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    _tenantController.dispose();
    super.dispose();
  }

  Future<void> _login() async {
    if (!_formKey.currentState!.validate()) return;

    final result = await AuthStore.instance.login(
      _emailController.text.trim(),
      _passwordController.text,
      _tenantController.text.trim(),
    );

    if (!mounted) return;
    switch (result.outcome) {
      case LoginOutcome.success:
        Navigator.of(context).pushReplacement(
          MaterialPageRoute(builder: (_) => const HomeScreen()),
        );
      case LoginOutcome.mfaRequired:
        // Password accepted; route to the second-factor code-entry screen.
        // No token is stored yet — the MFA verify mints it.
        Navigator.of(context).push(
          MaterialPageRoute(
            builder: (_) => MfaScreen(challenge: result.challenge!),
          ),
        );
      case LoginOutcome.failure:
        // Live-region announce the failure so screen-reader users hear it
        // without re-scanning the form (WCAG 4.1.3).
        final error = AuthStore.instance.error;
        if (error != null) {
          A11y.announce(context, error);
        }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(32),
            child: Form(
              key: _formKey,
              child: ListenableBuilder(
                listenable: AuthStore.instance,
                builder: (context, _) {
                  final auth = AuthStore.instance;
                  return Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      // Brand mark — decorative; hidden from assistive tech.
                      const ExcludeSemantics(
                        child: Icon(
                          Icons.receipt_long,
                          size: 64,
                          color: Colors.blue,
                        ),
                      ),
                      const SizedBox(height: 16),
                      const Text(
                        'Better AP',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          fontSize: 28,
                          fontWeight: FontWeight.bold,
                          letterSpacing: -0.5,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'Accounts Payable, Made Simple',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          fontSize: 14,
                          // shade700 clears AA contrast (shade500 is 2.55:1).
                          color: Colors.grey.shade700,
                        ),
                      ),
                      const SizedBox(height: 32),
                      TextFormField(
                        controller: _tenantController,
                        decoration: const InputDecoration(
                          labelText: 'Tenant',
                          hintText: 'acme',
                          prefixIcon: Icon(Icons.business),
                          border: OutlineInputBorder(),
                        ),
                        validator: (v) =>
                            v == null || v.isEmpty ? 'Required' : null,
                      ),
                      const SizedBox(height: 16),
                      TextFormField(
                        controller: _emailController,
                        decoration: const InputDecoration(
                          labelText: 'Email',
                          prefixIcon: Icon(Icons.email),
                          border: OutlineInputBorder(),
                        ),
                        keyboardType: TextInputType.emailAddress,
                        autocorrect: false,
                        validator: (v) =>
                            v == null || v.isEmpty ? 'Required' : null,
                      ),
                      const SizedBox(height: 16),
                      TextFormField(
                        controller: _passwordController,
                        decoration: InputDecoration(
                          labelText: 'Password',
                          prefixIcon: const Icon(Icons.lock),
                          border: const OutlineInputBorder(),
                          // Labelled, ≥48dp show/hide toggle (WCAG 4.1.2). The
                          // explicit Semantics label is the screen-reader name;
                          // the tooltip serves sighted hover/long-press.
                          suffixIcon: Semantics(
                            label: _obscurePassword
                                ? 'Show password'
                                : 'Hide password',
                            button: true,
                            child: IconButton(
                              tooltip: _obscurePassword
                                  ? 'Show password'
                                  : 'Hide password',
                              icon: Icon(
                                _obscurePassword
                                    ? Icons.visibility
                                    : Icons.visibility_off,
                              ),
                              onPressed: () => setState(
                                () => _obscurePassword = !_obscurePassword,
                              ),
                            ),
                          ),
                        ),
                        obscureText: _obscurePassword,
                        validator: (v) =>
                            v == null || v.isEmpty ? 'Required' : null,
                        onFieldSubmitted: (_) => _login(),
                      ),
                      if (auth.error != null) ...[
                        const SizedBox(height: 12),
                        Semantics(
                          liveRegion: true,
                          child: Text(
                            auth.error!,
                            // shade700 keeps the error legible at AA contrast.
                            style: TextStyle(color: Colors.red.shade700),
                            textAlign: TextAlign.center,
                          ),
                        ),
                      ],
                      const SizedBox(height: 24),
                      FilledButton(
                        onPressed: auth.loading ? null : _login,
                        style: FilledButton.styleFrom(
                          padding: const EdgeInsets.symmetric(vertical: 16),
                        ),
                        child: auth.loading
                            ? const SizedBox(
                                height: 20,
                                width: 20,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                  color: Colors.white,
                                ),
                              )
                            : const Text('Sign In'),
                      ),
                    ],
                  );
                },
              ),
            ),
          ),
        ),
      ),
    );
  }
}
