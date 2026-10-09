import '../models/agent.dart';
import '../models/place.dart';
import '../models/plan.dart';
import 'api_client.dart';

/// 백엔드 API 를 화면에서 쓰기 좋은 형태로 묶은 것.
/// 경로·필드는 docs/places, docs/planner, docs/agent README 와 같다.
class GuryongApi {
  GuryongApi(this.client);
  final ApiClient client;

  Map<String, dynamic> _m(dynamic v) => v as Map<String, dynamic>;
  List<Map<String, dynamic>> _l(dynamic v) => (v as List).cast<Map<String, dynamic>>();

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

  /// 보낸 필드만 바뀐다. start_time 에 null 을 보내면 시각 고정을 푼다.
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

  Future<List<ProposalDetail>> proposals(int planId, {String? status}) async =>
      _l(await client.get('/planner/plans/$planId/proposals', {'status': status})).map(ProposalDetail.fromJson).toList();

  Future<ProposalDetail> proposal(int planId, int proposalId) async =>
      ProposalDetail.fromJson(_m(await client.get('/planner/plans/$planId/proposals/$proposalId')));

  // ---------- agent ----------

  Future<List<AgentSession>> sessions(int planId) async =>
      _l(await client.get('/agent/sessions', {'plan_id': planId})).map(AgentSession.fromJson).toList();

  Future<AgentSession> createSession(int planId) async =>
      AgentSession.fromJson(_m(await client.post('/agent/sessions', {'plan_id': planId})));

  Future<AgentSessionDetail> session(String id) async => AgentSessionDetail.fromJson(_m(await client.get('/agent/sessions/$id')));

  /// SSE: start · thinking · text · tool_call · tool_result · proposal · done · error
  Stream<SseEvent> chat(String sessionId, String message) => client.postSse('/agent/sessions/$sessionId/chat', {'message': message});

  /// 승인 → {proposal, plan}. 응답 생성 중이면 409.
  Future<PlanDetail> acceptProposal(String sessionId, int proposalId) async {
    final res = _m(await client.post('/agent/sessions/$sessionId/proposals/$proposalId/accept'));
    return PlanDetail.fromJson(_m(res['plan']));
  }

  Future<void> rejectProposal(String sessionId, int proposalId) =>
      client.post('/agent/sessions/$sessionId/proposals/$proposalId/reject');
}
