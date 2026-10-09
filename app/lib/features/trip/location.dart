import 'dart:async';
import 'dart:math' as math;

import 'package:clock/clock.dart';
import 'package:geolocator/geolocator.dart';

import '../../core/api/api_client.dart';
import '../../core/models/trip.dart';

/// 기기 위치 한 번 (GPS·네트워크 위치).
class LocationFix {
  LocationFix(this.lat, this.lng, {this.accuracy});
  final double lat;
  final double lng;
  final double? accuracy;
}

enum LocationAccess { granted, denied, deniedForever, serviceOff }

/// 위치를 주는 쪽. 실제 앱은 [GeolocatorSource] (Android·iOS·Windows), 테스트는 가짜.
abstract class LocationSource {
  Future<LocationAccess> check();
  Future<LocationAccess> request();
  Stream<LocationFix> positions();

  /// 영구 거부 · 위치 서비스 꺼짐이면 OS 설정을 연다.
  Future<void> openSettings(LocationAccess access);
}

/// geolocator 로 When-in-Use 위치. 백그라운드 위치는 쓰지 않는다 (trip §2.2).
class GeolocatorSource implements LocationSource {
  static LocationAccess _map(LocationPermission p) => switch (p) {
        LocationPermission.always || LocationPermission.whileInUse => LocationAccess.granted,
        LocationPermission.deniedForever => LocationAccess.deniedForever,
        _ => LocationAccess.denied,
      };

  @override
  Future<LocationAccess> check() async {
    try {
      if (!await Geolocator.isLocationServiceEnabled()) return LocationAccess.serviceOff;
      return _map(await Geolocator.checkPermission());
    } catch (_) {
      return LocationAccess.denied; // 플러그인이 없는 환경 (테스트·웹)
    }
  }

  @override
  Future<LocationAccess> request() async {
    try {
      if (!await Geolocator.isLocationServiceEnabled()) return LocationAccess.serviceOff;
      return _map(await Geolocator.requestPermission());
    } catch (_) {
      return LocationAccess.denied;
    }
  }

  @override
  Stream<LocationFix> positions() => Geolocator.getPositionStream(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high, distanceFilter: 10),
      ).map((p) => LocationFix(p.latitude, p.longitude, accuracy: p.accuracy));

  @override
  Future<void> openSettings(LocationAccess access) async {
    try {
      access == LocationAccess.serviceOff ? await Geolocator.openLocationSettings() : await Geolocator.openAppSettings();
    } catch (_) {}
  }
}

/// 두 점 사이 거리 (m, 하버사인).
double distanceM(double lat1, double lng1, double lat2, double lng2) {
  const r = 6371000.0;
  double rad(double d) => d * math.pi / 180;
  final dLat = rad(lat2 - lat1), dLng = rad(lng2 - lng1);
  final a = math.pow(math.sin(dLat / 2), 2) + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.pow(math.sin(dLng / 2), 2);
  return 2 * r * math.asin(math.sqrt(a));
}

/// 위치 전송기 (13 §위치 전송, trip §2.2).
/// - 30m 이상 움직였거나, 마지막 전송 뒤 [interval](45초, 30~60초 사이) 이 지나면 지금 위치를 보낸다.
/// - 보내지 못한 점(네트워크·5xx)은 모아 두었다가 연결되면 `{"points": […]}` 로 (최대 100, 오래된 순).
/// - 409 trip_not_active 면 멈추고 [onInactive] (자동 종료됐을 수 있음).
/// - recorded_at 은 서버 시계(가상 시각이면 그 시각) 기준: [serverNow].
class LocationSender {
  LocationSender({
    required this.send,
    required this.serverNow,
    this.onResult,
    this.onOffline,
    this.onInactive,
    this.interval = const Duration(seconds: 45),
    this.minDistanceM = 30,
  });

  final Future<LocationResult> Function(List<LocationPoint> points) send;
  final DateTime Function() serverNow;
  final void Function(LocationResult result)? onResult;
  final void Function(bool offline)? onOffline;
  final void Function()? onInactive;
  final Duration interval;
  final double minDistanceM;

  static const maxBatch = 100;

  final pending = <LocationPoint>[];
  LocationFix? latest;
  LocationFix? _lastSentFix;
  DateTime? _lastSentAt; // 기기 시계 (clock)
  StreamSubscription<LocationFix>? _sub;
  Timer? _timer;
  bool _sending = false;
  bool running = false;
  int sentRequests = 0;

  void start(Stream<LocationFix> fixes) {
    stop();
    running = true;
    _sub = fixes.listen(onFix, onError: (_) {});
    _timer = Timer.periodic(const Duration(seconds: 15), (_) => _tick());
  }

  void stop() {
    running = false;
    _sub?.cancel();
    _sub = null;
    _timer?.cancel();
    _timer = null;
  }

  void onFix(LocationFix f) {
    latest = f;
    final last = _lastSentFix;
    final moved = last == null || distanceM(last.lat, last.lng, f.lat, f.lng) >= minDistanceM;
    if (moved || _due()) _enqueue(f);
  }

  bool _due() => _lastSentAt == null || clock.now().difference(_lastSentAt!) >= interval;

  void _tick() {
    final f = latest;
    if (f != null && _due()) {
      _enqueue(f);
    } else if (pending.isNotEmpty) {
      flush();
    }
  }

  void _enqueue(LocationFix f) {
    pending.add(LocationPoint(lat: f.lat, lng: f.lng, accuracy: f.accuracy, recordedAt: serverNow()));
    _lastSentFix = f;
    _lastSentAt = clock.now();
    flush();
  }

  /// 모아 둔 점을 오래된 순으로 100개씩.
  Future<void> flush() async {
    if (_sending || pending.isEmpty) return;
    _sending = true;
    try {
      while (pending.isNotEmpty) {
        final batch = pending.take(maxBatch).toList();
        sentRequests++;
        final res = await send(batch);
        pending.removeRange(0, batch.length);
        onOffline?.call(false);
        onResult?.call(res);
      }
    } on ApiException catch (e) {
      if (e.code == 'trip_not_active') {
        pending.clear();
        stop();
        onInactive?.call();
      } else if (e.isNetwork || e.status >= 500) {
        onOffline?.call(e.isNetwork);
      } else {
        pending.clear(); // 422 등: 다시 보내도 같다
      }
    } finally {
      _sending = false;
    }
  }
}
