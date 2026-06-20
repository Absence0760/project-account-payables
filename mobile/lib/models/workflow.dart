/// Read-only workflow definition + its configured steps, mirroring the backend
/// `WorkflowDefinitionResponse` (`GET /api/workflows`). Mobile surfaces the
/// list + an active-steps detail view only — the no-code builder (create/edit)
/// stays on desktop.
class WorkflowDefinition {
  final String id;
  final String name;
  final String? description;
  final bool isActive;
  final bool isDefault;
  final List<WorkflowStepConfig> steps;
  final DateTime? createdAt;
  final DateTime? updatedAt;

  const WorkflowDefinition({
    required this.id,
    required this.name,
    this.description,
    required this.isActive,
    required this.isDefault,
    required this.steps,
    this.createdAt,
    this.updatedAt,
  });

  factory WorkflowDefinition.fromJson(Map<String, dynamic> json) {
    // steps_config is `{ "steps": [ {number, type, name, enabled, config}, ... ] }`.
    final config = json['steps_config'];
    final rawSteps = (config is Map<String, dynamic> ? config['steps'] : null);
    final steps = <WorkflowStepConfig>[];
    if (rawSteps is List) {
      for (final s in rawSteps) {
        if (s is Map<String, dynamic>) {
          steps.add(WorkflowStepConfig.fromJson(s));
        }
      }
    }
    return WorkflowDefinition(
      id: json['id'] as String,
      name: json['name'] as String? ?? '',
      description: json['description'] as String?,
      isActive: json['is_active'] as bool? ?? false,
      isDefault: json['is_default'] as bool? ?? false,
      steps: steps,
      createdAt: _parseDate(json['created_at']),
      updatedAt: _parseDate(json['updated_at']),
    );
  }

  static DateTime? _parseDate(Object? raw) {
    if (raw is String && raw.isNotEmpty) return DateTime.tryParse(raw);
    return null;
  }
}

/// One configured step in a workflow definition. `config` is left as the raw
/// JSON map — the detail view summarizes the common keys (approver strategy,
/// thresholds) without modelling every builder step type.
class WorkflowStepConfig {
  final int number;
  final String type;
  final String name;
  final bool enabled;
  final Map<String, dynamic> config;

  const WorkflowStepConfig({
    required this.number,
    required this.type,
    required this.name,
    required this.enabled,
    required this.config,
  });

  factory WorkflowStepConfig.fromJson(Map<String, dynamic> json) {
    final cfg = json['config'];
    return WorkflowStepConfig(
      number: (json['number'] as num?)?.toInt() ?? 0,
      type: json['type'] as String? ?? '',
      name: json['name'] as String? ?? '',
      enabled: json['enabled'] as bool? ?? true,
      config: cfg is Map<String, dynamic> ? cfg : const {},
    );
  }

  /// Human-readable label for the step type (extraction, approval, …).
  String get typeLabel {
    switch (type) {
      case 'extraction':
        return 'Extraction';
      case 'approval':
        return 'Approval';
      case 'erp_export':
        return 'ERP Export';
      case 'done':
        return 'Done';
      case 'condition':
        return 'Condition';
      case 'parallel':
        return 'Parallel';
      case 'webhook':
        return 'Webhook';
      case 'email':
        return 'Email';
      case 'delay':
        return 'Delay';
      default:
        return type;
    }
  }
}
