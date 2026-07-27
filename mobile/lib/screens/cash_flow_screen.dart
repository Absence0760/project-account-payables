import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';
import 'package:intl/intl.dart';

import 'package:feohledger_mobile/l10n/gen/app_localizations.dart';
import 'package:feohledger_mobile/models/cash_flow.dart';
import 'package:feohledger_mobile/stores/cash_flow_store.dart';
import 'package:feohledger_mobile/widgets/kpi_card.dart';

final _currencyFormat = NumberFormat.currency(symbol: '\$');

/// Format a server-supplied money display string for the UI. The parse is for
/// *rendering only* — we never do arithmetic on money on the device (every
/// total is server-computed). Falls back to the raw string if it isn't numeric.
String _money(String display) {
  final n = num.tryParse(display);
  return n == null ? display : _currencyFormat.format(n);
}

/// Predictive cash-flow forecast (CFO / admin). Shows a KPI summary (opening +
/// projected end balance, total committed / pending outflow over the horizon),
/// a low-balance alert when the cash-position threshold is breached, a
/// per-period forecast list (inflow context: committed vs pending outflow), and
/// the running cash-position balance per period. Pull-to-refresh; 30/60/90-day
/// horizon chips. Reached from the Dashboard app-bar (gated to CFO / admin,
/// matching the backend `_CFO_ROLES` gate on the analytics endpoints).
class CashFlowScreen extends StatefulWidget {
  const CashFlowScreen({super.key});

  @override
  State<CashFlowScreen> createState() => _CashFlowScreenState();
}

class _CashFlowScreenState extends State<CashFlowScreen> {
  @override
  void initState() {
    super.initState();
    SchedulerBinding.instance.addPostFrameCallback((_) {
      CashFlowStore.instance.fetch();
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(l.cashFlowTitle)),
      body: ListenableBuilder(
        listenable: CashFlowStore.instance,
        builder: (context, _) {
          final store = CashFlowStore.instance;

          if (store.loading && store.data == null) {
            return const Center(child: CircularProgressIndicator());
          }

          if (store.error != null && store.data == null) {
            return Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(l.cashFlowErrorPrefix(store.error.toString())),
                  const SizedBox(height: 16),
                  FilledButton(
                    onPressed: store.fetch,
                    child: Text(l.commonRetry),
                  ),
                ],
              ),
            );
          }

          final data = store.data;
          if (data == null) return const SizedBox.shrink();

          return RefreshIndicator(
            onRefresh: store.fetch,
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _horizonChips(l, store),
                const SizedBox(height: 16),
                if (data.hasBreach) ...[
                  _lowBalanceAlert(l, data),
                  const SizedBox(height: 16),
                ],
                _kpiSummary(l, data),
                const SizedBox(height: 24),
                _forecastSection(context, l, data),
                const SizedBox(height: 24),
                _positionSection(context, l, data),
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _horizonChips(AppLocalizations l, CashFlowStore store) {
    return Row(
      children: [
        for (final days in CashFlowStore.horizonOptions) ...[
          ChoiceChip(
            label: Text(l.cashFlowHorizonDays(days)),
            selected: store.horizonDays == days,
            onSelected: (_) => store.setHorizon(days),
          ),
          const SizedBox(width: 8),
        ],
      ],
    );
  }

  Widget _lowBalanceAlert(AppLocalizations l, CashFlowData data) {
    final count = data.breaches.length;
    final worst = data.breaches.reduce((a, b) {
      final av = num.tryParse(a.shortfallDisplay) ?? 0;
      final bv = num.tryParse(b.shortfallDisplay) ?? 0;
      return av >= bv ? a : b;
    });
    final message = count == 1
        ? l.cashFlowBreachSingle(
            data.thresholdDisplay != null
                ? _money(data.thresholdDisplay!)
                : l.cashFlowMinimum,
            worst.period,
            _money(worst.shortfallDisplay),
          )
        : l.cashFlowBreachMultiple(
            count,
            worst.period,
            _money(worst.shortfallDisplay),
          );
    return Semantics(
      label: l.cashFlowLowBalanceAlertLabel(message),
      excludeSemantics: true,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
        decoration: BoxDecoration(
          color: Colors.red.shade50,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: Colors.red.shade200),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(Icons.warning_amber_rounded,
                size: 20, color: Colors.red.shade800),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    l.cashFlowLowBalanceAlert,
                    style: TextStyle(
                      fontWeight: FontWeight.w700,
                      // shade900 clears AA contrast on the 0.x-alpha tint.
                      color: Colors.red.shade900,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    message,
                    style: TextStyle(color: Colors.red.shade900, fontSize: 13),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _kpiSummary(AppLocalizations l, CashFlowData data) {
    final endColor = data.hasBreach ? Colors.red : Colors.green;
    return Column(
      children: [
        Row(
          children: [
            Expanded(
              child: KpiCard(
                title: l.cashFlowOpeningBalance,
                value: _money(data.openingBalanceDisplay),
                subtitle: _openingSourceLabel(l, data.openingBalanceSource),
                icon: Icons.account_balance,
                color: Colors.blue,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: KpiCard(
                title: l.cashFlowProjectedEnd,
                value: _money(data.projectedEndBalanceDisplay),
                subtitle: l.cashFlowProjectedEndSubtitle(data.horizonDays),
                icon: Icons.trending_up,
                color: endColor,
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: KpiCard(
                title: l.cashFlowCommittedOut,
                value: _money(data.totals.committedAmountDisplay),
                subtitle: l.cashFlowCommittedSubtitle,
                icon: Icons.lock_clock,
                color: Colors.deepOrange,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: KpiCard(
                title: l.cashFlowPendingOut,
                value: _money(data.totals.pendingAmountDisplay),
                subtitle: l.cashFlowPendingSubtitle,
                icon: Icons.pending_actions,
                color: Colors.amber.shade700,
              ),
            ),
          ],
        ),
      ],
    );
  }

  String _openingSourceLabel(AppLocalizations l, String source) =>
      switch (source) {
        'provider' => l.cashFlowOpeningSourceProvider,
        'settings' => l.cashFlowOpeningSourceSettings,
        'query' => l.cashFlowOpeningSourceQuery,
        _ => l.cashFlowOpeningSourceUnset,
      };

  Widget _forecastSection(
      BuildContext context, AppLocalizations l, CashFlowData data) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          l.cashFlowProjectedOutflows,
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 8),
        if (data.forecastPeriods.isEmpty)
          _emptyCard(l.cashFlowNoOutflows)
        else
          ...data.forecastPeriods.map((p) => _forecastRow(l, p)),
      ],
    );
  }

  Widget _forecastRow(AppLocalizations l, CashFlowForecastPeriod p) {
    // One announcement per row instead of period + four money fragments.
    return Semantics(
      label: l.cashFlowForecastRowLabel(
        p.period,
        _money(p.scheduledAmountDisplay),
        _money(p.committedAmountDisplay),
        _money(p.pendingAmountDisplay),
        p.count,
      ),
      excludeSemantics: true,
      child: Card(
        elevation: 0,
        margin: const EdgeInsets.only(bottom: 8),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(color: Colors.grey.shade200),
        ),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      p.period,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      l.cashFlowInvoiceCount(p.count),
                      style:
                          TextStyle(color: Colors.grey.shade700, fontSize: 12),
                    ),
                  ],
                ),
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    _money(p.scheduledAmountDisplay),
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    l.cashFlowCommittedAmount(_money(p.committedAmountDisplay)),
                    style:
                        TextStyle(color: Colors.grey.shade700, fontSize: 11),
                  ),
                  Text(
                    l.cashFlowPendingAmount(_money(p.pendingAmountDisplay)),
                    style:
                        TextStyle(color: Colors.grey.shade700, fontSize: 11),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _positionSection(
      BuildContext context, AppLocalizations l, CashFlowData data) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          l.cashFlowPosition,
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 8),
        if (data.positionPeriods.isEmpty)
          _emptyCard(l.cashFlowNoPosition)
        else
          ...data.positionPeriods.map((p) => _positionRow(l, p)),
      ],
    );
  }

  Widget _positionRow(AppLocalizations l, CashPositionPeriod p) {
    final breach = p.belowThreshold;
    // shade900 keeps the red closing balance legible at AA on white.
    final closingColor = breach ? Colors.red.shade900 : Colors.black87;
    return Semantics(
      label: l.cashFlowPositionRowLabel(
            p.period,
            _money(p.openingDisplay),
            _money(p.outflowDisplay),
            _money(p.closingDisplay),
          ) +
          (breach ? l.cashFlowBelowThresholdSuffix : ''),
      excludeSemantics: true,
      child: Card(
        elevation: 0,
        margin: const EdgeInsets.only(bottom: 8),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(
            color: breach ? Colors.red.shade200 : Colors.grey.shade200,
          ),
        ),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            children: [
              if (breach) ...[
                Icon(Icons.warning_amber_rounded,
                    size: 18, color: Colors.red.shade800),
                const SizedBox(width: 8),
              ],
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      p.period,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      l.cashFlowOutAmount(_money(p.outflowDisplay)),
                      style:
                          TextStyle(color: Colors.grey.shade700, fontSize: 12),
                    ),
                  ],
                ),
              ),
              Text(
                _money(p.closingDisplay),
                style: TextStyle(
                  fontWeight: FontWeight.w700,
                  color: closingColor,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _emptyCard(String message) {
    return Card(
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: Colors.grey.shade200),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Text(
          message,
          // shade700 keeps the empty-state copy at AA contrast.
          style: TextStyle(color: Colors.grey.shade700),
        ),
      ),
    );
  }
}
