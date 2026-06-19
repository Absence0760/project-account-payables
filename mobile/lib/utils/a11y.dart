import 'package:flutter/semantics.dart' show SemanticsService;
import 'package:flutter/widgets.dart';

/// Accessibility helpers shared across screens.
///
/// Funnels live-region announcements (WCAG 4.1.3) through one place so every
/// caller uses the non-deprecated [SemanticsService.sendAnnouncement] API and
/// resolves [TextDirection] from the active [Directionality] — several screens
/// import `intl`, which also exports a `TextDirection`, so keeping the
/// announce plumbing here avoids that ambiguity at each call site.
class A11y {
  const A11y._();

  /// Announce [message] to assistive tech via the current view.
  ///
  /// State changes that aren't seamlessly announced by the platform (a toast,
  /// a row vanishing after approval, an inline error) should call this so
  /// screen-reader users hear the result. No-op if the view can't be resolved.
  static void announce(BuildContext context, String message) {
    if (message.isEmpty) return;
    final view = View.maybeOf(context);
    if (view == null) return;
    final direction = Directionality.maybeOf(context) ?? TextDirection.ltr;
    SemanticsService.sendAnnouncement(view, message, direction);
  }
}
