import 'package:flutter/foundation.dart';

import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/cash_flow.dart';

/// CFO / admin predictive cash-flow store over [CashFlowApi]. Holds the
/// forecast + cash-position payload for the currently selected horizon and
/// exposes loading / error states for the screen.
///
/// Not offline-cached: the cash position is a privileged, fast-moving CFO read
/// (opening balance + breach alerts), so a stale on-device copy would be more
/// misleading than useful — we refetch live and surface a Retry on failure.
class CashFlowStore extends ChangeNotifier {
  static final CashFlowStore instance = CashFlowStore._();
  CashFlowStore._();

  /// The horizon options offered as filter chips, in days.
  static const horizonOptions = <int>[30, 60, 90];

  CashFlowData? _data;
  bool _loading = false;
  String? _error;
  int _horizonDays = 90;

  CashFlowData? get data => _data;
  bool get loading => _loading;
  String? get error => _error;
  int get horizonDays => _horizonDays;

  /// Drop all in-memory state. Called on logout / forced logout through
  /// `SessionManager.endSession` — these are process-lifetime singletons, so
  /// without this a signed-out user's data would still be in memory for the
  /// next account on the device. Tests use it to decouple from run order.
  void reset() {
    _data = null;
    _loading = false;
    _error = null;
    _horizonDays = 90;
  }

  /// Switch the forecast horizon (30 / 60 / 90 days) and refetch. No-ops if the
  /// horizon is unchanged so a repeated chip tap doesn't re-hit the API.
  Future<void> setHorizon(int days) async {
    if (days == _horizonDays) return;
    _horizonDays = days;
    await fetch();
  }

  Future<void> fetch() async {
    _loading = true;
    _error = null;
    notifyListeners();

    try {
      _data = await CashFlowApi.get(horizonDays: _horizonDays);
      _loading = false;
      notifyListeners();
    } catch (e) {
      _data = null;
      _loading = false;
      _error = e.toString();
      notifyListeners();
    }
  }
}
