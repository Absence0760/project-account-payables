enum PaymentMethod {
  ach('ach'),
  wire('wire'),
  check('check'),
  virtualCard('virtual_card');

  const PaymentMethod(this.value);
  final String value;

  static PaymentMethod fromString(String s) {
    return PaymentMethod.values.firstWhere(
      (e) => e.value == s,
      orElse: () => PaymentMethod.ach,
    );
  }

  String get label => switch (this) {
    PaymentMethod.ach => 'ACH',
    PaymentMethod.wire => 'Wire',
    PaymentMethod.check => 'Check',
    PaymentMethod.virtualCard => 'Virtual Card',
  };
}

enum PaymentStatus {
  pending('pending'),
  processing('processing'),
  completed('completed'),
  failed('failed'),
  cancelled('cancelled');

  const PaymentStatus(this.value);
  final String value;

  static PaymentStatus fromString(String s) {
    return PaymentStatus.values.firstWhere(
      (e) => e.value == s,
      orElse: () => PaymentStatus.pending,
    );
  }
}

class Payment {
  final String id;
  final String invoiceId;
  final double amount;
  final PaymentMethod method;
  final PaymentStatus status;
  final String? reference;
  final DateTime createdAt;

  Payment({
    required this.id,
    required this.invoiceId,
    required this.amount,
    required this.method,
    required this.status,
    this.reference,
    required this.createdAt,
  });

  factory Payment.fromJson(Map<String, dynamic> json) {
    return Payment(
      id: json['id'] as String,
      invoiceId: json['invoice_id'] as String,
      amount: (json['amount'] as num).toDouble(),
      method: PaymentMethod.fromString(json['method'] as String),
      status: PaymentStatus.fromString(json['status'] as String),
      reference: json['reference'] as String?,
      createdAt: DateTime.parse(json['created_at'] as String),
    );
  }
}

class DashboardData {
  final int totalInvoices;
  final double totalAmount;
  final Map<String, int> pipeline;
  final List<VendorSpend> topVendors;
  final AgingReport aging;
  final List<MonthlyTrend> trends;
  final UpcomingPayments upcoming;

  DashboardData({
    required this.totalInvoices,
    required this.totalAmount,
    required this.pipeline,
    required this.topVendors,
    required this.aging,
    required this.trends,
    required this.upcoming,
  });

  factory DashboardData.fromJson(Map<String, dynamic> json) {
    // upcoming_payments is a list of invoices from the API
    final upcomingRaw = json['upcoming_payments'];
    final upcomingList = upcomingRaw is List ? upcomingRaw : [];
    final upcomingTotal = upcomingList.fold<double>(
      0,
      (sum, item) => sum + ((item['amount'] as num?)?.toDouble() ?? 0),
    );

    return DashboardData(
      totalInvoices: json['total_invoices'] as int? ?? 0,
      totalAmount: (json['total_amount'] as num?)?.toDouble() ?? 0,
      pipeline: (json['pipeline'] as Map<String, dynamic>?)
              ?.map((k, v) => MapEntry(k, v as int)) ??
          {},
      topVendors: (json['vendor_spend'] as List<dynamic>?)
              ?.map((v) => VendorSpend.fromJson(v as Map<String, dynamic>))
              .toList() ??
          [],
      aging: AgingReport.fromJson(
        json['aging'] as Map<String, dynamic>? ?? {},
      ),
      trends: (json['monthly_trend'] as List<dynamic>?)
              ?.map((t) => MonthlyTrend.fromJson(t as Map<String, dynamic>))
              .toList() ??
          [],
      upcoming: UpcomingPayments(
        count: upcomingList.length,
        totalAmount: upcomingTotal,
      ),
    );
  }
}

class VendorSpend {
  final String vendorName;
  final double totalAmount;
  final int invoiceCount;

  VendorSpend({
    required this.vendorName,
    required this.totalAmount,
    required this.invoiceCount,
  });

  factory VendorSpend.fromJson(Map<String, dynamic> json) {
    return VendorSpend(
      vendorName: (json['vendor'] ?? json['vendor_name']) as String? ?? 'Unknown',
      totalAmount: ((json['amount'] ?? json['total_amount']) as num?)?.toDouble() ?? 0,
      invoiceCount: json['invoice_count'] as int? ?? 0,
    );
  }
}

class AgingReport {
  final double current;
  final double thirtyDays;
  final double sixtyDays;
  final double ninetyPlus;

  AgingReport({
    required this.current,
    required this.thirtyDays,
    required this.sixtyDays,
    required this.ninetyPlus,
  });

  factory AgingReport.fromJson(Map<String, dynamic> json) {
    return AgingReport(
      current: (json['current'] as num?)?.toDouble() ?? 0,
      thirtyDays: ((json['days_30'] ?? json['30_days']) as num?)?.toDouble() ?? 0,
      sixtyDays: ((json['days_60'] ?? json['60_days']) as num?)?.toDouble() ?? 0,
      ninetyPlus: ((json['days_90_plus'] ?? json['90_plus']) as num?)?.toDouble() ?? 0,
    );
  }
}

class MonthlyTrend {
  final String month;
  final int count;
  final double amount;

  MonthlyTrend({
    required this.month,
    required this.count,
    required this.amount,
  });

  factory MonthlyTrend.fromJson(Map<String, dynamic> json) {
    return MonthlyTrend(
      month: json['month'] as String? ?? '',
      count: json['count'] as int? ?? 0,
      amount: (json['amount'] as num?)?.toDouble() ?? 0,
    );
  }
}

class UpcomingPayments {
  final int count;
  final double totalAmount;

  UpcomingPayments({required this.count, required this.totalAmount});

  factory UpcomingPayments.fromJson(Map<String, dynamic> json) {
    return UpcomingPayments(
      count: json['count'] as int? ?? 0,
      totalAmount: (json['total_amount'] as num?)?.toDouble() ?? 0,
    );
  }
}
