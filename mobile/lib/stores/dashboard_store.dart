import 'package:flutter/foundation.dart';

import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/payment.dart';
import 'package:ap_mobile/services/offline_store.dart';

class DashboardStore extends ChangeNotifier {
  static final DashboardStore instance = DashboardStore._();
  DashboardStore._();

  DashboardData? _data;
  bool _loading = false;
  String? _error;
  bool _fromCache = false;

  DashboardData? get data => _data;
  bool get loading => _loading;
  String? get error => _error;
  bool get fromCache => _fromCache;

  Future<void> fetch() async {
    _loading = true;
    _error = null;
    notifyListeners();

    try {
      final result = await OfflineStore.instance.cachedFetch<DashboardData>(
        key: 'dashboard',
        fetch: DashboardApi.get,
        toCache: (d) => {
          'total_invoices': d.totalInvoices,
          'total_amount': d.totalAmount,
          'pipeline': d.pipeline,
          'vendor_spend': d.topVendors
              .map((v) => {'vendor': v.vendorName, 'amount': v.totalAmount})
              .toList(),
          'aging': {
            'current': d.aging.current,
            'days_30': d.aging.thirtyDays,
            'days_60': d.aging.sixtyDays,
            'days_90_plus': d.aging.ninetyPlus,
          },
          'monthly_trend': d.trends
              .map(
                  (t) => {'month': t.month, 'count': t.count, 'amount': t.amount})
              .toList(),
          'upcoming_payments': [],
        },
        fromCache: (json) =>
            DashboardData.fromJson(json as Map<String, dynamic>),
      );
      _data = result.data;
      _fromCache = result.fromCache;
      _loading = false;
      notifyListeners();
    } catch (e) {
      // Try loading from cache on failure
      try {
        final cached = await OfflineStore.instance.get('dashboard');
        if (cached != null) {
          _data = DashboardData.fromJson(cached as Map<String, dynamic>);
          _fromCache = true;
          _loading = false;
          notifyListeners();
          return;
        }
      } catch (_) {}
      // No cache to fall back on — don't keep claiming the data is cached.
      _fromCache = false;
      _loading = false;
      _error = e.toString();
      notifyListeners();
    }
  }
}
