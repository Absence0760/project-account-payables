import 'package:flutter/foundation.dart';

import 'package:ap_mobile/api/endpoints.dart';
import 'package:ap_mobile/models/payment.dart';
import 'package:ap_mobile/models/payment_queue.dart';
import 'package:ap_mobile/services/offline_store.dart';

/// Payment queue + summary + runs. Holds the approved-invoice queue, the
/// per-row selection (checkbox + chosen [PaymentMethod]), the KPI summary, and
/// the payment-run list. Drives "create a draft run from the selection" and
/// "execute / cancel a run". Mirrors the other ChangeNotifier singletons.
///
/// Money: every amount is carried as a display string (see [moneyToDisplay]);
/// the store never sums money on the device. The "selected total" shown in the
/// UI counts rows, not currency — the authoritative total is computed by the
/// backend when the run is created.
class PaymentQueueStore extends ChangeNotifier {
  static final PaymentQueueStore instance = PaymentQueueStore._();
  PaymentQueueStore._();

  List<PaymentQueueItem> _queue = [];
  List<PaymentRun> _runs = [];
  PaymentSummary? _summary;
  bool _loading = false;
  String? _error;
  bool _fromCache = false;

  // Per-invoice selection state. Absent ⇒ unselected. Present ⇒ selected with
  // the chosen method (defaults to ACH the first time a row is ticked).
  final Map<String, PaymentMethod> _selection = {};

  List<PaymentQueueItem> get queue => _queue;
  List<PaymentRun> get runs => _runs;
  PaymentSummary? get summary => _summary;
  bool get loading => _loading;
  String? get error => _error;
  bool get fromCache => _fromCache;

  bool isSelected(String invoiceId) => _selection.containsKey(invoiceId);
  PaymentMethod methodFor(String invoiceId) =>
      _selection[invoiceId] ?? PaymentMethod.ach;
  int get selectedCount => _selection.length;
  bool get hasSelection => _selection.isNotEmpty;

  /// Drop all in-memory state. Called on logout / forced logout through
  /// `SessionManager.endSession` — these are process-lifetime singletons, so
  /// without this a signed-out user's data would still be in memory for the
  /// next account on the device. Tests use it to decouple from run order.
  void reset() {
    _queue = [];
    _runs = [];
    _summary = null;
    _loading = false;
    _error = null;
    _fromCache = false;
    _selection.clear();
  }

  void toggleSelection(String invoiceId) {
    if (_selection.containsKey(invoiceId)) {
      _selection.remove(invoiceId);
    } else {
      _selection[invoiceId] = PaymentMethod.ach;
    }
    notifyListeners();
  }

  void setMethod(String invoiceId, PaymentMethod method) {
    // Picking a method implicitly selects the row.
    _selection[invoiceId] = method;
    notifyListeners();
  }

  void clearSelection() {
    _selection.clear();
    notifyListeners();
  }

  /// Load the queue + summary together (the two halves of the payments-page
  /// header). Runs are loaded separately by [fetchRuns].
  Future<void> fetch() async {
    _loading = true;
    _error = null;
    notifyListeners();

    try {
      final results = await Future.wait([
        PaymentApi.queue(),
        PaymentApi.summary(),
      ]);
      _queue = results[0] as List<PaymentQueueItem>;
      _summary = results[1] as PaymentSummary;
      _fromCache = false;
      _loading = false;

      // Drop selections for invoices that have left the queue (paid / voided).
      _selection.removeWhere(
        (id, _) => !_queue.any((item) => item.id == id),
      );

      await OfflineStore.instance.put(
        'payment_queue',
        _queue.map(_queueItemToJson).toList(),
      );

      notifyListeners();
    } catch (e) {
      try {
        final cached = await OfflineStore.instance.get('payment_queue');
        if (cached != null) {
          _queue = (cached as List)
              .map((j) => PaymentQueueItem.fromJson(j as Map<String, dynamic>))
              .toList();
          _fromCache = true;
          _loading = false;
          notifyListeners();
          return;
        }
      } catch (_) {}
      _fromCache = false;
      _loading = false;
      _error = e.toString();
      notifyListeners();
    }
  }

  Future<void> fetchRuns() async {
    try {
      _runs = await PaymentApi.runs();
      notifyListeners();
    } catch (e) {
      _error = e.toString();
      notifyListeners();
    }
  }

  /// Create a draft run from the current selection. On success, clears the
  /// selection and refreshes the queue + runs, and returns the server's
  /// human-readable message (which flags CFO approval when required). Returns
  /// null on failure with [error] set.
  Future<String?> createRunFromSelection() async {
    if (_selection.isEmpty) return null;
    final selections = _selection.entries
        .map((e) => PaymentRunSelection(invoiceId: e.key, method: e.value))
        .toList();
    try {
      final result = await PaymentApi.createRun(selections);
      _selection.clear();
      await fetch();
      await fetchRuns();
      return result['message'] as String? ?? 'Payment run created';
    } catch (e) {
      _error = e.toString();
      notifyListeners();
      return null;
    }
  }

  /// Execute a draft run. Returns the server message, or null on failure.
  Future<String?> executeRun(String runId) async {
    try {
      final result = await PaymentApi.executeRun(runId);
      await fetch();
      await fetchRuns();
      return result['message'] as String? ?? 'Payment run executed';
    } catch (e) {
      _error = e.toString();
      notifyListeners();
      return null;
    }
  }

  /// Cancel a draft run (releases its invoices back to the queue).
  Future<String?> cancelRun(String runId) async {
    try {
      final result = await PaymentApi.cancelRun(runId);
      await fetch();
      await fetchRuns();
      return result['message'] as String? ?? 'Payment run cancelled';
    } catch (e) {
      _error = e.toString();
      notifyListeners();
      return null;
    }
  }

  Map<String, dynamic> _queueItemToJson(PaymentQueueItem i) => {
        'id': i.id,
        'invoice_number': i.invoiceNumber,
        'vendor_name': i.vendorName,
        'amount': i.amountDisplay,
        'currency': i.currency,
        'due_date': i.dueDate?.toIso8601String(),
        'payment_terms': i.paymentTerms,
        'status': i.status,
        'is_overdue': i.isOverdue,
        'discount_eligible': i.discountEligible,
        'discount_date': i.discountDate?.toIso8601String(),
        'discount_amount': i.discountAmountDisplay,
      };
}
