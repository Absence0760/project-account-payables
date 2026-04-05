export type WorkflowStepType = 'extraction' | 'approval' | 'erp_export';

export const STEP_TYPE_LABELS: Record<WorkflowStepType, string> = {
	extraction: 'Data Extraction',
	approval: 'Approval',
	erp_export: 'ERP Export',
};

export const STEP_TYPE_DESCRIPTIONS: Record<WorkflowStepType, string> = {
	extraction: 'AI-powered data extraction from uploaded invoices',
	approval: 'Human review and approval of invoice data',
	erp_export: 'Export approved invoices to your ERP system',
};

export interface ExtractionStepConfig {
	auto_approve_enabled: boolean;
	auto_approve_threshold: number;
}

export interface ApprovalStepConfig {
	required: boolean;
	approver_id: string | null;
	approver_ids: string[];
	approver_strategy: 'manual' | 'specific' | 'auto';
}

export type ErpExportFormat = 'json' | 'xml' | 'csv' | 'cxml' | 'edi';

export const ERP_FORMAT_LABELS: Record<ErpExportFormat, string> = {
	json: 'JSON',
	xml: 'XML',
	csv: 'CSV',
	cxml: 'cXML',
	edi: 'EDI',
};

export interface ErpExportStepConfig {
	erp_system: string;
	export_format: ErpExportFormat;
	endpoint_url: string;
}

export type StepConfig = ExtractionStepConfig | ApprovalStepConfig | ErpExportStepConfig;

export interface WorkflowStep {
	number: number;
	type: WorkflowStepType;
	name: string;
	enabled: boolean;
	config: StepConfig;
}

export interface WorkflowDefinition {
	id: string;
	name: string;
	description: string | null;
	steps_config: { steps: WorkflowStep[] };
	is_active: boolean;
	is_default: boolean;
	created_at: string;
	updated_at: string | null;
}

export const DEFAULT_EXTRACTION_CONFIG: ExtractionStepConfig = {
	auto_approve_enabled: false,
	auto_approve_threshold: 0.95,
};

export const DEFAULT_APPROVAL_CONFIG: ApprovalStepConfig = {
	required: true,
	approver_id: null,
	approver_ids: [],
	approver_strategy: 'manual',
};

export const DEFAULT_ERP_CONFIG: ErpExportStepConfig = {
	erp_system: 'default',
	export_format: 'json',
	endpoint_url: '',
};
