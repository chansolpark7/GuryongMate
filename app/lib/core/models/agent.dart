// agent 서비스 응답 모델 (backend/app/agent/schemas.py).
import 'plan.dart';

class AgentSession {
  AgentSession({required this.id, required this.planId, required this.updatedAt});
  final String id;
  final int planId;
  final DateTime updatedAt;

  factory AgentSession.fromJson(Map<String, dynamic> j) =>
      AgentSession(id: j['id'] as String, planId: j['plan_id'] as int, updatedAt: DateTime.parse(j['updated_at'] as String));
}

class AgentMessage {
  AgentMessage({required this.role, required this.content, this.proposalId});
  final String role; // user | assistant
  final String content;
  final int? proposalId;

  factory AgentMessage.fromJson(Map<String, dynamic> j) =>
      AgentMessage(role: j['role'] as String, content: j['content'] as String? ?? '', proposalId: j['proposal_id'] as int?);
}

class AgentSessionDetail {
  AgentSessionDetail({required this.session, required this.messages, required this.pendingProposal});
  final AgentSession session;
  final List<AgentMessage> messages;
  final ProposalDetail? pendingProposal;

  factory AgentSessionDetail.fromJson(Map<String, dynamic> j) => AgentSessionDetail(
        session: AgentSession.fromJson(j),
        messages: ((j['messages'] as List?) ?? const []).map((e) => AgentMessage.fromJson(e as Map<String, dynamic>)).toList(),
        pendingProposal:
            j['pending_proposal'] == null ? null : ProposalDetail.fromJson(j['pending_proposal'] as Map<String, dynamic>),
      );
}
