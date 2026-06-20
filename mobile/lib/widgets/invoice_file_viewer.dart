import 'package:flutter/material.dart';
import 'package:path/path.dart' as p;
import 'package:pdfx/pdfx.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/config.dart';

/// Renders an uploaded invoice file (image or PDF) full-screen.
///
/// [fileUrl] is the API-relative path the backend returns on the invoice
/// (`/api/invoices/file/{key}`); the absolute URL and the auth + tenant headers
/// are resolved here, so callers only pass the relative path. Images render via
/// [Image.network]; PDFs are fetched as bytes (the JWT + `X-Tenant-Slug`
/// headers can't be attached to a plain URL the native PDF engine would open)
/// and rendered with the `pdfx` viewer.
///
/// The viewer detects the file type from the URL extension and falls back to an
/// inline image attempt for anything that isn't a PDF — the backend only stores
/// PDF / PNG / JPG / TIFF, all of which `Image.network` can decode except PDF.
class InvoiceFileViewer extends StatefulWidget {
  final String fileUrl;

  const InvoiceFileViewer({super.key, required this.fileUrl});

  /// Whether [fileUrl] points at a PDF (by extension).
  static bool isPdf(String fileUrl) =>
      p.extension(fileUrl.split('?').first).toLowerCase() == '.pdf';

  /// Absolute URL for [fileUrl] against the configured API host.
  static String absoluteUrl(String fileUrl) =>
      '${AppConfig.apiBaseUrl}$fileUrl';

  @override
  State<InvoiceFileViewer> createState() => _InvoiceFileViewerState();
}

class _InvoiceFileViewerState extends State<InvoiceFileViewer> {
  PdfController? _pdfController;
  bool _loading = true;
  String? _error;

  bool get _isPdf => InvoiceFileViewer.isPdf(widget.fileUrl);

  @override
  void initState() {
    super.initState();
    if (_isPdf) {
      _loadPdf();
    } else {
      // Images are streamed by Image.network — nothing to pre-fetch.
      _loading = false;
    }
  }

  @override
  void dispose() {
    _pdfController?.dispose();
    super.dispose();
  }

  Future<void> _loadPdf() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      // Fetch via the shared client so the auth + tenant headers are attached
      // (the native PDF engine can't open a URL with custom headers) and so the
      // fetch is swappable in tests.
      final bytes = await ApiClient().getBytes(widget.fileUrl);
      if (!mounted) return;
      final controller = PdfController(
        document: PdfDocument.openData(bytes),
      );
      setState(() {
        _pdfController = controller;
        _loading = false;
      });
    } catch (e) {
      debugPrint('[viewer] PDF load failed: $e');
      if (!mounted) return;
      setState(() {
        _error = 'Unable to load PDF';
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(_isPdf ? 'Invoice PDF' : 'Invoice Image'),
      ),
      backgroundColor: Colors.black,
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Semantics(
              liveRegion: true,
              child: Text(
                _error!,
                style: const TextStyle(color: Colors.white),
              ),
            ),
            const SizedBox(height: 16),
            if (_isPdf)
              FilledButton.icon(
                onPressed: _loadPdf,
                icon: const Icon(Icons.refresh),
                label: const Text('Retry'),
              ),
          ],
        ),
      );
    }
    if (_isPdf) {
      return PdfView(controller: _pdfController!);
    }
    // Image path — stream with auth headers, allow pinch-zoom.
    return InteractiveViewer(
      child: Center(
        child: Image.network(
          InvoiceFileViewer.absoluteUrl(widget.fileUrl),
          headers: ApiClient().authHeaders,
          fit: BoxFit.contain,
          loadingBuilder: (context, child, progress) {
            if (progress == null) return child;
            return const Center(child: CircularProgressIndicator());
          },
          errorBuilder: (_, _, _) => const Center(
            child: Text(
              'Unable to load image',
              style: TextStyle(color: Colors.white),
            ),
          ),
        ),
      ),
    );
  }
}
