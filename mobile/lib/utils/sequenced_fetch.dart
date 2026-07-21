/// Request-sequencing guard for `ChangeNotifier` list stores whose `fetch()`
/// is re-triggered by a live search box or filter change (see e.g.
/// [InvoiceStore], [VendorStore]).
///
/// Without a guard, firing a new `fetch()` before the previous one's response
/// has landed can let an earlier, slower request resolve AFTER a later one and
/// clobber the store with stale results that no longer match the current
/// search/filter — with no error surfaced to the user (issue #182).
///
/// Usage — capture a token at the very start of `fetch()`, before the first
/// `await`, and guard every place the response's data gets applied:
///
/// ```dart
/// class MyStore extends ChangeNotifier with SequencedFetch {
///   Future<void> fetch() async {
///     final token = nextRequestToken();
///     _loading = true;
///     notifyListeners();
///     try {
///       final result = await MyApi.list(...);
///       if (!isCurrentRequest(token)) return; // a newer fetch beat us — discard
///       _items = result;
///       _loading = false;
///       notifyListeners();
///     } catch (e) {
///       if (!isCurrentRequest(token)) return;
///       _loading = false;
///       _error = e.toString();
///       notifyListeners();
///     }
///   }
/// }
/// ```
mixin SequencedFetch {
  int _requestSeq = 0;

  /// Call once at the start of a new request, before any `await`. Returns a
  /// token to check when that request's response resolves.
  int nextRequestToken() => ++_requestSeq;

  /// True if [token] is still the latest request issued — i.e. no newer
  /// `fetch()` call has started since. False means a newer request has
  /// already superseded this one; its response should be silently discarded
  /// rather than applied to store state.
  bool isCurrentRequest(int token) => token == _requestSeq;

  /// Test seam: reset the sequence counter. Stores' own `debugReset()` should
  /// call this so tests aren't coupled to prior singleton state.
  void debugResetSequence() {
    _requestSeq = 0;
  }
}
