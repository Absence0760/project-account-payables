import 'package:flutter/foundation.dart';

import 'package:feohledger_mobile/api/api_client.dart';
import 'package:feohledger_mobile/api/endpoints.dart';
import 'package:feohledger_mobile/models/payment.dart';
import 'package:feohledger_mobile/models/payment_queue.dart';
import 'package:feohledger_mobile/services/offline_store.dart';

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

  /// The rail a row will ACTUALLY be paid on: the backend's pin when it sent
  /// one, else the operator's pick, else ACH.
  ///
  /// A pinned row is the only rail `POST /api/payments/runs` accepts for that
  /// invoice, so a stale operator pick must never win over it. Takes the row
  /// rather than an id so the pin travels with the data and no caller can look
  /// it up wrongly — the mobile counterpart of the web page's `methodFor()`.
  PaymentMethod methodFor(PaymentQueueItem item) =>
      item.requiredMethod ?? _selection[item.id] ?? PaymentMethod.ach;

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

  /// Tick / untick a queue row.
  ///
  /// A row a payment run would refuse ([PaymentQueueItem.isSelectable]) can
  /// never enter the selection: `create_payment_run_for_invoices` hard-409s the
  /// WHOLE batch on it, so one fully-credited or exception-held invoice used to
  /// take every other invoice in the run down with it. The guard lives HERE,
  /// with the state change, rather than only on the checkbox — the screen
  /// disables the control too, but the store is the chokepoint every caller
  /// goes through.
  void toggleSelection(PaymentQueueItem item) {
    if (!item.isSelectable) return;
    if (_selection.containsKey(item.id)) {
      _selection.remove(item.id);
    } else {
      // Seed from the row's own pin when it has one — defaulting a
      // card-claimed invoice to ACH would stage a run the backend refuses.
      _selection[item.id] = methodFor(item);
    }
    notifyListeners();
  }

  /// Choose the rail for a row (implicitly selecting it).
  ///
  /// A pinned row ignores [method] entirely: the backend accepts exactly one
  /// rail for it, so honouring the operator's pick would be honouring a choice
  /// they were never really offered.
  void setMethod(PaymentQueueItem item, PaymentMethod method) {
    if (!item.isSelectable) return;
    _selection[item.id] = item.requiredMethod ?? method;
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

      _reconcileSelection();

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
          // The cached rows carry their own `blocked` / `required_method`, so
          // the offline queue is reconciled exactly like a live one — a row
          // that was blocked when it was cached stays unselectable offline.
          _reconcileSelection();
          notifyListeners();
          return;
        }
      } catch (_) {}
      _fromCache = false;
      _loading = false;
      _error = describeApiError(e);
      notifyListeners();
    }
  }

  /// Re-derive the selection against the queue that was just loaded.
  ///
  /// Three things can change under a selected row between fetches, and all
  /// three are resolved here rather than at send time, so the checkbox and the
  /// method dropdown show the truth as soon as the list refreshes:
  ///
  /// * it left the queue (paid / voided) — dropped;
  /// * it became unselectable (an exception was raised on it, a credit memo
  ///   covered it) — dropped, because a run containing it would 409 as a whole;
  /// * it gained a pinned rail (a virtual card was issued against it) — its
  ///   stored method is overwritten with the pin.
  void _reconcileSelection() {
    final byId = {for (final item in _queue) item.id: item};
    _selection.removeWhere((id, _) {
      final row = byId[id];
      return row == null || !row.isSelectable;
    });
    for (final entry in _selection.entries.toList()) {
      final pinned = byId[entry.key]!.requiredMethod;
      if (pinned != null) _selection[entry.key] = pinned;
    }
  }

  Future<void> fetchRuns() async {
    try {
      _runs = await PaymentApi.runs();
      notifyListeners();
    } catch (e) {
      _error = describeApiError(e);
      notifyListeners();
    }
  }

  /// Create a draft run from the current selection. On success, clears the
  /// selection and refreshes the queue + runs, and returns the server's
  /// human-readable message (which flags CFO approval when required). Returns
  /// null on failure with [error] set.
  Future<String?> createRunFromSelection() async {
    if (_selection.isEmpty) return null;
    // Belt-and-braces on the rail: `_reconcileSelection` already pinned every
    // selected row that has a `required_method`, but resolving again against
    // the live queue means a future selection path that skips [setMethod]
    // still can't send a rail the backend refuses. A row no longer in the
    // queue keeps its stored method — the server is the authority on it.
    final byId = {for (final item in _queue) item.id: item};
    final selections = _selection.entries.map((e) {
      final row = byId[e.key];
      return PaymentRunSelection(
        invoiceId: e.key,
        method: row == null ? e.value : methodFor(row),
      );
    }).toList();
    try {
      final result = await PaymentApi.createRun(selections);
      _selection.clear();
      await fetch();
      await fetchRuns();
      return result['message'] as String? ?? 'Payment run created';
    } catch (e) {
      _error = describeApiError(e);
      notifyListeners();
      return null;
    }
  }

  /// CFO sign-off on a draft run that trips the org's approval threshold.
  /// Returns the server message, or null on failure with [error] set.
  ///
  /// This authorizes execution; it moves no money. Refusals are routine — 409
  /// (not a draft / doesn't need sign-off / already signed off) and 403 (the
  /// maker-checker check: the run's creator can't approve their own run) — so
  /// [error] carries the server's own `detail` sentence via [describeApiError].
  Future<String?> approveRun(String runId) async {
    try {
      final result = await PaymentApi.approveRun(runId);
      await fetchRuns();
      return result['message'] as String? ?? 'Run approved by CFO';
    } catch (e) {
      _error = describeApiError(e);
      notifyListeners();
      return null;
    }
  }

  /// Execute a draft run. Returns the server message, or null on failure.
  ///
  /// A refusal here is routine, not exceptional — the CFO-approval gate and the
  /// segregation-of-duties check both answer 403 — so [error] carries the
  /// server's own `detail` sentence (see [describeApiError]), never the raw
  /// JSON body it arrived in.
  Future<String?> executeRun(String runId) async {
    try {
      final result = await PaymentApi.executeRun(runId);
      await fetch();
      await fetchRuns();
      return result['message'] as String? ?? 'Payment run executed';
    } catch (e) {
      _error = describeApiError(e);
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
      _error = describeApiError(e);
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
        // Round-trip the refusal verdict too. Without it a cached row came
        // back with `blocked: false` and no pin, so the offline queue offered
        // a checkbox on an invoice the backend refuses — the exact hazard the
        // live queue closes.
        'blocked': i.blocked,
        'blocked_reason': i.blockedReason,
        'required_method': i.requiredMethodCode,
      };
}
