import 'package:flutter/material.dart';

import 'package:ap_mobile/config.dart';
import 'package:ap_mobile/screens/login_screen.dart';
import 'package:ap_mobile/services/biometric_service.dart';
import 'package:ap_mobile/stores/auth_store.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  bool _bioAvailable = false;
  bool _bioEnabled = false;

  @override
  void initState() {
    super.initState();
    _loadBiometricState();
  }

  Future<void> _loadBiometricState() async {
    final available = await BiometricService.instance.isAvailable;
    final enabled = await BiometricService.instance.isEnabled;
    if (mounted) {
      setState(() {
        _bioAvailable = available;
        _bioEnabled = enabled;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final user = AuthStore.instance.user;

    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        children: [
          // User info
          Container(
            padding: const EdgeInsets.all(24),
            color: Colors.blue.withValues(alpha: 0.05),
            child: Column(
              children: [
                CircleAvatar(
                  radius: 32,
                  backgroundColor: Colors.blue,
                  child: Text(
                    user?.fullName.isNotEmpty == true
                        ? user!.fullName[0].toUpperCase()
                        : '?',
                    style: const TextStyle(
                      fontSize: 24,
                      color: Colors.white,
                    ),
                  ),
                ),
                const SizedBox(height: 12),
                Text(
                  user?.fullName ?? 'Unknown',
                  style: const TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  user?.email ?? '',
                  style: TextStyle(color: Colors.grey.shade600),
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 6,
                  children: (user?.roles ?? [])
                      .map(
                        (r) => Chip(
                          label:
                              Text(r, style: const TextStyle(fontSize: 12)),
                          visualDensity: VisualDensity.compact,
                        ),
                      )
                      .toList(),
                ),
              ],
            ),
          ),

          // Connection
          const SizedBox(height: 16),
          ListTile(
            leading: const Icon(Icons.business),
            title: const Text('Tenant'),
            subtitle: Text(AppConfig.tenantSlug ?? 'Not set'),
          ),
          ListTile(
            leading: const Icon(Icons.link),
            title: const Text('API Server'),
            subtitle: Text(AppConfig.apiBaseUrl),
          ),

          // Security
          if (_bioAvailable) ...[
            const Divider(height: 32),
            SwitchListTile(
              secondary: const Icon(Icons.fingerprint),
              title: const Text('Biometric Unlock'),
              subtitle: const Text('Use fingerprint or face to unlock'),
              value: _bioEnabled,
              onChanged: (enabled) async {
                if (enabled) {
                  final ok = await BiometricService.instance.authenticate();
                  if (!ok) return;
                }
                await BiometricService.instance.setEnabled(enabled);
                setState(() => _bioEnabled = enabled);
              },
            ),
          ],

          const Divider(height: 32),

          // Logout
          ListTile(
            leading: Icon(Icons.logout, color: Colors.red.shade700),
            title: Text(
              'Sign Out',
              // shade700 keeps the destructive label at AA contrast.
              style: TextStyle(color: Colors.red.shade700),
            ),
            onTap: () async {
              await AuthStore.instance.logout();
              if (context.mounted) {
                Navigator.of(context).pushAndRemoveUntil(
                  MaterialPageRoute(
                    builder: (_) => const LoginScreen(),
                  ),
                  (_) => false,
                );
              }
            },
          ),
        ],
      ),
    );
  }
}
