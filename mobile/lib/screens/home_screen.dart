import 'package:flutter/material.dart';

import 'package:ap_mobile/screens/approvals_screen.dart';
import 'package:ap_mobile/screens/dashboard_screen.dart';
import 'package:ap_mobile/screens/invoices_screen.dart';
import 'package:ap_mobile/screens/payments_screen.dart';
import 'package:ap_mobile/screens/settings_screen.dart';
import 'package:ap_mobile/stores/auth_store.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _currentIndex = 0;

  List<Widget> get _screens {
    final screens = <Widget>[
      const DashboardScreen(),
      const InvoicesScreen(),
    ];
    if (AuthStore.instance.canApprove) {
      screens.add(const ApprovalsScreen());
    }
    if (AuthStore.instance.canViewPayments) {
      screens.add(const PaymentsScreen());
    }
    screens.add(const SettingsScreen());
    return screens;
  }

  List<BottomNavigationBarItem> get _navItems {
    final items = <BottomNavigationBarItem>[
      const BottomNavigationBarItem(
        icon: Icon(Icons.dashboard),
        label: 'Dashboard',
      ),
      const BottomNavigationBarItem(
        icon: Icon(Icons.receipt_long),
        label: 'Invoices',
      ),
    ];
    if (AuthStore.instance.canApprove) {
      items.add(const BottomNavigationBarItem(
        icon: Icon(Icons.check_circle),
        label: 'Approvals',
      ));
    }
    if (AuthStore.instance.canViewPayments) {
      items.add(const BottomNavigationBarItem(
        icon: Icon(Icons.payments),
        label: 'Payments',
      ));
    }
    items.add(const BottomNavigationBarItem(
      icon: Icon(Icons.settings),
      label: 'Settings',
    ));
    return items;
  }

  @override
  Widget build(BuildContext context) {
    final screens = _screens;
    final safeIndex = _currentIndex.clamp(0, screens.length - 1);

    return Scaffold(
      body: IndexedStack(
        index: safeIndex,
        children: screens,
      ),
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: safeIndex,
        onTap: (i) => setState(() => _currentIndex = i),
        type: BottomNavigationBarType.fixed,
        selectedItemColor: Colors.blue,
        unselectedItemColor: Colors.grey,
        items: _navItems,
      ),
    );
  }
}
