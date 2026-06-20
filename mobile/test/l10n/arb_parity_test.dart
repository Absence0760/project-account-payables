import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

// ARB key-parity guard — the mobile counterpart of the web
// `frontend/src/lib/i18n/messages_parity.test.ts`. Flutter's gen-l10n already
// fails the build if a non-template ARB has an *extra* key, but it silently
// falls back to the template for a *missing* one — so a forgotten translation
// would ship as English with no warning. This test reads every ARB straight
// off disk and asserts each shipped locale has exactly the template's key set,
// no empty values, and the same `{placeholder}` tokens as English.

const _arbDir = 'lib/l10n';
const _templateFile = 'app_en.arb';

/// All non-template ARB catalogues, including the `pt` base fallback that
/// gen-l10n requires alongside `pt_BR`.
const _localeFiles = <String>[
  'app_de.arb',
  'app_es.arb',
  'app_fr.arb',
  'app_ja.arb',
  'app_pt.arb',
  'app_pt_BR.arb',
];

/// Message keys only — drops ARB metadata (`@@locale`, `@key` descriptors).
Map<String, String> _messages(Map<String, dynamic> arb) {
  final out = <String, String>{};
  arb.forEach((key, value) {
    if (key.startsWith('@')) return;
    out[key] = value as String;
  });
  return out;
}

/// The *set* of `{name}` placeholder tokens (excludes ICU `{count, plural, …}`
/// blocks, which carry a comma). Deduplicated + sorted so a token that appears
/// once per plural arm compares equal across locales with a different number of
/// arms — Japanese has no grammatical plural, so its plural blocks carry only
/// an `other` arm (one `{count}`) where English carries `one` + `other` (two
/// `{count}`s); both reference the same single placeholder. Mirrors the web
/// `messages_parity` intent (same placeholder set, not the same multiplicity).
List<String> _placeholders(String s) {
  final matches = RegExp(r'\{[a-zA-Z0-9_]+\}').allMatches(s);
  final names = matches.map((m) => m.group(0)!).toSet().toList()..sort();
  return names;
}

/// Reads + parses an ARB. Throws (not `expect`) so it is safe to call at the
/// top level of `main()` while building the parameterized test list.
Map<String, dynamic> _readArb(String fileName) {
  final file = File('$_arbDir/$fileName');
  if (!file.existsSync()) {
    throw StateError('$fileName is missing');
  }
  return jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
}

void main() {
  final template = _messages(_readArb(_templateFile));
  final templateKeys = template.keys.toList()..sort();

  test('the English template catalogue is non-empty', () {
    expect(templateKeys, isNotEmpty);
  });

  for (final fileName in _localeFiles) {
    test('$fileName: complete, non-empty, placeholder-faithful', () {
      final dict = _messages(_readArb(fileName));
      final keys = dict.keys.toList()..sort();

      expect(keys, equals(templateKeys),
          reason: '$fileName key set differs from $_templateFile');

      for (final key in templateKeys) {
        expect(dict[key]!.trim(), isNotEmpty, reason: '$fileName.$key is empty');
        expect(_placeholders(dict[key]!), equals(_placeholders(template[key]!)),
            reason: '$fileName.$key placeholder mismatch');
      }
    });
  }
}
