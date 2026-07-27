import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:feohledger_mobile/api/api_client.dart';
import 'package:feohledger_mobile/stores/workflow_store.dart';

Map<String, dynamic> _workflowJson(
  String id, {
  String name = 'Default Workflow',
  bool isActive = true,
  bool isDefault = true,
  List<Map<String, dynamic>>? steps,
}) =>
    {
      'id': id,
      'name': name,
      'description': 'Standard invoice processing',
      'is_active': isActive,
      'is_default': isDefault,
      'steps_config': {
        'steps': steps ??
            [
              {'number': 1, 'type': 'extraction', 'name': 'Extract', 'config': {}},
              {
                'number': 2,
                'type': 'approval',
                'name': 'Review',
                'enabled': true,
                'config': {
                  'approver_strategy': 'manual',
                  'approver_ids': ['u1'],
                },
              },
            ],
      },
      'created_at': '2026-01-01T12:00:00',
      'updated_at': null,
    };

http.Response _list(List<Map<String, dynamic>> items) => http.Response(
      jsonEncode({'items': items, 'total': items.length, 'page': 1}),
      200,
      headers: {'content-type': 'application/json'},
    );

void main() {
  final store = WorkflowStore.instance;

  setUp(() {
    WorkflowStore.instance.reset();
    ApiClient().debugConfigure();
  });

  test('fetch populates workflows and parses their steps', () async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => _list([_workflowJson('wf1')])),
    );

    await store.fetch();

    expect(store.workflows, hasLength(1));
    expect(store.error, isNull);
    expect(store.loading, isFalse);
    final wf = store.workflows.first;
    expect(wf.id, 'wf1');
    expect(wf.isDefault, isTrue);
    expect(wf.steps, hasLength(2));
    expect(wf.steps.first.type, 'extraction');
    expect(wf.steps[1].type, 'approval');
    expect(wf.steps[1].typeLabel, 'Approval');
  });

  test('fetch surfaces an error when the request fails', () async {
    ApiClient().debugConfigure(
      client: MockClient((req) async => http.Response('boom', 500)),
    );

    await store.fetch();

    expect(store.error, isNotNull);
    expect(store.workflows, isEmpty);
    expect(store.loading, isFalse);
  });

  test('a workflow with no steps_config parses to an empty step list',
      () async {
    ApiClient().debugConfigure(
      client: MockClient(
        (req) async => _list([
          {
            'id': 'wf2',
            'name': 'Empty',
            'is_active': false,
            'is_default': false,
            'steps_config': {},
            'created_at': '2026-01-01T12:00:00',
          }
        ]),
      ),
    );

    await store.fetch();

    expect(store.workflows, hasLength(1));
    expect(store.workflows.first.steps, isEmpty);
    expect(store.workflows.first.isActive, isFalse);
  });
}
