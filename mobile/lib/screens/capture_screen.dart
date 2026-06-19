import 'dart:io';

import 'package:flutter/material.dart';

import 'package:ap_mobile/api/api_client.dart';
import 'package:ap_mobile/services/camera_capture.dart';
import 'package:ap_mobile/stores/invoice_store.dart';
import 'package:ap_mobile/utils/a11y.dart';

/// Camera capture screen — snap a photo or pick from gallery, then upload.
class CaptureScreen extends StatefulWidget {
  const CaptureScreen({super.key});

  @override
  State<CaptureScreen> createState() => _CaptureScreenState();
}

class _CaptureScreenState extends State<CaptureScreen> {
  File? _selectedFile;
  bool _uploading = false;
  String? _error;

  Future<void> _capture({bool fromCamera = true}) async {
    final file = await CameraCapture.pickImage(fromCamera: fromCamera);
    if (file != null) {
      setState(() {
        _selectedFile = file;
        _error = null;
      });
    }
  }

  Future<void> _upload() async {
    if (_selectedFile == null) return;

    setState(() {
      _uploading = true;
      _error = null;
    });

    try {
      final result = await CameraCapture.uploadInvoice(_selectedFile!);
      // Refresh invoice list
      await InvoiceStore.instance.fetch();

      if (mounted) {
        final message =
            result['message'] as String? ?? 'Invoice uploaded successfully';
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(message)),
        );
        // Announce success before navigating away (WCAG 4.1.3).
        A11y.announce(context, message);
        Navigator.of(context).pop();
      }
    } catch (e) {
      final message = e is ApiException
          ? 'Upload failed (${e.statusCode}): ${e.message}'
          : 'Upload failed: $e';
      if (!mounted) return;
      setState(() {
        _uploading = false;
        _error = message;
      });
      A11y.announce(context, message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Capture Invoice')),
      body: Column(
        children: [
          Expanded(
            child: _selectedFile != null
                ? InteractiveViewer(
                    child: Image.file(
                      _selectedFile!,
                      fit: BoxFit.contain,
                    ),
                  )
                : Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const ExcludeSemantics(
                          child: Icon(
                            Icons.camera_alt,
                            size: 80,
                            color: Colors.grey, // decorative placeholder
                          ),
                        ),
                        const SizedBox(height: 16),
                        const Text(
                          'Take a photo or choose from gallery',
                          style: TextStyle(fontSize: 16),
                        ),
                        const SizedBox(height: 32),
                        Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            FilledButton.icon(
                              onPressed: () => _capture(fromCamera: true),
                              icon: const Icon(Icons.camera_alt),
                              label: const Text('Camera'),
                            ),
                            const SizedBox(width: 16),
                            OutlinedButton.icon(
                              onPressed: () => _capture(fromCamera: false),
                              icon: const Icon(Icons.photo_library),
                              label: const Text('Gallery'),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Semantics(
                liveRegion: true,
                child: Text(
                  _error!,
                  style: TextStyle(color: Colors.red.shade700),
                  textAlign: TextAlign.center,
                ),
              ),
            ),
          if (_selectedFile != null)
            SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _uploading ? null : () => _capture(),
                        icon: const Icon(Icons.refresh),
                        label: const Text('Retake'),
                      ),
                    ),
                    const SizedBox(width: 16),
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: _uploading ? null : _upload,
                        icon: _uploading
                            ? const SizedBox(
                                width: 18,
                                height: 18,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                  color: Colors.white,
                                ),
                              )
                            : const Icon(Icons.upload),
                        label: Text(_uploading ? 'Uploading...' : 'Upload'),
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}
