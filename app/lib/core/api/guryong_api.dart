import '../models/account.dart';
import '../models/agent.dart';
import '../models/place.dart';
import '../models/plan.dart';
import '../models/safety.dart';
import 'api_client.dart';

/// 백엔드 API 를 화면에서 쓰기 좋은 형태로 묶은 것.
/// 경로·필드는 docs/places, planner, agent, account, safety, routing, docent, trip README 와 같다.
class GuryongApi {
  GuryongApi(this.client);
  final ApiClient client;

  Map<String, dynamic> _m(dynamic v) => v as Map<String, dynamic>;
  List<Map<String, dynamic>> _l(dynamic v) => (v as List).cast<Map<String, dynamic>>();

  // ---------- account ----------

  Future<TokenResponse> login(String loginId, String password) async =>
      TokenResponse.fromJson(_m(await client.post('/auth/login', {'login_id': loginId.trim().toLowerCase(), 'password': password})));

  Future<TokenResponse> signup(String loginId, String password, {String? displayName}) async => TokenResponse.fromJson(_m(await client.post('/auth/signup', {
        'login_id': loginId.trim().toLowerCase(),
        'password': password,
        if (displayName != null && displayName.trim().isNotEmpty) 'display_name': displayName.trim(),
      })));

  Future<TokenResponse> googleLogin(String idToken) async => TokenResponse.fromJson(_m(await client.post('/auth/google', {'id_token': idToken})));

  Future<TokenResponse> refresh(String refreshToken) async =>
      TokenResponse.fromJson(_m(await client.post('/auth/refresh', {'refresh_token': refreshToken})));

  Future<void> logout(String refreshToken) => client.post('/auth/logout', {'refresh_token': refreshToken});

  Future<UserView> me() async => UserView.fromJson(_m(await client.get('/users/me')));

  Future<void> deleteMe() => client.delete('/users/me');

  Future<dynamic> health() => client.get('/health');

  // ---------- places ----------

  Future<List<PlaceSummary>> searchPlaces({
    String? q,
    String mode = 'hybrid',
    List<String>? tags,
    String? contentType,
    String? area,
    double? lat,
    double? lng,
    double? radiusM,
    int limit = 30,
  }) async {
    final res = _m(await client.get('/places', {
      'q': (q == null || q.trim().isEmpty) ? null : q.trim(),
      // 검색어가 없으면 의미 검색을 할 수 없으므로 keyword(필터만) 로 보낸다
      'mode': (q == null || q.trim().isEmpty) ? 'keyword' : mode,
      'tags': tags,
      'content_type': contentType,
      'area': area,
      'lat': lat,
      'lng': lng,
      'radius_m': radiusM,
      'limit': limit,
    }));
    return _l(res['items']).map(PlaceSummary.fromJson).toList();
  }

  Future<PlaceDetail> place(String id) async => PlaceDetail.fromJson(_m(await client.get('/places/$id')));

  Future<List<Story>> placeStories(String id) async => _l(await client.get('/places/$id/stories')).map(Story.fromJson).toList();

  Future<List<TagCount>> tags() async => _l(await client.get('/places/tags')).map(TagCount.fromJson).toList();

  // ---------- planner ----------

  Future<List<PlanSummary>> plans() async => _l(await client.get('/planner/plans')).map(PlanSummary.fromJson).toList();

  Future<PlanDetail> plan(int id) async => PlanDetail.fromJson(_m(await client.get('/planner/plans/$id')));

  Future<PlanDetail> createPlan(Map<String, dynamic> body) async => PlanDetail.fromJson(_m(await client.post('/planner/plans', body)));

  Future<PlanDetail> updatePlan(int id, Map<String, dynamic> body) async =>
      PlanDetail.fromJson(_m(await client.patch('/planner/plans/$id', body)));

  Future<void> deletePlan(int id) => client.delete('/planner/plans/$id');

  Future<PlanDetail> addItem(int planId, Map<String, dynamic> body) async =>
      PlanDetail.fromJson(_m(await client.post('/planner/plans/$planId/items', body)));

  /// 보낸 필드만 바뀐다. start_time 에 null 을 보내면 시각 고정을 푼다. travel_mode 로 구간 수단을 바꾼다.
  Future<PlanDetail> updateItem(int planId, int itemId, Map<String, dynamic> body) async =>
      PlanDetail.fromJson(_m(await client.patch('/planner/plans/$planId/items/$itemId', body)));

  Future<PlanDetail> removeItem(int planId, int itemId) async =>
      PlanDetail.fromJson(_m(await client.delete('/planner/plans/$planId/items/$itemId')));

  Future<PlanDetail> addConstraint(int planId, Map<String, dynamic> body) async =>
      PlanDetail.fromJson(_m(await client.post('/planner/plans/$planId/constraints', body)));

  Future<PlanDetail> removeConstraint(int planId, int constraintId) async =>
      PlanDetail.fromJson(_m(await client.delete('/planner/plans/$planId/constraints/$constraintId')));

  /// 여러 변경을 원자적으로 즉시 적용 (실행 취소·복제에 쓴다).
  Future<PlanDetail> applyOps(int planId, List<Map<String, dynamic>> ops) async =>
      PlanDetail.fromJson(_m(await client.post('/planner/plans/$planId/ops', {'ops': ops})));

  /// 목록은 ProposalSummary (ops·before·after 없음).
  Future<List<ProposalDetail>> proposals(int planId, {String? status}) async =>
      _l(await client.get('/planner/plans/$planId/proposals', {'status': status})).map(ProposalDetail.fromJson).toList();

  Future<ProposalDetail> proposal(int planId, int proposalId) async =>
      ProposalDetail.fromJson(_m(await client.get('/planner/plans/$planId/proposals/$proposalId')));

  Future<SafetyCheck> safety(int planId) async => SafetyCheck.fromJson(_m(await client.get('/planner/plans/$planId/safety')));

  /// ops 를 적용했다고 치고 점검 (21 수정안 위험). 새 항목 item_id 는 음수.
  Future<SafetyCheck> safetyPreview(int planId, List<Map<String, dynamic>> ops) async =>
      SafetyCheck.fromJson(_m(await client.post('/planner/plans/$planId/safety/preview', {'ops': ops})));

  Future<List<SafetyAlert>> safetyAlerts(int planId, {String status = 'new'}) async =>
      _l(await client.get('/planner/plans/$planId/safety/alerts', {'status': status})).map(SafetyAlert.fromJson).toList();

  Future<void> safetyAlertSeen(int planId, int alertId) => client.post('/planner/plans/$planId/safety/alerts/$alertId/seen');

  /// 개발·시연용 (AUTH_REQUIRED=false 에서만). 운영이면 404 not_available.
  Future<dynamic> safetyScan({bool force = true}) => client.post('/planner/safety/scan', null, {'force': force});

  // ---------- agent ----------

  Future<List<AgentSession>> sessions(int planId) async =>
      _l(await client.get('/agent/sessions', {'plan_id': planId})).map(AgentSession.fromJson).toList();

  Future<AgentSession> createSession(int planId) async =>
      AgentSession.fromJson(_m(await client.post('/agent/sessions', {'plan_id': planId})));

  Future<AgentSessionDetail> session(String id) async => AgentSessionDetail.fromJson(_m(await client.get('/agent/sessions/$id')));

  Future<Map<String, dynamic>> agentInfo() async => _m(await client.get('/agent/info'));

  /// SSE: start · status · step · thinking · text · proposal · recommendation · steps_done · done · error (agent §3.1)
  /// [placeId]: 현장 가이드(17)에서 묻는 말 — 그 장소 맥락으로 답하고 메시지에 place_id 를 남긴다.
  Stream<SseEvent> chat(String sessionId, String message, {String? placeId}) =>
      client.postSse('/agent/sessions/$sessionId/chat', {'message': message, 'place_id': ?placeId});

  /// 승인 → {proposal, plan}. 409 base_changed · progress_changed · proposal_not_pending · agent_busy 는 ApiException.code 로.
  Future<(ProposalDetail, PlanDetail)> acceptProposal(String sessionId, int proposalId, {bool overwrite = false}) async {
    final res = _m(await client.post('/agent/sessions/$sessionId/proposals/$proposalId/accept', {'mode': overwrite ? 'overwrite' : 'auto'}));
    return (ProposalDetail.fromJson(_m(res['proposal'])), PlanDetail.fromJson(_m(res['plan'])));
  }

  Future<ProposalDetail> rejectProposal(String sessionId, int proposalId) async =>
      ProposalDetail.fromJson(_m(await client.post('/agent/sessions/$sessionId/proposals/$proposalId/reject')));

  /// 현재 일정에 맞춰 다시 제안 (SSE, 대화에 "이전 제안을 현재 일정에 맞춰 다시 만들어줘." 가 남는다).
  Stream<SseEvent> repropose(String sessionId, int proposalId) => client.postSse('/agent/sessions/$sessionId/proposals/$proposalId/repropose');

  Future<(Recommendation, PlanDetail)> candidate(String sessionId, int recId, int candidateId, {required bool add}) async {
    final res = _m(await client.post('/agent/sessions/$sessionId/recommendations/$recId/candidates/$candidateId/${add ? 'add' : 'remove'}'));
    return (Recommendation.fromJson(_m(res['recommendation'])), PlanDetail.fromJson(_m(res['plan'])));
  }

  Future<GuideJob> guidePrepare(int planId, {List<String>? placeIds, bool force = false}) async => GuideJob.fromJson(_m(await client.post(
        '/agent/plans/$planId/guide/prepare',
        {'place_ids': ?placeIds, if (force) 'force': true},
      )));

  Future<GuideJob> guideJob(int planId, String jobId) async => GuideJob.fromJson(_m(await client.get('/agent/plans/$planId/guide/jobs/$jobId')));

  // ---------- docent ----------

  Future<MemoryList> memory({int? planId}) async => MemoryList.fromJson(_m(await client.get('/docent/memory', {'plan_id': planId})));

  Future<void> deleteMemory(int id) => client.delete('/docent/memory/$id');

  Future<void> addMemory(Map<String, dynamic> body) => client.post('/docent/memory', body);

  Future<void> updateMemory(int id, Map<String, dynamic> body) => client.patch('/docent/memory/$id', body);

  /// 가이드 노트 본문 (short·long·highlights·sources …). 17 미리보기.
  Future<Map<String, dynamic>> guideNote(int planId, String placeId) async => _m(await client.get('/docent/plans/$planId/notes/$placeId'));

  Future<List<GuideNoteSummary>> guideNotes(int planId) async =>
      _l(await client.get('/docent/plans/$planId/notes')).map(GuideNoteSummary.fromJson).toList();

  // ---------- safety · routing · trip ----------

  Future<Outlook> outlook(DateTime date) async => Outlook.fromJson(_m(await client.get('/safety/outlook', {
        'date': '${date.year}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')}',
      })));

  Future<Situation> situation() async => Situation.fromJson(_m(await client.get('/safety/situation')));

  Future<List<AvoidZone>> avoidZones({String minRisk = 'caution'}) async => (await avoid(minRisk: minRisk)).$1;

  /// 지금 피할 구역과 safety 기준 시각(가상 시각이면 그 시각) — 28 범례 "지금 기준 HH:MM".
  Future<(List<AvoidZone>, DateTime?)> avoid({String minRisk = 'caution'}) async {
    final j = _m(await client.get('/safety/avoid', {'min_risk': minRisk}));
    return (_l(j['zones']).map(AvoidZone.fromJson).toList(), j['now'] == null ? null : DateTime.parse(j['now'] as String));
  }

  Future<SimState> sim() async => SimState.fromJson(_m(await client.get('/safety/sim')));

  Future<SimState> setSim(Map<String, dynamic> body) async => SimState.fromJson(_m(await client.put('/safety/sim', body)));

  /// hazards: report(지나는 구역 알림) | avoid(우회). stops = [(lat, lng), …].
  Future<RouteResult> route(String mode, List<(double, double)> stops, {String? hazards, bool steps = false}) async =>
      RouteResult.fromJson(_m(await client.post('/routing/route', {
        'mode': mode,
        'stops': [for (final (lat, lng) in stops) {'lat': lat, 'lng': lng}],
        'hazards': ?hazards,
        if (steps) 'steps': true,
      })));

  Future<TripState> tripState(int planId) async => TripState.fromJson(_m(await client.get('/trip/plans/$planId/state')));

  /// 여행일 시작. 409 not_trip_day {start_date, end_date} 는 ApiException.detail 로.
  Future<TripState> tripStart(int planId, {String? sessionId}) async =>
      TripState.fromJson(_m(await client.post('/trip/plans/$planId/start', {'session_id': ?sessionId})));

  Future<TripState> tripEnd(int planId) async => TripState.fromJson(_m(await client.post('/trip/plans/$planId/end')));

  /// 위치 한 점 또는 모아 둔 점들 (최대 100, 오래된 순). 409 trip_not_active 면 여행일이 끝난 것.
  Future<LocationResult> sendLocation(int planId, List<LocationPoint> points) async => LocationResult.fromJson(_m(await client.post(
        '/trip/plans/$planId/location',
        points.length == 1 ? points.first.toJson() : {'points': [for (final p in points) p.toJson()]},
      )));

  Future<List<TripEvent>> tripEvents(int planId, {int after = 0, int limit = 50}) async =>
      _l(await client.get('/trip/plans/$planId/events', {'after': after, 'limit': limit})).map(TripEvent.fromJson).toList();

  /// 이벤트 전부 (오래된 순). 한 쪽([page])이 가득 차면 마지막 id 뒤를 이어 읽는다 (알림 목록·하루 요약, audit-2026-10-12 F6).
  Future<List<TripEvent>> tripEventsAll(int planId, {int page = 200}) async {
    final out = <TripEvent>[];
    var after = 0;
    for (var i = 0; i < 50; i++) {
      final got = await tripEvents(planId, after: after, limit: page);
      out.addAll(got);
      if (got.length < page) break;
      after = got.map((e) => e.id).reduce((a, b) => a > b ? a : b);
    }
    return out;
  }

  Future<void> eventSeen(int planId, int eventId) => client.post('/trip/plans/$planId/events/$eventId/seen');

  Future<GuideNote> guideNoteDetail(int planId, String placeId) async => GuideNote.fromJson(await guideNote(planId, placeId));
}
