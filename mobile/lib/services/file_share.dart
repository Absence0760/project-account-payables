import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

/// Thin, swappable wrapper around [SharePlus] so a screen can hand exported
/// bytes to the platform share sheet without taking a hard dependency on the
/// plugin's static API (which can't be exercised in a widget test). The default
/// [instance] writes the bytes to a temp file and opens the share sheet; tests
/// swap in a fake via [debugOverride] and assert it was invoked.
class FileShare {
  static FileShare instance = FileShare._();
  FileShare._();

  /// Subclass hook so a test can extend [FileShare] and override [shareBytes]
  /// without touching the platform channel.
  @visibleForTesting
  FileShare.forTest();

  /// Replace the singleton with a fake for testing; pass `null` to restore the
  /// real implementation.
  @visibleForTesting
  static void debugOverride(FileShare? fake) {
    instance = fake ?? FileShare._();
  }

  /// Write [bytes] to a temp file named [filename] and present the platform
  /// share sheet. [mimeType] tags the shared file (e.g. `text/csv`).
  Future<void> shareBytes({
    required Uint8List bytes,
    required String filename,
    required String mimeType,
  }) async {
    final dir = await getTemporaryDirectory();
    final file = File(p.join(dir.path, filename));
    await file.writeAsBytes(bytes, flush: true);
    await SharePlus.instance.share(
      ShareParams(
        files: [XFile(file.path, mimeType: mimeType, name: filename)],
      ),
    );
  }
}
