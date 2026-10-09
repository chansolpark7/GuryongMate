// agent 서비스 응답 모델 (docs/agent/README.md §2~4, planner §6 RecommendationView).
import 'plan.dart';

class AgentSession {
  AgentSession({required this.id, required this.planId, required this.updatedAt});
  final String id;
  final int planId;
  final DateTime updatedAt;

  factory AgentSession.fromJson(Map<String, dynamic> j) =>
      AgentSession(id: j['id'] as String, planId: j['plan_id'] as int, updatedAt: DateTime.parse(j['updated_at'] as String));
}

/// 진행 단계 (agent §3.2). 같은 id 는 같은 줄이다.
class AgentStep {
  AgentStep({required this.id, required this.kind, required this.status, required this.title, required this.detail});
  final String id;
  final String kind; // search · place · plan · edit · rule · review · recommend · proposal · route · safety · memory · forget · recall · research · guide
  String status; // running | done | error
  String title;
  String detail;

  bool get running => status == 'running';
  bool get failed => status == 'error';

  factory AgentStep.fromJson(Map<String, dynamic> j) => AgentStep(
        id: '${j['id']}',
        kind: j['kind'] as String? ?? '',
        status: j['status'] as String? ?? 'done',
        title: j['title'] as String? ?? '',
        detail: j['detail'] as String? ?? '',
      );

  void update(AgentStep s) {
    status = s.status;
    title = s.title;
    detail = s.detail;
  }
}

/// 장소 추천 후보 (planner §6.1). state 로 버튼을 정한다.
class RecommendationCandidate {
  RecommendationCandidate({
    required this.id,
    required this.placeId,
    required this.place,
    required this.day,
    required this.timeHint,
    required this.stayMinutes,
    required this.reason,
    required this.state,
    required this.itemId,
    required this.startAt,
    required this.endAt,
  });

  final int id;
  final String placeId;
  final PlaceInfo? place;
  final int day;
  final String? timeHint;
  final int stayMinutes;
  final String reason;
  final String state; // in_plan | not_in_plan | day_out_of_range
  final int? itemId;
  final String? startAt;
  final String? endAt;

  String get title => place?.title ?? placeId;
  bool get inPlan => state == 'in_plan';
  bool get notInPlan => state == 'not_in_plan';

  factory RecommendationCandidate.fromJson(Map<String, dynamic> j) {
    final item = j['item'] as Map<String, dynamic>?;
    return RecommendationCandidate(
      id: j['id'] as int,
      placeId: j['place_id'] as String,
      place: j['place'] == null ? null : PlaceInfo.fromJson(j['place'] as Map<String, dynamic>),
      day: j['day'] as int,
      timeHint: j['time_hint'] as String?,
      stayMinutes: j['stay_minutes'] as int? ?? 60,
      reason: j['reason'] as String? ?? '',
      state: j['state'] as String? ?? 'not_in_plan',
      itemId: item?['item_id'] as int?,
      startAt: item?['start_at'] as String?,
      endAt: item?['end_at'] as String?,
    );
  }
}

class Recommendation {
  Recommendation({required this.id, required this.planId, required this.title, required this.candidates});
  final int id;
  final int planId;
  final String title;
  final List<RecommendationCandidate> candidates;

  factory Recommendation.fromJson(Map<String, dynamic> j) => Recommendation(
        id: j['id'] as int,
        planId: j['plan_id'] as int,
        title: j['title'] as String? ?? '',
        candidates: ((j['candidates'] as List?) ?? const []).map((e) => RecommendationCandidate.fromJson(e as Map<String, dynamic>)).toList(),
      );
}

/// 대화 기록의 카드 (조회할 때의 서버 상태).
class SessionCard {
  SessionCard.proposal(ProposalDetail this.proposal) : recommendation = null;
  SessionCard.recommendation(Recommendation this.recommendation) : proposal = null;
  final ProposalDetail? proposal;
  final Recommendation? recommendation;

  static SessionCard? fromJson(Map<String, dynamic> j) => switch (j['type']) {
        'proposal' when j['proposal'] != null => SessionCard.proposal(ProposalDetail.fromJson(j['proposal'] as Map<String, dynamic>)),
        'recommendation' when j['recommendation'] != null =>
          SessionCard.recommendation(Recommendation.fromJson(j['recommendation'] as Map<String, dynamic>)),
        _ => null,
      };
}

class AgentMessage {
  AgentMessage({required this.id, required this.role, required this.content, this.steps = const [], this.stepsSummary, this.cards = const []});
  final int? id;
  final String role; // user | assistant
  final String content;
  final List<AgentStep> steps;
  final String? stepsSummary;
  final List<SessionCard> cards;

  factory AgentMessage.fromJson(Map<String, dynamic> j) => AgentMessage(
        id: j['id'] as int?,
        role: j['role'] as String,
        content: j['content'] as String? ?? '',
        steps: ((j['steps'] as List?) ?? const []).map((e) => AgentStep.fromJson(e as Map<String, dynamic>)).toList(),
        stepsSummary: j['steps_summary'] as String?,
        cards: ((j['cards'] as List?) ?? const []).map((e) => SessionCard.fromJson(e as Map<String, dynamic>)).whereType<SessionCard>().toList(),
      );
}

class AgentSessionDetail {
  AgentSessionDetail({required this.session, required this.messages});
  final AgentSession session;
  final List<AgentMessage> messages;

  factory AgentSessionDetail.fromJson(Map<String, dynamic> j) => AgentSessionDetail(
        session: AgentSession.fromJson(j),
        messages: ((j['messages'] as List?) ?? const []).map((e) => AgentMessage.fromJson(e as Map<String, dynamic>)).toList(),
      );
}

/// 가이드 준비 작업 (agent §12).
class GuideJob {
  GuideJob({required this.jobId, required this.status, required this.total, required this.finished, required this.ready, required this.failed, required this.current, required this.etaSec, required this.places});
  final String jobId;
  final String status; // running | done
  final int total;
  final int finished;
  final int ready;
  final int failed;
  final String? current;
  final int etaSec;
  final List<GuideJobPlace> places;

  bool get running => status == 'running';

  factory GuideJob.fromJson(Map<String, dynamic> j) => GuideJob(
        jobId: j['job_id'] as String,
        status: j['status'] as String? ?? 'done',
        total: j['total'] as int? ?? 0,
        finished: j['finished'] as int? ?? 0,
        ready: j['ready'] as int? ?? 0,
        failed: j['failed'] as int? ?? 0,
        current: j['current'] as String?,
        etaSec: j['eta_sec'] as int? ?? 0,
        places: ((j['places'] as List?) ?? const []).map((e) => GuideJobPlace.fromJson(e as Map<String, dynamic>)).toList(),
      );
}

class GuideJobPlace {
  GuideJobPlace({required this.placeId, required this.title, required this.status, required this.error, required this.sources});
  final String placeId;
  final String title;
  final String status; // pending | preparing | ready | failed
  final String error;
  final int sources;

  factory GuideJobPlace.fromJson(Map<String, dynamic> j) => GuideJobPlace(
        placeId: j['place_id'] as String,
        title: j['title'] as String? ?? '',
        status: j['status'] as String? ?? 'pending',
        error: j['error'] as String? ?? '',
        sources: j['sources'] as int? ?? 0,
      );
}
