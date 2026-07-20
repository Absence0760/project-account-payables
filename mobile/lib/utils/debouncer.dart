import 'dart:async';

/// Debounces a rapidly-repeated action (e.g. a search box's `onChanged`) so it
/// only runs once the caller has been quiet for [delay] — matching the web
/// app's 300ms search-debounce convention (see `frontend/src/routes/invoices/
/// +page.svelte`). Without this, a search box driving a store `fetch()` on
/// every keystroke fires one request per character, which both hammers the
/// API and — paired with no request-sequencing guard — lets an earlier, slower
/// response land after a later one and clobber the list (issue #182).
///
/// Owned by a `State`; call [cancel] from `dispose()` so no `Timer` outlives
/// the widget.
class Debouncer {
  Debouncer({this.delay = const Duration(milliseconds: 300)});

  final Duration delay;
  Timer? _timer;

  /// Cancel any pending call and schedule [action] to run after [delay].
  void run(void Function() action) {
    _timer?.cancel();
    _timer = Timer(delay, action);
  }

  /// Cancel any pending call without running it. Call from `dispose()`.
  void cancel() {
    _timer?.cancel();
    _timer = null;
  }
}
