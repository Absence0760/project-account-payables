import 'package:flutter_test/flutter_test.dart';

import 'package:ap_mobile/models/vendor.dart';

void main() {
  group('VendorStatus enum', () {
    test('fromString round-trips known values', () {
      for (final s in VendorStatus.values) {
        expect(VendorStatus.fromString(s.value), s);
      }
    });

    test('fromString falls back to unverified for unknown values', () {
      expect(VendorStatus.fromString('???'), VendorStatus.unverified);
    });

    test('isUnverified is true only for unverified', () {
      expect(VendorStatus.unverified.isUnverified, isTrue);
      expect(VendorStatus.active.isUnverified, isFalse);
      expect(VendorStatus.rejected.isUnverified, isFalse);
    });

    test('labels are human-readable', () {
      expect(VendorStatus.active.label, 'Active');
      expect(VendorStatus.unverified.label, 'Unverified');
    });
  });

  group('Vendor.fromJson', () {
    test('parses a full vendor row', () {
      final v = Vendor.fromJson({
        'id': 'v1',
        'name': 'Acme Supplies',
        'code': 'ACME',
        'email': 'ap@acme.com',
        'phone': '555-1234',
        'status': 'active',
        'source': 'erp_sync',
        'payment_terms': 'Net 30',
        'verified_by': 'Jane',
        'erp_vendor_id': 'ERP-1',
        'invoice_count': 7,
      });
      expect(v.id, 'v1');
      expect(v.name, 'Acme Supplies');
      expect(v.status, VendorStatus.active);
      expect(v.source, 'erp_sync');
      expect(v.sourceLabel, 'ERP');
      expect(v.invoiceCount, 7);
    });

    test('tolerates missing optional fields', () {
      final v = Vendor.fromJson({
        'id': 'v2',
        'name': 'Bare Vendor',
        'status': 'unverified',
        'source': 'manual',
        'created_at': '2026-01-01T00:00:00',
      });
      expect(v.code, isNull);
      expect(v.email, isNull);
      expect(v.invoiceCount, 0);
      expect(v.status, VendorStatus.unverified);
      expect(v.sourceLabel, 'Manual');
    });
  });
}
