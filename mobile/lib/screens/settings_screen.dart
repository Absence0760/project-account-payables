import 'package:flutter/material.dart';

import 'package:ap_mobile/config.dart';
import 'package:ap_mobile/screens/login_screen.dart';
import 'package:ap_mobile/stores/auth_store.dart';

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListenableBuilder(
        listenable: AuthStore.instance,
        builder: (context, _) {
          final user = AuthStore.instance.user;

          return ListView(
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
                              label: Text(r, style: const TextStyle(fontSize: 12)),
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

              const Divider(height: 32),

              // Logout
              ListTile(
                leading: const Icon(Icons.logout, color: Colors.red),
                title: const Text(
                  'Sign Out',
                  style: TextStyle(color: Colors.red),
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
          );
        },
      ),
    );
  }
}
