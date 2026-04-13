import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

/// Push notification service — Firebase Cloud Messaging.
///
/// Requires Firebase project setup:
/// - iOS: GoogleService-Info.plist in ios/Runner/
/// - Android: google-services.json in android/app/
///
/// Until Firebase is configured, all methods are no-ops that log warnings.
class PushService {
  static final PushService instance = PushService._();
  PushService._();

  bool _initialized = false;
  String? _fcmToken;

  String? get fcmToken => _fcmToken;

  final _localNotifications = FlutterLocalNotificationsPlugin();

  /// Initialize FCM and request permissions.
  /// Call once from main.dart after Firebase.initializeApp().
  Future<void> init() async {
    if (_initialized) return;

    try {
      // Request permission (iOS prompts user, Android auto-grants)
      final settings = await FirebaseMessaging.instance.requestPermission(
        alert: true,
        badge: true,
        sound: true,
      );

      if (settings.authorizationStatus == AuthorizationStatus.denied) {
        debugPrint('[push] Notification permission denied');
        return;
      }

      // Get FCM token
      _fcmToken = await FirebaseMessaging.instance.getToken();
      debugPrint('[push] FCM token: $_fcmToken');

      // Listen for token refresh
      FirebaseMessaging.instance.onTokenRefresh.listen((token) {
        _fcmToken = token;
        debugPrint('[push] FCM token refreshed');
        // TODO: send token to backend for targeted push
      });

      // Initialize local notifications for foreground display
      await _localNotifications.initialize(
        const InitializationSettings(
          android: AndroidInitializationSettings('@mipmap/ic_launcher'),
          iOS: DarwinInitializationSettings(),
        ),
      );

      // Handle foreground messages
      FirebaseMessaging.onMessage.listen(_handleForegroundMessage);

      // Handle background message taps (app was in background)
      FirebaseMessaging.onMessageOpenedApp.listen(_handleMessageTap);

      _initialized = true;
      debugPrint('[push] Push notifications initialized');
    } catch (e) {
      debugPrint('[push] Init failed (Firebase not configured?): $e');
    }
  }

  void _handleForegroundMessage(RemoteMessage message) {
    debugPrint('[push] Foreground message: ${message.notification?.title}');

    final notification = message.notification;
    if (notification == null) return;

    // Show local notification since FCM doesn't auto-show in foreground
    _localNotifications.show(
      notification.hashCode,
      notification.title,
      notification.body,
      const NotificationDetails(
        android: AndroidNotificationDetails(
          'ap_approvals',
          'Approval Notifications',
          channelDescription: 'Invoice approval requests and status updates',
          importance: Importance.high,
          priority: Priority.high,
        ),
        iOS: DarwinNotificationDetails(),
      ),
    );
  }

  void _handleMessageTap(RemoteMessage message) {
    debugPrint('[push] Message tapped: ${message.data}');
    // TODO: navigate to specific invoice based on message.data['invoice_id']
  }
}
